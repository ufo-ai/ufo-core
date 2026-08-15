# Sandbox workspace, filesystem safety, and egress proxying  `stage-12`

This stage is shared behind-the-scenes support for any turn that runs tools. It gives each conversation a private workspace, like a locked workbench, and controls how that workspace touches files and the internet. The conversation module opens or reuses the right workspace, while select chooses the backend: a plain local folder, a Docker container, or an E2B cloud sandbox. The local, Docker, and E2B modules then run commands and move files in those places.

Session is the safe doorway tools use to execute commands and read or write files. Containment checks every untrusted path so tricks like “go up a folder” or symlinks cannot escape the allowed area. Exec_env gives commands fake-looking credential variables, so tools can ask for access without seeing real secrets.

For network access, rules builds the allowed outbound connections and decides when credentials may be brokered. The proxy server enforces those rules, injects secrets only at the gate, and records billable usage. Workspace_changes keeps a compact “what changed?” record using diffs. The package init files simply make these modules importable.

## Files in this stage

### Egress proxy gateway
The proxy server acts as the sandbox network gatekeeper, enforcing outbound access decisions, brokered secret injection, and billable usage recording.

### `core/src/ufo/sandbox/proxy/server.py`

`io_transport` · `request handling`

A sandboxed agent is not allowed to talk directly to the internet. Instead, its HTTP and HTTPS traffic goes through this proxy, like a guarded front desk for outgoing calls. The proxy reads a signed token from each connection to learn who is asking: a live agent turn, or a short-lived background probe. If the token is missing, expired, unknown, or the turn has ended, the proxy refuses the connection.

Once the caller is known, the proxy builds a rule set for that caller. These rules say which hosts are allowed, whether normal internet access is allowed, which credentials may be injected, and which requests must be forwarded through a server-side grant broker. By default, nothing is allowed. Exact approved hosts can be reached; broader internet access is allowed only for public IPv4 addresses after DNS is checked and pinned.

For plain allowed hosts, the proxy simply tunnels encrypted bytes and cannot see the request. For hosts that need a credential or model key, it performs controlled TLS interception: it presents a certificate trusted by the sandbox, reads the HTTP request, swaps a harmless sentinel value for the real secret, and opens its own secure connection upstream. This keeps real keys out of the sandbox. It also meters egress and parses model responses from OpenAI or Anthropic so sandbox-made model calls are billed correctly.

#### Function details

##### `_ContentDecoder.unconsumed_tail`  (lines 184–184)

```
def unconsumed_tail(self) -> bytes
```

**Purpose**: This protocol property describes the unread compressed bytes left inside a decompressor. It lets the token-usage parser work with different decompression objects through one small interface.

**Data flow**: A decompressor object is read through this property → it reports any bytes not yet consumed → the parser can continue decoding without losing data.

**Call relations**: HttpTokenUsage uses this protocol when it decodes compressed model responses. It is part of the contract used by HttpTokenUsage._decode rather than a standalone action.


##### `_ContentDecoder.decompress`  (lines 186–186)

```
def decompress(self, data: bytes, max_length: int=0) -> bytes
```

**Purpose**: This protocol method describes how compressed response bytes are turned into plain bytes. It includes a maximum output length so a bad or huge response cannot expand without limit.

**Data flow**: Compressed bytes and an optional size limit go in → the decoder expands what it safely can → plain response bytes come out, with any remainder left on the decoder.

**Call relations**: HttpTokenUsage._decode calls this through the protocol while reading model-provider responses. The protocol keeps the parser independent from one exact zlib class.


##### `_ContentDecoder.flush`  (lines 188–188)

```
def flush(self) -> bytes
```

**Purpose**: This protocol method describes how to finish a decompression stream and get any final plain bytes. It is used when the response body is complete.

**Data flow**: The decompressor's internal buffered state goes in → final pending bytes are emitted → the parser can make one last attempt to read usage data.

**Call relations**: HttpTokenUsage._finish_decoder calls this at the end of a response. It completes the parsing path started by HttpTokenUsage.feed.


##### `generate_ca`  (lines 194–217)

```
async def generate_ca() -> tuple[str, str]
```

**Purpose**: Creates a temporary certificate authority, meaning a root certificate and private key that the proxy can use to mint per-host certificates. Without this, the proxy could not safely inspect allowed HTTPS requests that need secret injection.

**Data flow**: No caller data goes in → a temporary directory is made and the openssl command-line tool creates a root certificate and key → the certificate text and key text are returned.

**Call relations**: Startup code uses this before constructing the proxy. It delegates the actual OpenSSL calls to _openssl, and EgressProxy._leaf_context later uses the generated CA to sign host-specific certificates.

*Call graph*: calls 1 internal fn (_openssl); 2 external calls (Path, TemporaryDirectory).


##### `_openssl`  (lines 220–226)

```
async def _openssl(*argv: str) -> None
```

**Purpose**: Runs the external openssl program and turns failures into Python errors. This keeps certificate creation in one place.

**Data flow**: OpenSSL command arguments go in → a subprocess is started and its error output is collected → the function returns nothing on success or raises an error with the OpenSSL message on failure.

**Call relations**: generate_ca, EgressProxy.start, and EgressProxy._leaf_context all call this whenever they need certificate or key files created.

*Call graph*: called by 3 (_leaf_context, start, generate_ca); 1 external calls (create_subprocess_exec).


##### `PerAgentRules.resolve`  (lines 265–296)

```
async def resolve(self, principal: EgressPrincipal | None) -> tuple[Rule, ...]
```

**Purpose**: Builds the exact network rule list for the principal making a request. It combines base model rules, internet policy, stored credentials, OAuth-style grants, and CLI forwarding rules for that one agent and workspace.

**Data flow**: A run token, probe token, or no token goes in → the function looks up the relevant agent and member, enters the correct workspace and agent scope, derives applicable rules, and removes model-key injection for probes → a tuple of rules comes out.

**Call relations**: EgressProxy receives this function as its rule resolver and calls it through EgressProxy._rules_for. It calls _turn_of for live turn identities, _conversation_of for probe identities, and _without_the_model_key for probes.

*Call graph*: calls 3 internal fn (_conversation_of, _turn_of, _without_the_model_key); 5 external calls (agent, derive_cli_rules, derive_credential_rules, derive_grant_rules, ws).


##### `PerAgentRules._turn_of`  (lines 298–322)

```
async def _turn_of(self, run: RunToken) -> _Authority | None
```

**Purpose**: Looks up which agent owns a run token's turn and whether that agent had internet access enabled. This turns a token into the authority needed to derive rules.

**Data flow**: A run token goes in → the database is queried for the turn and its agent inside the same workspace → an authority object comes out, or nothing if the turn is not found.

**Call relations**: PerAgentRules.resolve calls this for normal run tokens before deriving rules. Its result decides which agent scope and internet policy are used.

*Call graph*: called by 1 (resolve); 3 external calls (__init__, select, workspace_tx).


##### `PerAgentRules._conversation_of`  (lines 324–353)

```
async def _conversation_of(self, probe: ProbeToken) -> _Authority | None
```

**Purpose**: Looks up the agent and internet policy for a probe token, which names a conversation rather than a turn. This lets background or scheduled probe work inherit the conversation's agent rules.

**Data flow**: A probe token goes in → the database is queried for the conversation and its agent → an authority object comes out with the token's acting member, or nothing if no matching conversation exists.

**Call relations**: PerAgentRules.resolve calls this for probe tokens. The returned authority feeds the same rule-building path as a turn, except probes later lose model-key injection.

*Call graph*: called by 1 (resolve); 3 external calls (__init__, select, workspace_tx).


##### `PerAgentRules._without_the_model_key`  (lines 355–366)

```
def _without_the_model_key(self, rules: tuple[Rule, ...]) -> tuple[Rule, ...]
```

**Purpose**: Removes rules that would inject the deployment's model API key. This prevents unattended probes from spending the deployment's model budget through the proxy.

**Data flow**: A tuple of rules goes in → any injection rule containing the model-key sentinel is filtered out → a safer tuple of rules comes out.

**Call relations**: PerAgentRules.resolve calls this only for probe tokens, after deriving the normal rule set.

*Call graph*: called by 1 (resolve).


##### `PerAgentRules.turn_live`  (lines 368–385)

```
async def turn_live(self, run: RunToken) -> bool
```

**Purpose**: Checks whether a run token still names a turn whose database status is running. It is the fresh safety check that stops old tokens from continuing to draw credentials.

**Data flow**: A run token goes in → the turn status is read from the workspace database → true comes out only if the status is RUNNING.

**Call relations**: This is intended to be wired into EgressProxy as its turn authorizer. EgressProxy._turn_authorized calls that authorizer for every CONNECT request.

*Call graph*: 3 external calls (select, workspace_tx, ws).


##### `EgressProxy.probe_tokens`  (lines 414–418)

```
def probe_tokens(self) -> ProbeTokenCodec
```

**Purpose**: Builds the codec that verifies probe tokens using the same deployment secret as run tokens. This ensures both accepted token types belong to the same deployment trust boundary.

**Data flow**: The proxy's run-token codec is read → its secret is reused → a probe-token codec is returned.

**Call relations**: EgressProxy._principal uses this when a Proxy-Authorization header is not a valid run token and might be a probe token.

*Call graph*: 1 external calls (__init__).


##### `EgressProxy.start`  (lines 420–436)

```
async def start(self, bind_host: str=PROXY_BIND_HOST, port: int=0, public_url: str | None=None) -> ProxyEndpoint
```

**Purpose**: Starts the proxy server and prepares the temporary certificate files it needs. It returns the endpoint information that sandboxes use to connect to the proxy.

**Data flow**: Optional bind host, port, and public URL go in → a work directory is created, CA files and a reusable leaf key are written, and an asyncio TCP server is started → a ProxyEndpoint with port, CA certificate, and public URL comes out.

**Call relations**: Called during proxy startup. It uses _openssl for key generation and registers EgressProxy._handle as the function that will serve each accepted connection.

*Call graph*: calls 1 internal fn (_openssl); 4 external calls (__init__, start_server, Path, TemporaryDirectory).


##### `EgressProxy.stop`  (lines 438–475)

```
async def stop(self, graceful_shutdown_seconds: int=0) -> None
```

**Purpose**: Shuts the proxy down without hanging forever. It stops accepting new connections, gives existing ones a grace period, cancels unfinished work, drains the metering worker, and removes temporary files.

**Data flow**: A grace period goes in → the listener is closed, active connection and rule-resolution tasks are waited on or cancelled, pending meter records are flushed, and the work directory is cleaned up → the proxy is left stopped.

**Call relations**: Called during teardown. It coordinates with tasks created by _handle, _rules_for, and _enqueue_meter.

*Call graph*: 3 external calls (gather, wait, monotonic).


##### `EgressProxy._handle`  (lines 477–583)

```
async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None
```

**Purpose**: Serves one client connection from a sandbox. It validates that the request is a proxy CONNECT, authenticates the token, checks capacity limits and rules, then chooses tunneling or credential-aware inspection.

**Data flow**: A client reader and writer go in → the request header is read, the target host and token are parsed, authorization and rules are checked, DNS may be resolved for internet access, and traffic is handed to _tunnel or _mitm → the connection is closed and counters are decremented afterward.

**Call relations**: asyncio.start_server calls this for every connection. It is the central traffic dispatcher and calls _read_request_head, _principal, _authorized, _rules_for, _tunnel, _mitm, and _respond.

*Call graph*: calls 7 internal fn (_authorized, _mitm, _principal, _rules_for, _tunnel, _read_request_head, _respond); 3 external calls (__init__, close, current_task).


##### `EgressProxy._authorized`  (lines 585–595)

```
async def _authorized(self, principal: EgressPrincipal) -> bool
```

**Purpose**: Decides whether a principal is still allowed to make this specific connection. Run tokens must name a still-running turn; probe tokens must not be expired.

**Data flow**: A run or probe token goes in → probes are checked against the current time, while run tokens are passed to _turn_authorized → a true or false decision comes out.

**Call relations**: EgressProxy._handle calls this before rules are loaded. It calls _turn_authorized for run-token database checks.

*Call graph*: calls 1 internal fn (_turn_authorized); called by 1 (_handle); 1 external calls (now).


##### `EgressProxy._turn_authorized`  (lines 597–610)

```
async def _turn_authorized(self, run: RunToken) -> bool
```

**Purpose**: Runs the configured turn-liveness check and logs failures. It separates a genuine denial from an infrastructure problem that should become a service-unavailable response.

**Data flow**: A run token goes in → the configured authorizer is awaited → true or false comes out, or an exception is logged and re-raised.

**Call relations**: EgressProxy._authorized calls this for run tokens. EgressProxy._handle catches raised errors and answers with an authorization-unavailable response.

*Call graph*: called by 1 (_authorized); 1 external calls (log_error).


##### `EgressProxy._rules_for`  (lines 612–631)

```
async def _rules_for(self, principal: EgressPrincipal | None) -> tuple[Rule, ...]
```

**Purpose**: Gets the rule set for a principal, using a short-lived cache so repeated connections do not constantly re-query grants and credentials. It also shares one in-flight lookup among simultaneous connections for the same identity.

**Data flow**: A principal goes in → the cache is checked, expired entries are removed, and a shared resolution task may be created → the tuple of rules comes out or the resolution error is passed through.

**Call relations**: EgressProxy._handle calls this after authorization. It uses _rule_key to choose a cache key and _resolve_rules to do the real lookup.

*Call graph*: calls 2 internal fn (_resolve_rules, _rule_key); called by 1 (_handle); 3 external calls (create_task, shield, monotonic).


##### `EgressProxy._resolve_rules`  (lines 633–654)

```
async def _resolve_rules(self, key: _RuleKey, principal: EgressPrincipal) -> tuple[Rule, ...]
```

**Purpose**: Performs the actual rule resolution and stores the result in the cache. If rule resolution fails, it logs the problem and does not cache the failure.

**Data flow**: A cache key and principal go in → the configured resolver is awaited, errors are logged, and successful rules are cached with an expiry time → the rules are returned.

**Call relations**: EgressProxy._rules_for creates this as a task. _read_fault is attached so abandoned task exceptions are still observed.

*Call graph*: called by 1 (_rules_for); 4 external calls (__init__, current_task, monotonic, log_error).


##### `EgressProxy._principal`  (lines 656–669)

```
def _principal(self, proxy_auth: str) -> EgressPrincipal | None
```

**Purpose**: Extracts the signed identity from the Proxy-Authorization header. It accepts either a run token or a probe token, and treats forged or wrong-kind tokens as no identity.

**Data flow**: A header string goes in → the run-token codec tries to parse it, then the probe-token codec tries if needed → a principal object or None comes out.

**Call relations**: EgressProxy._handle calls this before authorization. It uses EgressProxy.probe_tokens for the probe-token parser.

*Call graph*: called by 1 (_handle).


##### `EgressProxy._resolve_public_address`  (lines 671–703)

```
async def _resolve_public_address(self, host: str, port: int) -> str
```

**Purpose**: Resolves an internet host to a public IPv4 address and refuses private, multicast, IPv6, malformed, or unresolvable destinations. This prevents sandboxes from using broad internet access to reach internal networks.

**Data flow**: A host and port go in → literal IPv4 is accepted or DNS A records are looked up → the first globally routable IPv4 address comes out, or an error refuses the destination.

**Call relations**: EgressProxy._handle uses this, unless a custom resolver was supplied, for hosts allowed only by InternetRule rather than exact ScopeRule.

*Call graph*: 1 external calls (IPv4Address).


##### `EgressProxy._tunnel`  (lines 705–733)

```
async def _tunnel(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int, principal: EgressPrincipal, rules: tuple[Rule, ...], connect_host: str) -> None
```

**Purpose**: Relays bytes to an allowed host without looking inside the encrypted connection. This is used when no credential injection or broker forwarding is needed.

**Data flow**: Client streams, host details, principal, rules, and resolved connect host go in → an upstream TCP connection is opened, a CONNECT success is sent, meters are recorded, and bytes are copied both ways → the tunnel ends when either side finishes or fails.

**Call relations**: EgressProxy._handle calls this for allowed hosts with no InjectionRule or ForwardRule. It uses _meter, _meter_ledger, _relay, and _respond.

*Call graph*: calls 4 internal fn (_meter, _meter_ledger, _relay, _respond); called by 1 (_handle); 4 external calls (drain, write, open_connection, wait_for).


##### `EgressProxy._mitm`  (lines 735–795)

```
async def _mitm(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int, injections: list[InjectionRule], forwards: list[ForwardRule], principal: EgressPrincipal, rules:
```

**Purpose**: Inspects an allowed HTTPS request when the proxy must inject a secret, forward through a grant broker, or count model tokens. It terminates TLS from the sandbox and opens its own verified TLS connection upstream.

**Data flow**: Client streams, host details, matching injection and forward rules, principal, and all rules go in → a trusted leaf certificate is prepared, the client's HTTPS request is read, a broker forward may be chosen, otherwise headers are rewritten and the request is sent upstream → response bytes are relayed and model usage may be parsed.

**Call relations**: EgressProxy._handle calls this for hosts that have injection or forwarding rules. It calls _leaf_context, _start_tls_server, _read_request_head, _forward_match, _forward_broker, _inject, _relay, and metering helpers.

*Call graph*: calls 11 internal fn (_forward_broker, _leaf_context, _meter, _meter_ledger, _meter_tokens, _forward_match, _inject, _read_request_head, _relay, _respond (+1 more)); called by 1 (_handle); 3 external calls (__init__, open_connection, wait_for).


##### `EgressProxy._forward_broker`  (lines 797–841)

```
async def _forward_broker(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, rule: ForwardRule, request: tuple[bytes, list[bytes]], host: str, principal: EgressPrincipal,
```

**Purpose**: Sends a sentinel-marked request through a grant broker instead of directly to the provider. This keeps the real account credential on the server side, never inside the sandbox or proxy header rewrite path.

**Data flow**: The client request, matching forward rule, host, principal, and rules go in → the bounded body is read, refusal cases are answered, metrics are recorded, headers are cleaned, the broker performs the provider call, and a reconstructed HTTP response is written back.

**Call relations**: EgressProxy._mitm calls this when _forward_match finds a grant sentinel. It uses _read_request_body, _drain_refused_body, _forward_headers, _forward_response_bytes, _meter, _meter_ledger, and _respond.

*Call graph*: calls 7 internal fn (_meter, _meter_ledger, _drain_refused_body, _forward_headers, _forward_response_bytes, _read_request_body, _respond); called by 1 (_mitm); 3 external calls (drain, write, log).


##### `EgressProxy._leaf_context`  (lines 843–900)

```
async def _leaf_context(self, host: str) -> ssl.SSLContext
```

**Purpose**: Creates or reuses a TLS server context for one hostname, signed by the proxy's temporary certificate authority. This lets the sandbox trust the proxy for allowed inspected HTTPS traffic.

**Data flow**: A host name goes in → a safe filename is derived, certificate extension and signing-request files are created, OpenSSL signs a leaf certificate, and an SSL context is loaded and cached → the context comes out.

**Call relations**: EgressProxy._mitm calls this before starting TLS with the sandbox. It uses _openssl and the containment helpers to avoid unsafe file paths.

*Call graph*: calls 1 internal fn (_openssl); called by 1 (_mitm); 4 external calls (Path, SSLContext, contained_file, contained_leaf).


##### `EgressProxy._meter`  (lines 902–905)

```
def _meter(self, host: str, rules: tuple[Rule, ...]) -> None
```

**Purpose**: Emits an in-process metric for a metered host access. This gives observability even before ledger rows are written.

**Data flow**: A host and rules go in → matching MeterRule entries are found → a sandbox_egress_total metric is emitted for each matching dimension.

**Call relations**: _tunnel, _mitm, and _forward_broker call this once a permitted exchange is actually underway.

*Call graph*: called by 3 (_forward_broker, _mitm, _tunnel); 1 external calls (emit_metric).


##### `EgressProxy._meter_ledger`  (lines 907–916)

```
async def _meter_ledger(self, host: str, principal: EgressPrincipal, rules: tuple[Rule, ...]) -> None
```

**Purpose**: Queues a billable egress-request record when the rules say this host should be counted. Token-only meters are excluded because they are billed separately.

**Data flow**: A host, principal, and rules go in → the function checks for non-token meter rules, determines the turn ID or None for probes, and enqueues an egress meter record → no immediate database write happens.

**Call relations**: _tunnel, _mitm, and _forward_broker call this after admitting traffic. It passes work to _enqueue_meter so request handling is not blocked by ledger writes.

*Call graph*: calls 1 internal fn (_enqueue_meter); called by 3 (_forward_broker, _mitm, _tunnel); 1 external calls (__init__).


##### `EgressProxy._meter_tokens`  (lines 918–940)

```
async def _meter_tokens(self, principal: EgressPrincipal, accumulator: 'HttpTokenUsage') -> None
```

**Purpose**: Turns parsed model-provider usage into a billable token record for a sandbox model call. It refuses to bill probes for model usage because probes should not receive the deployment model key.

**Data flow**: A principal and HttpTokenUsage accumulator go in → probe usage is warned and ignored, absent usage is logged, and valid run-token usage is wrapped in a token meter record → the record is queued for later database writing.

**Call relations**: EgressProxy._mitm calls this after relaying a metered model response through HttpTokenUsage. It sends records to _enqueue_meter.

*Call graph*: calls 1 internal fn (_enqueue_meter); called by 1 (_mitm); 3 external calls (__init__, log, warn).


##### `EgressProxy._enqueue_meter`  (lines 942–949)

```
async def _enqueue_meter(self, record: _MeterRecord) -> None
```

**Purpose**: Adds one metering record to the background queue, starting the worker if needed. This keeps network relaying fast by moving database writes off the main request path.

**Data flow**: An egress or token meter record goes in → the meter worker is created or checked, then the record is put on the queue → the caller resumes once the queue accepts it.

**Call relations**: _meter_ledger and _meter_tokens call this. It starts _meter_loop as the consumer.

*Call graph*: calls 1 internal fn (_meter_loop); called by 2 (_meter_ledger, _meter_tokens); 1 external calls (create_task).


##### `EgressProxy._meter_loop`  (lines 951–983)

```
async def _meter_loop(self) -> None
```

**Purpose**: Runs in the background and writes meter records in small batches. Batching reduces database overhead during busy proxy traffic.

**Data flow**: Records are read from the queue → the loop waits a tiny window to collect more, writes a batch, marks queue items done, and stops when it receives a None sentinel → failed batches are logged.

**Call relations**: _enqueue_meter starts this worker. It calls _write_meter_batch for the actual database work and is stopped by EgressProxy.stop.

*Call graph*: calls 1 internal fn (_write_meter_batch); called by 1 (_enqueue_meter); 2 external calls (sleep, log_error).


##### `EgressProxy._write_meter_batch`  (lines 985–1027)

```
async def _write_meter_batch(self, records: list[_MeterRecord]) -> None
```

**Purpose**: Combines queued meter records by billed workspace and turn before writing them. This turns many small observations into fewer accounting updates.

**Data flow**: A list of meter records goes in → egress counts and token usage are summed per workspace/turn and model → each billed group is written inside the correct workspace transaction.

**Call relations**: _meter_loop calls this. It calls _write_billed once per billed identity and logs per-identity write failures.

*Call graph*: calls 1 internal fn (_write_billed); called by 1 (_meter_loop); 4 external calls (__init__, workspace_tx, log_error, ws).


##### `EgressProxy._write_billed`  (lines 1029–1049)

```
async def _write_billed(self, connection: AsyncConnection, workspace_id: UUID, turn_id: UUID | None, requests: int | None, tokens: dict[str, Usage]) -> None
```

**Purpose**: Writes one workspace-and-turn billing group to the accounting tables. It separates probe egress, turn egress, and sandbox model-token charges.

**Data flow**: A database connection, workspace ID, optional turn ID, request count, and token usage map go in → probe requests are written without a turn, turn requests are written with a turn, and each model usage is priced and recorded → accounting rows are updated.

**Call relations**: _write_meter_batch calls this inside a workspace transaction. It delegates final accounting to record_probe_egress_request, record_egress_request, and record_sandbox_tokens.

*Call graph*: called by 1 (_write_meter_batch); 3 external calls (record_egress_request, record_probe_egress_request, record_sandbox_tokens).


##### `_rule_key`  (lines 1052–1064)

```
def _rule_key(principal: EgressPrincipal) -> _RuleKey
```

**Purpose**: Chooses the cache key used for a principal's rule set. Run tokens key by the token itself, while probe tokens key by the stable details that actually affect their rules.

**Data flow**: A run or probe token goes in → run tokens are returned as-is; probe tokens are reduced to workspace, conversation, and acting member → the cache key comes out.

**Call relations**: EgressProxy._rules_for calls this before checking the rule cache. It prevents one-use probe tokens from flooding the cache.

*Call graph*: called by 1 (_rules_for); 1 external calls (__init__).


##### `_read_fault`  (lines 1067–1073)

```
def _read_fault(task: asyncio.Task[tuple[Rule, ...]]) -> None
```

**Purpose**: Consumes the exception from a rule-resolution task if all waiters disappeared. This avoids noisy asyncio logging that could include poorly structured error text.

**Data flow**: A completed task goes in → if it was not cancelled, its exception is read → nothing is returned, but the task is marked observed.

**Call relations**: EgressProxy._rules_for attaches this as a done callback to tasks created for EgressProxy._resolve_rules.


##### `_start_tls_server`  (lines 1076–1092)

```
async def _start_tls_server(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, context: ssl.SSLContext) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]
```

**Purpose**: Turns an accepted CONNECT connection into a TLS server connection from the sandbox's point of view. This is the start of controlled HTTPS inspection.

**Data flow**: Plain client streams and an SSL context go in → a CONNECT success is written, reading is paused so TLS bytes stay available, the transport is upgraded to TLS, and the same reader/writer are returned for decrypted traffic.

**Call relations**: EgressProxy._mitm calls this after preparing a leaf certificate. It bridges the plain proxy CONNECT phase and the inspected HTTPS phase.

*Call graph*: called by 1 (_mitm); 3 external calls (drain, write, get_running_loop).


##### `_read_request_head`  (lines 1095–1128)

```
async def _read_request_head(reader: asyncio.StreamReader) -> tuple[bytes, list[bytes]] | _HeaderRefusal | None
```

**Purpose**: Reads an HTTP request line and headers with a timeout and size limit. This protects the proxy from clients that send headers too slowly or too large.

**Data flow**: A stream reader goes in → one request line and header lines are read until the blank line, timeout, EOF, or size limit → a request tuple, refusal object, or None comes out.

**Call relations**: EgressProxy._handle uses this for the initial CONNECT request, and EgressProxy._mitm uses it for the HTTPS request after TLS starts.

*Call graph*: called by 2 (_handle, _mitm); 3 external calls (__init__, readline, timeout).


##### `_forward_match`  (lines 1131–1145)

```
def _forward_match(headers: list[bytes], candidates: list[ForwardRule]) -> ForwardRule | None
```

**Purpose**: Finds the forwarding rule whose sentinel appears in the request headers. A sentinel is a harmless placeholder value that identifies which account grant should be used.

**Data flow**: Request header lines and candidate forward rules go in → header names and one- or two-part auth values are compared to each rule's exact sentinel → the matching rule or None comes out.

**Call relations**: EgressProxy._mitm calls this before deciding whether to use _forward_broker or send the request directly upstream with header injection.

*Call graph*: called by 1 (_mitm).


##### `_read_request_body`  (lines 1162–1215)

```
async def _read_request_body(reader: asyncio.StreamReader, headers: list[bytes]) -> bytes | _Refusal
```

**Purpose**: Reads the complete body for a broker-forwarded request, but only when it has a safe content length and fits the limit. The broker interface expects one bounded request, not a streaming upload.

**Data flow**: A stream reader and headers go in → content-length and transfer-encoding are inspected, invalid or too-large cases become a refusal, otherwise exactly that many bytes are read → body bytes or a refusal come out.

**Call relations**: EgressProxy._forward_broker calls this before invoking the grant broker. Refusals are later paired with _drain_refused_body.

*Call graph*: called by 1 (_forward_broker); 2 external calls (__init__, readexactly).


##### `_drain_refused_body`  (lines 1218–1235)

```
async def _drain_refused_body(reader: asyncio.StreamReader, pending: int) -> None
```

**Purpose**: Discards body bytes from a client after the proxy has already decided to refuse the request. This gives the client a better chance to finish writing and read the clear refusal response.

**Data flow**: A reader and maximum pending byte count go in → bytes are read and thrown away until the cap, timeout, or EOF → nothing is returned.

**Call relations**: EgressProxy._forward_broker calls this after sending a refusal from _read_request_body.

*Call graph*: called by 1 (_forward_broker); 2 external calls (read, timeout).


##### `_forward_headers`  (lines 1238–1250)

```
def _forward_headers(headers: list[bytes], rule: ForwardRule) -> dict[str, str]
```

**Purpose**: Builds the headers that are safe to send to the grant broker. It removes the sentinel credential header and connection-specific headers that the broker or its HTTP client should recreate.

**Data flow**: Original header lines and the matching forward rule go in → dropped headers are filtered out and remaining names and values are decoded → a plain dictionary of forwarded headers comes out.

**Call relations**: EgressProxy._forward_broker calls this when constructing the broker request.

*Call graph*: called by 1 (_forward_broker).


##### `_forward_response_bytes`  (lines 1253–1271)

```
def _forward_response_bytes(response: ForwardedResponse) -> bytes
```

**Purpose**: Turns a broker response object back into a simple HTTP/1.1 response for the sandbox. It also removes unsafe or conflicting headers.

**Data flow**: A ForwardedResponse goes in → status, reason phrase, safe headers, measured content length, connection close, and body are assembled → raw HTTP response bytes come out.

**Call relations**: EgressProxy._forward_broker writes these bytes back to the client. It uses _has_crlf to prevent header injection.

*Call graph*: calls 1 internal fn (_has_crlf); called by 1 (_forward_broker); 1 external calls (HTTPStatus).


##### `_has_crlf`  (lines 1274–1275)

```
def _has_crlf(value: str) -> bool
```

**Purpose**: Checks whether a string contains carriage-return or newline characters. In HTTP headers, those characters could smuggle extra headers or split a response.

**Data flow**: A string goes in → it is scanned for '\r' or '\n' → true or false comes out.

**Call relations**: _forward_response_bytes calls this while filtering broker-provided headers.

*Call graph*: called by 1 (_forward_response_bytes).


##### `_inject`  (lines 1278–1303)

```
def _inject(headers: list[bytes], candidates: list[InjectionRule]) -> bytes
```

**Purpose**: Rebuilds request headers while replacing exact sentinel values with real secrets for allowed injection rules. It forces the connection to close so each request is separately checked and rewritten.

**Data flow**: Original header lines and candidate injection rules go in → connection headers are removed, matching sentinel headers are replaced with real credential values, and other headers are preserved → a new header block comes out.

**Call relations**: EgressProxy._mitm calls this before sending a credential-injected request upstream.

*Call graph*: called by 1 (_mitm).


##### `_relay`  (lines 1306–1340)

```
async def _relay(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, upstream_reader: asyncio.StreamReader, upstream_writer: asyncio.StreamWriter, on_downstream: Callable[[bytes]
```

**Purpose**: Copies bytes between the sandbox and upstream server in both directions. It includes special care to keep receiving a response after the client has finished sending its request.

**Data flow**: Client and upstream streams plus an optional downstream observer go in → two pump tasks copy bytes both ways, downstream progress is watched with an idle timeout, and the upstream writer is closed at the end → traffic is relayed until completion or timeout.

**Call relations**: EgressProxy._tunnel uses this for opaque tunnels, and EgressProxy._mitm uses it for inspected upstream exchanges. It creates _pump tasks for each direction.

*Call graph*: calls 1 internal fn (_pump); called by 2 (_mitm, _tunnel); 7 external calls (Event, can_write_eof, close, write_eof, create_task, timeout, wait).


##### `_pump`  (lines 1343–1358)

```
async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, on_chunk: Callable[[bytes], None] | None=None, on_progress: Callable[[], None] | None=None) -> None
```

**Purpose**: Copies chunks from one stream reader to one stream writer. It is the small worker used by the bidirectional relay.

**Data flow**: A reader, writer, and optional callbacks go in → chunks are read, written, drained, and reported to callbacks → the function exits on EOF, cancellation, or socket errors.

**Call relations**: _relay starts this for upstream-to-client and client-to-upstream copying.

*Call graph*: called by 1 (_relay); 3 external calls (read, drain, write).


##### `_int_field`  (lines 1361–1363)

```
def _int_field(usage: dict[str, object], name: str) -> int
```

**Purpose**: Safely reads an integer field from a parsed JSON object. It treats missing values, booleans, and non-integers as zero.

**Data flow**: A usage dictionary and field name go in → the value is checked for being a real integer → that integer or 0 comes out.

**Call relations**: HttpTokenUsage._absorb_anthropic and HttpTokenUsage._openai use this to parse provider token counts. _cached_field also uses it for nested cached-token details.

*Call graph*: called by 3 (_absorb_anthropic, _openai, _cached_field).


##### `_cached_field`  (lines 1366–1368)

```
def _cached_field(usage: dict[str, object], details_name: str) -> int
```

**Purpose**: Safely reads a nested cached-token count from provider usage details. It hides provider shape differences behind a small helper.

**Data flow**: A usage dictionary and details-field name go in → the nested details object is checked, then its cached_tokens integer is read → the cached-token count or 0 comes out.

**Call relations**: HttpTokenUsage._openai calls this when parsing OpenAI usage blocks.

*Call graph*: calls 1 internal fn (_int_field); called by 1 (_openai).


##### `HttpTokenUsage.feed`  (lines 1395–1432)

```
def feed(self, chunk: bytes) -> None
```

**Purpose**: Feeds raw HTTP response bytes into the model-usage parser. It first reads response headers, then configures chunked decoding and decompression before passing body bytes onward.

**Data flow**: A response byte chunk goes in → headers are accumulated until complete, transfer and content encoding are recognized, gzip or deflate decoding may be prepared, and body bytes are passed to _feed_wire_body → internal token-usage state may be updated.

**Call relations**: EgressProxy._mitm passes this as the downstream observer to _relay for model-host responses. It calls _feed_wire_body and _fail as needed.

*Call graph*: calls 2 internal fn (_fail, _feed_wire_body); 1 external calls (decompressobj).


##### `HttpTokenUsage.usage`  (lines 1434–1446)

```
def usage(self) -> tuple[str, Usage] | None
```

**Purpose**: Returns the parsed model name and token usage, if the response contained recognizable usage data. It finalizes decompression and tries both streaming-event and plain-JSON response shapes.

**Data flow**: The parser's accumulated internal state is read → any decompressor is flushed, remaining JSON body is checked if no event was seen, and token counts are packaged → a model and Usage object, or None, comes out.

**Call relations**: EgressProxy._meter_tokens calls this after relay finishes. It uses _finish_decoder and _maybe_json_body.

*Call graph*: calls 2 internal fn (_finish_decoder, _maybe_json_body); 1 external calls (__init__).


##### `HttpTokenUsage._feed_wire_body`  (lines 1448–1494)

```
def _feed_wire_body(self, chunk: bytes) -> None
```

**Purpose**: Converts the wire-level response body into actual payload bytes. It understands normal bodies and HTTP chunked transfer coding.

**Data flow**: Wire body bytes go in → chunk sizes and chunk boundaries are processed if needed, payload bytes are extracted, and the decompression step receives them → parsing continues or failure state is set.

**Call relations**: HttpTokenUsage.feed calls this after headers are complete. It calls _decode, _finish_decoder, and _fail.

*Call graph*: calls 3 internal fn (_decode, _fail, _finish_decoder); called by 1 (feed).


##### `HttpTokenUsage._decode`  (lines 1496–1512)

```
def _decode(self, chunk: bytes) -> None
```

**Purpose**: Applies gzip or deflate decompression when the response is compressed. Uncompressed bytes pass straight through.

**Data flow**: Payload bytes go in → if there is no decompressor they are sent to _feed_body; otherwise they are decompressed in bounded pieces and then sent to _feed_body → parser state advances or fails on bad compression.

**Call relations**: HttpTokenUsage._feed_wire_body calls this for each payload piece.

*Call graph*: calls 2 internal fn (_fail, _feed_body); called by 1 (_feed_wire_body).


##### `HttpTokenUsage._finish_decoder`  (lines 1514–1523)

```
def _finish_decoder(self) -> None
```

**Purpose**: Finishes any active decompressor exactly once. This captures final bytes that may only appear when a compressed stream ends.

**Data flow**: Internal decompressor state is read → flush output is sent to _feed_body, or decompression errors mark failure → no value is returned.

**Call relations**: HttpTokenUsage.usage calls this before returning results, and _feed_wire_body calls it when a chunked body reaches its final chunk.

*Call graph*: calls 2 internal fn (_fail, _feed_body); called by 2 (_feed_wire_body, usage).


##### `HttpTokenUsage._feed_body`  (lines 1525–1536)

```
def _feed_body(self, chunk: bytes) -> None
```

**Purpose**: Accumulates decoded body text and processes it line by line. It keeps the buffer bounded so a huge response cannot consume unlimited memory.

**Data flow**: Decoded body bytes go in → complete newline-terminated lines are sent to _consume, consumed bytes are removed, and oversized leftovers trigger failure → internal usage fields may be updated.

**Call relations**: HttpTokenUsage._decode and _finish_decoder call this after decompression.

*Call graph*: calls 2 internal fn (_consume, _fail); called by 2 (_decode, _finish_decoder).


##### `HttpTokenUsage._consume`  (lines 1538–1555)

```
def _consume(self, line: bytes) -> None
```

**Purpose**: Examines one decoded response line for model usage data. It understands server-sent events, which are streamed lines beginning with data:, and also gives plain JSON lines a chance.

**Data flow**: One line of bytes goes in → non-event JSON is tried, event JSON is parsed when present, and provider-specific parsing is chosen based on host → internal usage fields may be updated.

**Call relations**: HttpTokenUsage._feed_body calls this for each complete line. It delegates to _anthropic, _openai, or _maybe_json_body.

*Call graph*: calls 3 internal fn (_anthropic, _maybe_json_body, _openai); called by 1 (_feed_body); 1 external calls (loads).


##### `HttpTokenUsage._maybe_json_body`  (lines 1557–1572)

```
def _maybe_json_body(self, payload: bytes) -> None
```

**Purpose**: Tries to parse a complete plain JSON response body as a usage-bearing model response. This covers non-streaming model calls.

**Data flow**: A possible JSON payload goes in → it is parsed if it starts like an object, provider-specific fields are read, and usage counters may be updated → nothing is returned.

**Call relations**: HttpTokenUsage._consume calls this for non-SSE lines, and HttpTokenUsage.usage calls it at the end for any remaining buffered body.

*Call graph*: calls 2 internal fn (_absorb_anthropic, _openai); called by 2 (_consume, usage); 1 external calls (loads).


##### `HttpTokenUsage._anthropic`  (lines 1574–1584)

```
def _anthropic(self, event: dict[str, object]) -> None
```

**Purpose**: Parses Anthropic streaming events for model name and token counts. Anthropic reports initial input details and later output deltas in different event types.

**Data flow**: A parsed Anthropic event dictionary goes in → message_start updates model and input/cache counts, message_delta updates output counts → internal usage fields are updated.

**Call relations**: HttpTokenUsage._consume calls this for Anthropic-host server-sent events. It delegates the actual count reading to _absorb_anthropic.

*Call graph*: calls 1 internal fn (_absorb_anthropic); called by 1 (_consume).


##### `HttpTokenUsage._absorb_anthropic`  (lines 1586–1601)

```
def _absorb_anthropic(self, usage: object, initial: bool) -> None
```

**Purpose**: Reads Anthropic usage fields into the parser's normalized Usage counters. It handles both cache-read and cache-write token fields.

**Data flow**: An Anthropic usage object and an initial-or-delta flag go in → input and cache counts are read on initial events, output count is read when present, and the parser is marked as having seen usage → internal counters change.

**Call relations**: HttpTokenUsage._anthropic and _maybe_json_body call this for streaming and non-streaming Anthropic responses.

*Call graph*: calls 1 internal fn (_int_field); called by 2 (_anthropic, _maybe_json_body).


##### `HttpTokenUsage._openai`  (lines 1603–1627)

```
def _openai(self, event: dict[str, object]) -> None
```

**Purpose**: Parses OpenAI usage blocks from either Chat Completions or Responses API shapes. It normalizes their different field names into the same counters.

**Data flow**: A parsed OpenAI event dictionary goes in → model is read if present, usage is inspected for prompt/completion or input/output fields, cached-token details are read, and normalized values are passed onward → internal counters may change.

**Call relations**: HttpTokenUsage._consume and _maybe_json_body call this for OpenAI-host responses. It uses _int_field, _cached_field, and _absorb_openai.

*Call graph*: calls 3 internal fn (_absorb_openai, _cached_field, _int_field); called by 2 (_consume, _maybe_json_body); 1 external calls (log).


##### `HttpTokenUsage._absorb_openai`  (lines 1629–1644)

```
def _absorb_openai(self, prompt: int, output: int, cached: int) -> None
```

**Purpose**: Stores normalized OpenAI token counts, separating cached prompt tokens from fresh input tokens. This matches how host-side model billing prices cached input.

**Data flow**: Prompt/input count, output count, and cached count go in → cached is clamped if it exceeds prompt, fresh input is computed as prompt minus cached, and counters are stored → the parser is marked as having seen usage.

**Call relations**: HttpTokenUsage._openai calls this after recognizing a supported OpenAI usage shape.

*Call graph*: called by 1 (_openai); 1 external calls (log).


##### `HttpTokenUsage._fail`  (lines 1646–1650)

```
def _fail(self) -> None
```

**Purpose**: Marks token-usage parsing as failed and clears buffered data. This avoids returning partial or unsafe results after malformed headers, chunks, compression, or oversized bodies.

**Data flow**: No external input goes in → the overflow/failure flag is set and accumulated header, body, and chunk buffers are cleared → later feed calls ignore data and usage returns no parsed result.

**Call relations**: HttpTokenUsage.feed, _feed_wire_body, _decode, _finish_decoder, and _feed_body call this whenever parsing can no longer be trusted.

*Call graph*: called by 5 (_decode, _feed_body, _feed_wire_body, _finish_decoder, feed).


##### `_respond`  (lines 1653–1671)

```
async def _respond(writer: asyncio.StreamWriter, status: int, message: str) -> None
```

**Purpose**: Writes a clear HTTP refusal or error response to a client. It includes the proxy's message in the body so callers can understand why the connection was denied.

**Data flow**: A writer, status code, and message go in → an HTTP/1.1 response with content length and connection close is written and drained → the function returns, ignoring routine disconnect errors.

**Call relations**: EgressProxy._handle, _tunnel, _mitm, and _forward_broker call this whenever a request must be rejected or an upstream/broker failure must be reported.

*Call graph*: called by 4 (_forward_broker, _handle, _mitm, _tunnel); 3 external calls (drain, write, HTTPStatus).


### Conversation sandboxes
These files open or reuse a conversation workspace across local, Docker, and E2B sandbox backends.

### `core/src/ufo/sandbox/conversation.py`

`domain_logic` · `workspace access during turn startup, off-turn file writes, file browsing, and reads`

A conversation’s `/workspace` is treated like the only real copy of that conversation’s files. This file protects that idea. When a turn starts, or when something outside a turn needs to write an attachment or read files, `ConversationSandbox` either opens the already-known sandbox or creates one and records its durable handle in the database.

The important rule is: reads do not create workspaces. If a conversation has never had a sandbox, browsing or reading files simply returns nothing. This avoids a surprising side effect where a harmless “show me files” request would create storage.

The class also decides where the workspace lives. It may live in the project’s normal sandbox carrier, or it may be bound to a user-connected terminal. Once a conversation is bound, later opens must respect that binding so the user and the agent keep seeing the same files.

There is careful race protection. Two things can try to create the first sandbox at once, like an attachment arriving while a turn starts. The code uses a database compare-and-swap, meaning “write this handle only if the old value is still what I saw.” The loser adopts the winner’s sandbox, so the database names one workspace and nothing important is stranded.

#### Function details

##### `ConversationSandbox.open`  (lines 93–128)

```
async def open(self, conversation_id: UUID, run_token: str, env: Mapping[str, str]) -> SandboxSession
```

**Purpose**: Opens the sandbox for a conversation, creating it if needed, and makes sure the database records the sandbox handle that future processes should use. This is the main write-capable entry point for code that needs a real workspace.

**Data flow**: It receives a conversation id, a run token, and environment variables. It reads the current stored sandbox binding and the agent’s sandbox size, asks `_opened` to create or resume a sandbox, then tries to save the resulting handle. If another opener saved a handle first, it retries using the winner’s handle. It returns a `SandboxSession`, which is the usable connection to the chosen sandbox.

**Call relations**: The turn queue calls this when it needs a sandbox, and `write` also calls it before copying in content. Inside, it relies on `_binding` for the database state, `_opened` for the actual carrier choice and sandbox open, and `_claim` to settle races safely.

*Call graph*: calls 3 internal fn (_binding, _claim, _opened); called by 2 (_open_sandbox, write); 1 external calls (__init__).


##### `ConversationSandbox.existing`  (lines 130–173)

```
async def existing(self, conversation_id: UUID) -> SandboxSession | None
```

**Purpose**: Tries to attach to a conversation’s already-existing sandbox without creating anything new. This is the safe read-side path used for browsing, pruning, and reading files.

**Data flow**: It takes a conversation id and reads the stored sandbox handle. If there is no handle, or the handle belongs to another unavailable backend, it returns `None`. If the handle points to a connected terminal or the configured backend can still attach, it builds a sandbox specification with an unsigned off-turn token and returns a `SandboxSession`.

**Call relations**: `entries`, `prune`, and `read` call this so they do not accidentally provision storage. It uses `_stored` to check the database, `sandbox_handle_id` to tell which backend owns the saved handle, and then asks either the terminal carrier or the configured carrier to attach.

*Call graph*: calls 1 internal fn (_stored); called by 3 (entries, prune, read); 5 external calls (__init__, __init__, __init__, to_thread, sandbox_handle_id).


##### `ConversationSandbox.claim_terminal`  (lines 175–185)

```
async def claim_terminal(self, conversation_id: UUID, cwd: str) -> bool
```

**Purpose**: Binds a conversation that has no sandbox yet to the currently connected terminal directory. This lets the first later sandbox open use the user’s terminal workspace rather than a separate server-side one.

**Data flow**: It receives a conversation id and a terminal current working directory. It builds a `client:` handle from that directory, checks whether the conversation already has a stored handle, and if not tries to write this terminal handle. It returns `True` only if this call actually made the claim.

**Call relations**: This function is used when a live terminal connection wants to reserve a conversation’s workspace location. It reads through `_stored` and writes through `_claim`, so it follows the same race-safe database rule as normal sandbox opening.

*Call graph*: calls 2 internal fn (_claim, _stored).


##### `ConversationSandbox.write`  (lines 187–198)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Copies bytes into a file inside the conversation’s workspace and returns the `/workspace/...` path the agent can use. It is used for off-turn writes such as landing an inbound attachment.

**Data flow**: It receives a conversation id, a relative file path, and raw bytes. It first rejects content over the configured maximum size, then opens the sandbox with an unsigned off-turn token, writes the file through the session, and returns the container-visible workspace path.

**Call relations**: This is a convenience path built on top of `open`, because writing may need to create the workspace. After `open` returns a session, this function hands the file content to that session and uses `workspace_path` to report where the agent will see it.

*Call graph*: calls 1 internal fn (open); 1 external calls (workspace_path).


##### `ConversationSandbox.prune`  (lines 200–211)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int) -> None
```

**Purpose**: Deletes older files under a workspace subdirectory, keeping only the newest requested number. This is a safety valve for unattended off-turn writers that append files over time.

**Data flow**: It receives a conversation id, a relative directory prefix, and a keep count. It attaches only if a sandbox already exists, then runs a small Python pruning program inside the sandbox so file age and paths are judged from the workspace’s own view. If the program reports failure, it raises an operating-system style error.

**Call relations**: It calls `existing` because pruning must not create a workspace just to delete from it. Once attached, it delegates the actual deletion to the sandbox session using the embedded `PRUNE_PROG` script and a workspace path made with `workspace_path`.

*Call graph*: calls 1 internal fn (existing); 1 external calls (workspace_path).


##### `ConversationSandbox.entries`  (lines 213–252)

```
async def entries(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: Returns the files a user should see in the conversation’s workspace, sorted by path. It is the file-browser listing path.

**Data flow**: It receives a conversation id and attaches only to an existing sandbox. It asks the sandbox filesystem tool to find files under `/workspace`, excluding names like `.git`, then turns each result into a `WorkspaceFile` with a relative path, byte size, and modified time. If the tool says the list was cut short, it logs a warning.

**Call relations**: This function depends on `existing` to avoid creating a workspace during browsing. It calls `_workspace_rel` to convert sandbox or host-style absolute paths into user-facing relative paths, and it packages each result as a `WorkspaceFile` for callers.

*Call graph*: calls 2 internal fn (_workspace_rel, existing); 3 external calls (__init__, fromtimestamp, warn).


##### `ConversationSandbox._workspace_rel`  (lines 254–258)

```
def _workspace_rel(self, handle: SandboxHandle, path: str) -> str
```

**Purpose**: Turns an absolute path reported by a workspace walk into a path relative to the workspace root. This keeps file listings from exposing container paths or host paths to the user.

**Data flow**: It receives the sandbox handle and a path string. It checks whether the path starts under `/workspace` or under the handle’s host workspace path, removes that root prefix, and returns the remaining relative path. If the path is outside both known roots, it raises an error.

**Call relations**: `entries` uses this while building the visible file list. It is the small guard that makes sure a filesystem listing cannot quietly report something outside the workspace as if it were safe.

*Call graph*: called by 1 (entries).


##### `ConversationSandbox.read`  (lines 260–268)

```
async def read(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: Returns a stream of bytes for one workspace file, or `None` if there is no existing sandbox or no such file. It is the safe read path for downloading or viewing a file.

**Data flow**: It receives a conversation id and a relative file path. It attaches only to an existing sandbox, checks whether the file exists, and if it does, returns an async byte iterator that reads the file in chunks. It does not create storage and does not load the whole file at once.

**Call relations**: Like `entries` and `prune`, this function starts with `existing` so a read request stays side-effect-free. After that, the sandbox session performs the file existence check and supplies the read stream.

*Call graph*: calls 1 internal fn (existing).


##### `ConversationSandbox._opened`  (lines 270–323)

```
async def _opened(self, conversation_id: UUID, stored: str | None, run_token: str, env: Mapping[str, str], size: str) -> tuple[str, Carrier, SandboxHandle]
```

**Purpose**: Chooses where a sandbox open should happen and performs the actual create-or-resume operation. This is the central decision point for terminal-backed versus normal backend workspaces.

**Data flow**: It receives the conversation id, any stored handle, the run token, environment variables, and the sandbox size. It checks whether the conversation is already bound to a terminal, or should bind to a currently held terminal, and creates a terminal-backed sandbox if so. Otherwise it prepares a server-side workspace directory, optionally changes ownership for the sandbox user, and asks the configured carrier to create or resume the sandbox. It returns the backend name, the carrier, and the sandbox handle.

**Call relations**: `open` calls this during each open attempt. `_opened` builds `SandboxSpec` objects for either `TerminalCarrier` or the configured carrier, uses `sandbox_handle_id` to interpret stored handles, and runs filesystem preparation work off the event loop when needed.

*Call graph*: called by 1 (open); 5 external calls (__init__, __init__, to_thread, geteuid, sandbox_handle_id).


##### `ConversationSandbox._provisioned_dir`  (lines 325–340)

```
def _provisioned_dir(self, conversation_id: UUID) -> Path
```

**Purpose**: Creates and verifies the host directory used as a conversation’s workspace for the normal in-cluster carrier. It exists to prevent unsafe path tricks such as symlinks redirecting the sandbox outside the allowed root.

**Data flow**: It receives a conversation id. It ensures the configured workspace root exists, resolves that configured root safely, then creates or opens the conversation-specific directory under it using containment checks. It returns the verified directory path.

**Call relations**: This helper is part of the server-side sandbox opening path. The opening flow needs a real directory before it asks the carrier to mount or serve the workspace, and this function supplies one only after the containment helpers have approved it.

*Call graph*: 3 external calls (suppress, configured_root, contained_dir).


##### `ConversationSandbox._existing_dir`  (lines 342–353)

```
def _existing_dir(self, conversation_id: UUID) -> Path | None
```

**Purpose**: Finds an already-created workspace directory without creating it. It is the filesystem counterpart to the rule that read operations must not provision new workspaces.

**Data flow**: It receives a conversation id. It resolves the configured workspace root and checks for the conversation directory under it. If the directory is missing, it returns `None`; if the path is unsafe, the containment code raises; otherwise it returns the verified path.

**Call relations**: `existing` uses this when attaching to a normal backend workspace. That lets browsing and reading reach a real directory if one exists, while leaving never-created conversations untouched.

*Call graph*: 2 external calls (configured_root, contained_dir).


##### `ConversationSandbox._stored`  (lines 355–357)

```
async def _stored(self, conversation_id: UUID) -> str | None
```

**Purpose**: Fetches only the stored sandbox handle for a conversation. It is a small wrapper for callers that do not need the agent’s sandbox size.

**Data flow**: It receives a conversation id, calls `_binding`, and returns just the handle portion of that result. The value may be `None` if the conversation has not been bound to any sandbox yet.

**Call relations**: `existing`, `claim_terminal`, and `_claim` use this when they only need to know what handle the conversation row currently names. It centralizes the read through `_binding` so workspace scoping and missing-conversation checks stay consistent.

*Call graph*: calls 1 internal fn (_binding); called by 3 (_claim, claim_terminal, existing).


##### `ConversationSandbox._binding`  (lines 359–380)

```
async def _binding(self, conversation_id: UUID) -> tuple[str | None, str]
```

**Purpose**: Reads the conversation’s stored sandbox handle and the owning agent’s requested sandbox size from the database. This gives sandbox opening both the current binding and the size to use for first creation.

**Data flow**: It receives a conversation id. Inside a workspace-scoped database transaction, it joins the conversation row to its agent row, limited to the current workspace, and reads `sandbox_handle` plus `sandbox_size`. If no matching conversation exists in this workspace, it raises an error. Otherwise it returns the handle and size.

**Call relations**: `open` calls this at the start of the create-or-resume flow, and `_stored` calls it for handle-only checks. It uses the current workspace context so one workspace cannot accidentally open or claim another workspace’s conversation.

*Call graph*: called by 2 (_stored, open); 3 external calls (select, workspace_tx, ws_current).


##### `ConversationSandbox._claim`  (lines 382–403)

```
async def _claim(self, conversation_id: UUID, stored: str | None, handle: str) -> str
```

**Purpose**: Attempts to save a sandbox handle in the conversation row only if the row still contains the value the caller previously saw. This is the race guard that keeps two simultaneous openers from both becoming official.

**Data flow**: It receives a conversation id, the handle value that was previously read, and the new handle to store. It runs a conditional database update: if the stored value still matches, it writes the new handle and returns it. If someone else won first, it rereads the stored handle and returns that winner instead. If the handle disappeared unexpectedly, it raises an error.

**Call relations**: `open` uses this to settle competing sandbox creations, and `claim_terminal` uses it to bind only still-unbound conversations to a terminal. When `_claim` loses the race, it calls `_stored` so the caller can adopt the handle that actually became durable.

*Call graph*: calls 1 internal fn (_stored); called by 2 (claim_terminal, open); 3 external calls (update, workspace_tx, ws_current).


### `core/src/ufo/sandbox/local.py`

`io_transport` · `cross-cutting: sandbox creation, command execution, and workspace file access`

This file is the project’s lightweight sandbox carrier. Instead of starting Docker or a cloud sandbox, it treats the conversation workspace as a host directory and runs commands there with their current working directory set to that folder. This is useful for local development because it needs only Python and the host operating system.

The file also fills in the safety and consistency pieces that a container image would normally provide. It creates a scratch area with helper programs like `sbx` and `sbxfs`, adds those helpers to the command path, and gives subprocesses a controlled environment. Web requests are still sent through the sandbox proxy, so metering and model-key substitution work the same way as in stronger sandbox carriers.

For file access, it does not trust plain string paths. A requested `/workspace/...` path is translated into the real host workspace, then checked through a containment guard that prevents links or `..`-style tricks from escaping the workspace. Think of it like giving someone access to one room in a house and checking every door they try, rather than trusting the label on the door.

This is not a true security boundary. A local subprocess is still a host process. The file’s job is convenience and behavioral compatibility, not kernel-level isolation.

#### Function details

##### `_git_without_host_config`  (lines 57–73)

```
def _git_without_host_config(scratch: Path) -> dict[str, str]
```

**Purpose**: Builds a set of environment variables that stop local Git settings from leaking into sandbox commands. This avoids problems such as host credential helpers opening prompts or hanging forever when a sandboxed command tries to use Git.

**Data flow**: It takes the scratch directory path as input. It points Git’s global config at a safe temporary home, disables system config, clears config passed through the environment, and turns off interactive password prompts. It returns a dictionary of environment variables that can be merged into a command’s environment.

**Call relations**: When `LocalCarrier.create` or `LocalCarrier.attach` prepares a sandbox handle, they call this helper so every later command runs with Git cut off from the host machine’s private Git setup.

*Call graph*: called by 2 (attach, create).


##### `_provision_scratch`  (lines 76–94)

```
def _provision_scratch() -> Path
```

**Purpose**: Creates the local carrier’s private scratch area. This area holds helper executables, small support modules, and a fake home directory for tools that expect to write under `$HOME`.

**Data flow**: It creates a temporary directory, adds `home` and `bin` subdirectories, copies the sandbox helper programs into `bin`, copies needed Python support files beside them, and marks the helper programs executable. It returns the path to this scratch root.

**Call relations**: This is used when a `LocalCarrier` instance is built, so later methods can add the helper directory to `PATH` and give subprocesses a controlled home. Internally it relies on temporary-directory creation and path operations.

*Call graph*: 2 external calls (Path, mkdtemp).


##### `LocalCarrier.create`  (lines 101–133)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a usable local sandbox handle for a conversation. It makes sure the workspace folder exists and prepares the environment that future commands will inherit.

**Data flow**: It receives a sandbox specification containing the workspace path, conversation identity, proxy settings, run token, and extra environment variables. It creates the host workspace directory, writes the proxy certificate into scratch space, builds proxy and tool-related environment variables, adds safe Git settings, and returns a `SandboxHandle` describing the ready local sandbox.

**Call relations**: This is the normal setup path for a writable local sandbox. It calls `_git_without_host_config` to protect commands from host Git settings, uses background threads for blocking filesystem work, and hands the finished information to `SandboxHandle` for later use by execution and file operations.

*Call graph*: calls 1 internal fn (_git_without_host_config); 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier.attach`  (lines 135–155)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Reconnects to an existing local workspace without creating it. This is useful for read-only browsing or resuming access only if the workspace already exists.

**Data flow**: It receives a sandbox specification and checks whether the workspace directory is already present. If not, it returns `None`. If it exists, it builds a `SandboxHandle` with the workspace path, scratch helper path, scratch home, and safe Git settings, then returns that handle.

**Call relations**: This is the cautious counterpart to `LocalCarrier.create`: it prepares the same kind of local command environment but does not make a new workspace. Like creation, it calls `_git_without_host_config` so later subprocesses avoid host Git behavior.

*Call graph*: calls 1 internal fn (_git_without_host_config); 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier.exec`  (lines 157–199)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs one command inside the local workspace as a host subprocess. It makes local execution look like sandbox execution by rewriting `/workspace` paths and using the sandbox handle’s controlled environment.

**Data flow**: It receives a sandbox handle, a command argument tuple, and a timeout. It finds the real workspace root, rewrites command arguments that mention `/workspace` to the host folder path, starts the subprocess in that folder, and collects standard output, standard error, and exit code. If the command runs too long, it kills the whole process group and returns an `ExecResult` saying it timed out.

**Call relations**: This is the main command-running path for the local carrier. It calls `_root` to locate the host workspace, starts the subprocess, uses `asyncio.wait_for` to enforce the timeout, and calls `_kill_process_group` if the command must be stopped.

*Call graph*: calls 2 internal fn (_kill_process_group, _root); 3 external calls (__init__, create_subprocess_exec, wait_for).


##### `LocalCarrier.write`  (lines 201–224)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into a file inside the local workspace. It keeps the event loop responsive by doing blocking filesystem work in a background thread.

**Data flow**: It receives a sandbox handle, a logical workspace path, and the bytes to write. It hands the real write work to `_write_contained` in another thread. It produces no return value, but the target file is created or replaced inside the workspace if the path is valid.

**Call relations**: Higher-level code calls this when it needs to copy content into the sandbox workspace. This method is the async wrapper; `_write_contained` performs the careful path checking and actual write.

*Call graph*: 1 external calls (to_thread).


##### `LocalCarrier._write_contained`  (lines 226–228)

```
def _write_contained(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Performs the actual safe write to the host filesystem. It ensures the requested file stays inside the workspace before replacing its contents.

**Data flow**: It receives a sandbox handle, a logical path, and file bytes. It converts the logical `/workspace` path into a relative workspace name, finds the host workspace root, asks the containment guard to open the target safely, and writes the bytes with the correct file mode. The result is a completed file update inside the workspace.

**Call relations**: `LocalCarrier.write` calls this in a worker thread. This helper uses `_workspace_name` and `_root` to translate from sandbox naming to host naming, then delegates the escape-prevention checks to `contained_file`.

*Call graph*: calls 2 internal fn (_root, _workspace_name); 1 external calls (contained_file).


##### `LocalCarrier.read`  (lines 230–241)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams bytes out of a file in the local workspace. It reads in chunks so large files do not need to be loaded into memory all at once.

**Data flow**: It receives a sandbox handle and a logical workspace path. It opens a safely checked source file through `_contained_source`, then repeatedly reads fixed-size chunks in a background thread and yields them to the caller. When reading finishes or fails, it closes the file.

**Call relations**: Higher-level code calls this to copy files out of the sandbox. This method provides the async stream, while `_contained_source` does the safe open that prevents path escapes or symlink tricks.

*Call graph*: 1 external calls (to_thread).


##### `LocalCarrier._contained_source`  (lines 243–252)

```
def _contained_source(self, handle: SandboxHandle, path: str) -> BufferedReader
```

**Purpose**: Safely opens a workspace file for reading. It refuses missing paths and paths that cannot be proven to stay inside the workspace.

**Data flow**: It receives a sandbox handle and logical path. It converts the path to a workspace-relative name, finds the host workspace root, opens the target through the containment guard, checks that it exists, and returns a binary file reader. If the path is missing or blocked by containment, it raises `FileNotFoundError`.

**Call relations**: `LocalCarrier.read` calls this before streaming file contents. It uses `_workspace_name`, `_root`, and `contained_file` so the returned reader is tied to the opened file itself, not to a path that could later be swapped.

*Call graph*: calls 2 internal fn (_root, _workspace_name); 1 external calls (contained_file).


##### `LocalCarrier.file_op`  (lines 254–259)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a structured workspace file operation through the same `sbxfs` helper used in other sandbox carriers. This keeps local behavior close to container behavior.

**Data flow**: It receives a sandbox handle, an operation name, and operation parameters. It passes them to `sbxfs_file_op`, which invokes the file-operation helper against the local workspace. It returns the helper’s result as a dictionary.

**Call relations**: When callers need richer file actions than simple read or write, they come through this method. The method hands off to `sbxfs_file_op`, relying on the scratch `PATH` prepared during create or attach so the helper can run locally.

*Call graph*: 1 external calls (sbxfs_file_op).


##### `LocalCarrier.dial`  (lines 261–269)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Reports that the local carrier cannot expose a sandbox service through a separate external port. Local subprocesses are not running inside a network-addressable remote sandbox.

**Data flow**: It receives a sandbox handle and a port number, but it does not try to connect. It immediately raises `SandboxUnreachable` with an explanation that a remote carrier is needed for this feature.

**Call relations**: Code that wants to reach an in-sandbox service can call this uniformly across carriers. For the local carrier, the story ends here with a clear error instead of pretending a remote-style connection is possible.

*Call graph*: 1 external calls (__init__).


##### `_kill_process_group`  (lines 272–277)

```
async def _kill_process_group(process: asyncio.subprocess.Process) -> None
```

**Purpose**: Force-stops a command and any child processes it started. This prevents a timed-out command from leaving background work running on the host.

**Data flow**: It receives an async subprocess object. It sends a kill signal to the whole process group using the process id, ignores the case where the process is already gone, and waits for the process to finish cleanup.

**Call relations**: `LocalCarrier.exec` calls this when a command times out or when execution is interrupted. It exists because killing only the direct child can leave grandchildren running.

*Call graph*: called by 1 (exec); 2 external calls (wait, killpg).


##### `_root`  (lines 280–283)

```
def _root(handle: SandboxHandle) -> Path
```

**Purpose**: Returns the real host directory that backs `/workspace` for a local sandbox handle. It also catches the invalid case where no host path was set.

**Data flow**: It receives a sandbox handle. If the handle has no workspace host path, it raises an error explaining that local sandboxes require one. Otherwise it converts the stored path into a `Path` object and returns it.

**Call relations**: `LocalCarrier.exec`, `LocalCarrier._write_contained`, and `LocalCarrier._contained_source` call this whenever they need to turn the sandbox handle into an actual host filesystem location.

*Call graph*: called by 3 (_contained_source, _write_contained, exec); 1 external calls (Path).


##### `_workspace_name`  (lines 286–290)

```
def _workspace_name(path: str) -> PurePosixPath
```

**Purpose**: Converts a logical sandbox path under `/workspace` into the name that should be used relative to the host workspace directory.

**Data flow**: It receives a path string such as `/workspace/file.txt`. It treats it as a POSIX-style path and strips the `/workspace` prefix, returning the remaining relative path. It does not by itself prove the path is safe; that check is done later by the containment guard.

**Call relations**: `LocalCarrier._write_contained` and `LocalCarrier._contained_source` call this before using `contained_file`. It supplies the workspace-relative name that the containment guard then checks against the real filesystem.

*Call graph*: called by 2 (_contained_source, _write_contained); 1 external calls (PurePosixPath).


### `extensions/docker/ufo_ext_docker.py`

`io_transport` · `sandbox creation, command execution, file access, idle reclaim`

A sandbox is the safe workspace where an agent can run commands and touch files. This file provides the Docker version of that sandbox. Instead of running commands directly on the host machine, it keeps one Docker container per conversation, with the conversation's workspace mounted at `/workspace`.

The file solves two main problems. First, it isolates work so commands run in a controlled container. Second, it keeps network access accountable: every command gets proxy settings for the current turn only, so API calls leaving the container go through the host's egress proxy and are attributed to the right run token. The real model API key is not placed inside the container; a harmless sentinel value is used there and replaced by the proxy when allowed.

Containers can outlive a single turn. To save host resources, old idle containers are stopped, not deleted. Think of this like turning off a parked car instead of scrapping it: the workspace remains, and a later touch can start the container again. The code also creates one Docker network per conversation and removes that network when reclaiming idle work, because Docker bridge networks are a limited resource.

The main class, `DockerCarrier`, is the carrier implementation. It can create or attach to a sandbox, run commands, stream file reads and writes, revive stopped containers, and clean up idle resources. It uses Docker's command-line tool under the hood.

#### Function details

##### `_docker`  (lines 71–85)

```
async def _docker(*argv: str, stdin: bytes=b'', timeout_s: int=60) -> tuple[int, bytes, bytes]
```

**Purpose**: Runs a Docker command asynchronously and returns its exit code, standard output, and standard error. It gives all other functions one consistent way to talk to Docker and to turn a timeout into a special internal failure code.

**Data flow**: It receives Docker command arguments, optional input bytes, and a timeout. It starts the `docker` process, sends the input to it, waits for completion, and collects output. It returns a three-part result: numeric status, bytes from standard output, and bytes from standard error; if the command takes too long, it kills the process and returns the internal timeout code.

**Call relations**: This is the shared doorway to Docker. Container lookup, creation, network setup, certificate installation, command execution, cleanup, and diagnostic inspection all call this helper when they need Docker to do something.

*Call graph*: called by 12 (_death_report, _ensure_network, _held_id, _install_ca, _reclaim_idle, _release, _revive, _running_id, _stopped_id, _write_started (+2 more)); 2 external calls (create_subprocess_exec, wait_for).


##### `DockerCarrier.create`  (lines 98–194)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates or reconnects to the Docker container for a conversation. It also prepares the per-command proxy environment so commands from this turn use the right network credentials without permanently storing them in the container.

**Data flow**: It receives a sandbox specification containing the conversation id, workspace path, image name, proxy details, run token, and environment variables. It first reclaims old idle containers, then looks for an existing running or stopped container with the deterministic conversation name. If found, it installs the current proxy certificate and returns a `SandboxHandle`; otherwise it creates the Docker network, starts a new container with the workspace mounted, installs the certificate, and returns the handle. If creation partly succeeds and then fails, it removes the new container and network.

**Call relations**: This is the normal entry point when a conversation needs an active sandbox. It uses the lookup helpers to find existing containers, `_revive` to restart stopped ones, `_ensure_network` and `_network_name` to prepare networking, `_install_ca` for TLS trust, and `_docker` to actually run Docker commands.

*Call graph*: calls 8 internal fn (_ensure_network, _install_ca, _network_name, _reclaim_idle, _revive, _running_id, _stopped_id, _docker); 1 external calls (__init__).


##### `DockerCarrier.attach`  (lines 196–220)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Finds an already-existing conversation container without creating a new one. It is used for read-style access where absence should be reported as `None` rather than turning into a fresh sandbox.

**Data flow**: It receives the sandbox specification and checks whether the conversation's named container is running. If not, it checks for a stopped one and tries to revive it. If no usable container exists, or revive fails, it returns `None`; otherwise it returns a `SandboxHandle` pointing at that container.

**Call relations**: This is the cautious counterpart to `create`. It relies on `_running_id` and `_stopped_id` to discover containers and `_revive` to restart stopped ones, then hands back a handle for later file reads or other sandbox access.

*Call graph*: calls 3 internal fn (_revive, _running_id, _stopped_id); 1 external calls (__init__).


##### `DockerCarrier._reclaim_idle`  (lines 222–290)

```
async def _reclaim_idle(self, opening: UUID) -> None
```

**Purpose**: Stops old idle containers and removes their per-conversation Docker networks so the host does not run out of memory or bridge network subnets. It stops containers rather than deleting them, so their workspaces can be resumed later.

**Data flow**: It receives the conversation id currently being opened and marks it as recently touched. It asks Docker for existing UFO sandbox containers and networks, records any it did not already know about, then finds conversations that have been idle long enough and have no command currently in flight. For each stale conversation, it reserves the cleanup by removing its touch record, locks its lifecycle, calls `_release`, and restores the touch record if release failed.

**Call relations**: `create` calls this before opening a sandbox. It uses `_held_id` to find the container that owns a name, `_release` to stop and free resources, `_docker` to list Docker objects, and UUID parsing to turn Docker names back into conversation ids.

*Call graph*: calls 3 internal fn (_held_id, _release, _docker); called by 1 (create); 1 external calls (UUID).


##### `DockerCarrier.exec`  (lines 292–324)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a command inside the sandbox container and returns its output, error text, exit code, and timeout information. It also prevents idle reclaim from stopping the container while the command is running.

**Data flow**: It receives a sandbox handle, command arguments, and a timeout. It marks the conversation as in use, builds Docker `--env` arguments from the handle's per-turn egress environment, and runs `docker exec`. If Docker says the container is not running, it tries to revive the container and retries once. It returns an `ExecResult`, translating this file's internal timeout marker into the shell-style timeout code.

**Call relations**: This is the command-running path used by higher-level sandbox operations. It calls `_docker` for the actual `docker exec`, asks `_revive` for recovery if the container was stopped, and produces the standard result object expected by the sandbox interface.

*Call graph*: calls 2 internal fn (_revive, _docker); 1 external calls (__init__).


##### `DockerCarrier.write`  (lines 326–344)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into a file inside the sandbox workspace. It streams the content through standard input instead of putting it on a command line, which is safer and works for arbitrary binary data.

**Data flow**: It receives a sandbox handle, a target path, and bytes to write. It marks the conversation as active, calls `_write_started` to perform the write inside the container, and if Docker reports the container is stopped, revives and retries once. If the final write fails, it raises an `OSError`; otherwise it completes without returning a value.

**Call relations**: Higher-level code uses this when it needs to copy a file into the sandbox. It delegates the actual Docker command to `_write_started` and uses `_revive` for the same stopped-container recovery used by command execution.

*Call graph*: calls 2 internal fn (_revive, _write_started).


##### `DockerCarrier._write_started`  (lines 346–362)

```
async def _write_started(self, handle: SandboxHandle, path: str, content: bytes) -> tuple[int, bytes]
```

**Purpose**: Performs one actual write attempt into the container. It runs the sandbox's guarded copy-in program so paths are interpreted safely inside the sandbox workspace.

**Data flow**: It receives the handle, destination path, and content bytes. It runs `docker exec -i` with Python inside the container, sends the content bytes to standard input, and passes the destination path and workspace directory as arguments. It returns the Docker exit code and standard error bytes for the caller to interpret.

**Call relations**: `write` calls this helper for the first attempt and possibly again after a revive. It uses `_docker` to start the Docker exec process.

*Call graph*: calls 1 internal fn (_docker); called by 1 (write).


##### `DockerCarrier.read`  (lines 364–398)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the sandbox container in chunks. It avoids loading the whole file into host memory and reports file errors in a way that matches normal operating-system file errors.

**Data flow**: It receives a sandbox handle and path. It marks the conversation as active, starts a `cat` process in the container through `_read_started`, and yields chunks of bytes as they arrive. After the stream ends, it checks whether the read failed; if the container had been stopped, it revives and retries once from the beginning. If `cat` reports a recognizable file-system reason, it raises the matching `OSError`; otherwise it raises a runtime error with diagnostic details.

**Call relations**: This is the main file-read path. It uses `_read_started` to create the streaming generator, `_revive` to recover from a stopped container before any useful read completes, and `_death_report` when a failed read has no useful error text.

*Call graph*: calls 3 internal fn (_death_report, _read_started, _revive).


##### `DockerCarrier._read_started`  (lines 400–442)

```
def _read_started(self, handle: SandboxHandle, path: str) -> tuple[AsyncGenerator[bytes], list[tuple[int, str]]]
```

**Purpose**: Sets up one read attempt and returns both the byte stream and a place where the final failure, if any, will be recorded. This split lets `read` retry cleanly without reusing a half-finished async generator.

**Data flow**: It receives a handle and file path. It creates an empty failure list and defines an inner stream that will run `docker exec cat path`. It returns the stream generator plus the failure list; after the caller drains the stream, the list is either still empty for success or contains the exit code and error text.

**Call relations**: `read` calls this when starting a read and again after a successful revive. The nested `DockerCarrier._read_started.stream` does the actual subprocess work.

*Call graph*: called by 1 (read).


##### `DockerCarrier._read_started.stream`  (lines 415–440)

```
async def stream() -> AsyncGenerator[bytes]
```

**Purpose**: Runs `cat` inside the container and yields the file's bytes as they arrive. It also makes sure an abandoned read does not leave a Docker exec process running in the background.

**Data flow**: It starts `docker exec container cat path` with output and error pipes. It repeatedly reads bounded chunks from standard output and yields them. Once output ends, it reads standard error, waits for the process, and records a failure if the exit code is non-zero. If the caller stops reading early, it kills and drains the process.

**Call relations**: This inner generator is created by `_read_started` and consumed by `read`. It calls `asyncio.create_subprocess_exec` directly rather than `_docker` because it must stream output progressively instead of waiting for the entire process to finish.

*Call graph*: 1 external calls (create_subprocess_exec).


##### `DockerCarrier._death_report`  (lines 444–462)

```
async def _death_report(self, handle: SandboxHandle) -> str
```

**Purpose**: Adds useful Docker state information when a file read dies without explaining why. This helps distinguish a killed `cat` command from a stopped, crashed, or missing container.

**Data flow**: It receives a sandbox handle and asks Docker to inspect the container's status, exit code, and out-of-memory flag. If inspection succeeds, it returns a short text report with those fields. If inspection fails, it returns text saying Docker inspect itself failed, including Docker's error output.

**Call relations**: `read` calls this only when `cat` exits badly with no standard error. `_death_report` uses `_docker` to query Docker for facts before `read` raises its final runtime error.

*Call graph*: calls 1 internal fn (_docker); called by 1 (read).


##### `DockerCarrier.file_op`  (lines 464–469)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a structured sandbox file operation using the `sbxfs` tool baked into the image. This covers operations that are more specific than raw read or write, while still going through the same container execution rules.

**Data flow**: It receives a handle, an operation name, and operation parameters. It passes those to the shared `sbxfs_file_op` helper, which uses this carrier to execute the file-system command in the sandbox. It returns the operation's result dictionary.

**Call relations**: Higher-level file APIs call this for sandbox file operations. It delegates to `ufo.sdk.sandbox.sbxfs_file_op`, so this carrier does not duplicate the common file-operation protocol.

*Call graph*: 1 external calls (sbxfs_file_op).


##### `DockerCarrier.dial`  (lines 471–478)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Reports that this Docker carrier cannot expose a service running inside the sandbox on an externally reachable host and port. It tells callers to use a remote carrier if they need that feature.

**Data flow**: It receives a sandbox handle and a port number, but does not use them to create a connection. It immediately raises `SandboxUnreachable` with a clear explanation.

**Call relations**: Code that wants to reach an in-sandbox service may call `dial`. For this carrier, the story ends here: unlike some remote sandbox backends, it has no per-port public route to hand back.

*Call graph*: 1 external calls (__init__).


##### `DockerCarrier._release`  (lines 480–494)

```
async def _release(self, conversation_id: UUID, container_id: str | None) -> bool
```

**Purpose**: Stops a conversation's container and removes its Docker network. This frees the host resources that idle reclaim is trying to recover.

**Data flow**: It receives a conversation id and possibly a container id. If there is a container id, it asks Docker to stop it and gives up with `False` if stopping fails. Then it removes the conversation's network name and returns `True` if removal succeeds or the network was already gone; otherwise it returns `False`.

**Call relations**: `_reclaim_idle` calls this while holding the conversation's lifecycle lock. It uses `_network_name` to compute the Docker network name and `_docker` to perform the stop and network removal.

*Call graph*: calls 2 internal fn (_network_name, _docker); called by 1 (_reclaim_idle).


##### `DockerCarrier._revive`  (lines 496–516)

```
async def _revive(self, conversation_id: UUID, container_id: str) -> bool
```

**Purpose**: Restarts a stopped container and reconnects it to its per-conversation network. This is how the system resumes a sandbox that was stopped during idle reclaim.

**Data flow**: It receives the conversation id and container id. Under the lifecycle lock, it marks the conversation as touched, ensures the Docker network exists, connects the container to that network, and starts the container. It returns `True` if the start succeeds, `False` if Docker refuses the connect or start in an expected way, and raises if network creation itself fails.

**Call relations**: Many user-facing operations rely on this recovery path: `attach`, `create`, `exec`, `read`, and `write` call it when they find a stopped container. It uses `_network_name`, `_ensure_network`, and `_docker` to rebuild enough Docker state for the container to run again.

*Call graph*: calls 3 internal fn (_ensure_network, _network_name, _docker); called by 5 (attach, create, exec, read, write).


##### `DockerCarrier._held_id`  (lines 518–526)

```
async def _held_id(self, name: str) -> str | None
```

**Purpose**: Finds the Docker container id for a named sandbox container in any state, not just running. Reclaim needs this because even a stopped or paused container can still be the object associated with the conversation name.

**Data flow**: It receives a Docker container name. It runs `docker ps -aq` filtered to that exact name, checks for Docker command failure, and returns the matching container id text or `None` if there is no match.

**Call relations**: `_reclaim_idle` calls this before releasing a stale conversation. It uses `_docker` for the lookup and gives `_release` the container id it may need to stop.

*Call graph*: calls 1 internal fn (_docker); called by 1 (_reclaim_idle).


##### `DockerCarrier._stopped_id`  (lines 528–537)

```
async def _stopped_id(self, name: str) -> str | None
```

**Purpose**: Finds the Docker container id for a named sandbox container that exists but is exited. This lets the carrier resume a reclaimed sandbox instead of creating a new one.

**Data flow**: It receives a Docker container name. It asks Docker for containers with that exact name and `exited` status, raises if the Docker query itself fails, and returns the found id or `None`.

**Call relations**: `create` and `attach` call this after they do not find a running container. If it returns an id, those callers can try `_revive`.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create).


##### `DockerCarrier._running_id`  (lines 539–550)

```
async def _running_id(self, name: str) -> str | None
```

**Purpose**: Finds the Docker container id for a named sandbox container that is currently running. It treats Docker command failures as real errors, not as proof that no container exists.

**Data flow**: It receives a Docker container name. It runs `docker ps -q` filtered to the exact name and running status. If Docker succeeds, it returns the id or `None`; if Docker fails, it raises a runtime error with Docker's message.

**Call relations**: `create` and `attach` use this as their first check. When it finds a running container, they can return a handle immediately instead of creating or reviving anything.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create).


##### `DockerCarrier._network_name`  (lines 552–553)

```
def _network_name(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the deterministic Docker network name for one conversation. Using a predictable name lets separate operations find the same network later.

**Data flow**: It receives a conversation UUID. It combines the carrier's network prefix with the UUID's compact hexadecimal form and returns that string.

**Call relations**: `create`, `_revive`, and `_release` call this whenever they need to create, connect, or remove the conversation's Docker network.

*Call graph*: called by 3 (_release, _revive, create).


##### `DockerCarrier._ensure_network`  (lines 555–566)

```
async def _ensure_network(self, network: str) -> None
```

**Purpose**: Makes sure a Docker network exists, creating it if needed. It is safe when two tasks race to create the same network because Docker's already-exists response is treated as success.

**Data flow**: It receives a network name. It first asks Docker whether a matching network already exists; if so, it returns. If not, it runs `docker network create`. It raises an error only when Docker cannot list networks or cannot create the network for a reason other than an already-existing name.

**Call relations**: `create` calls this before starting a new container, and `_revive` calls it before reconnecting a stopped container. It uses `_docker` for both listing and creation.

*Call graph*: calls 1 internal fn (_docker); called by 2 (_revive, create).


##### `DockerCarrier._install_ca`  (lines 568–581)

```
async def _install_ca(self, container_id: str, ca_cert: str) -> None
```

**Purpose**: Installs the current egress proxy certificate authority inside a container. Without this, HTTPS connections from a reused container might not trust the proxy after the host process restarts and generates a new certificate.

**Data flow**: It receives a container id and certificate text. It runs a root shell command in the container, writes the certificate into the system certificate directory through standard input, and runs `update-ca-certificates`. If that Docker exec fails, it raises a runtime error with Docker's error message.

**Call relations**: `create` calls this for both new and reused containers before returning a handle. It uses `_docker` to run the privileged certificate-install command inside the container.

*Call graph*: calls 1 internal fn (_docker); called by 1 (create).


##### `manifest`  (lines 584–589)

```
def manifest() -> Manifest
```

**Purpose**: Declares this file as the Docker carrier extension so the larger system can discover and instantiate it. It names the carrier and points to `DockerCarrier` as the factory class.

**Data flow**: It takes no input. It constructs a `Manifest` containing the extension name, version, and a `CarrierSpec` for the Docker carrier. It returns that manifest object to the plugin or extension loader.

**Call relations**: The system's extension-loading path calls this when it wants to learn what this module provides. The returned manifest connects the name `docker` to the `DockerCarrier` implementation.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/e2b/ufo_ext_e2b.py`

`io_transport` · `sandbox startup, resume, command execution, file transfer, and service dialing`

A sandbox is an isolated computer-like workspace where the system can run commands and store files for one conversation. This file plugs E2B into the project as one possible sandbox provider. Without it, a deployment configured to use E2B would not know how to open a sandbox, run commands inside it, copy files in or out, or expose an in-sandbox service to the outside.

The main class, E2BCarrier, is the bridge between the project’s generic sandbox interface and E2B’s software development kit, or SDK, which is a library for talking to E2B. It creates a sandbox from the right template, reconnects to an existing one when a conversation resumes, installs the proxy certificate that allows safe HTTPS traffic, and ensures the shared /workspace directory exists.

A key idea here is the “lease”: E2B pauses idle sandboxes after a timeout, but can resume them later without losing files. The file keeps a small in-memory record of when each sandbox is believed to stay awake until. Before commands, uploads, downloads, or port access, it renews that lease when needed. It also routes all outbound network traffic through the project’s egress proxy, using a run token so requests can be attributed and controlled.

#### Function details

##### `_egress_env`  (lines 134–169)

```
def _egress_env(proxy: ProxyEndpoint, run_token: str) -> dict[str, str]
```

**Purpose**: Builds the environment variables that make commands inside the remote sandbox send outbound network traffic through the project’s egress proxy. This is what keeps external calls metered, authenticated, and protected instead of letting the sandbox talk directly to the internet.

**Data flow**: It receives a proxy description and a run token. It checks that the proxy has a public HTTPS URL, turns that URL into proxy settings with the run token embedded as the username, adds safe placeholder model API keys and certificate settings, and returns a dictionary of environment variables. If the proxy URL is missing or unsafe, it raises an error before any sandbox command can run unprotected.

**Call relations**: E2BCarrier.create calls this while preparing the SandboxHandle for a conversation. The returned settings are later included when E2BCarrier.exec runs commands, so every command inherits the same controlled network route.

*Call graph*: called by 1 (create); 1 external calls (urlsplit).


##### `E2BCommands.run`  (lines 182–190)

```
async def run(self, cmd: str, *, cwd: str | None=None, envs: dict[str, str] | None=None, user: str | None=None, timeout: float | None=None) -> E2BCommandResult
```

**Purpose**: Describes the E2B SDK method used to run a shell command inside a sandbox. It is a protocol method, meaning this file states the shape of the SDK object it expects rather than implementing the SDK itself.

**Data flow**: A command string, optional working directory, environment variables, user, and timeout go in. The SDK runs the command inside E2B and returns text output plus an exit code, or raises an SDK error if the command fails in a special way.

**Call relations**: E2BCarrier relies on this method in preparation and execution paths. It is the low-level command channel beneath E2BCarrier.exec, _install_ca, and _ensure_workspace.


##### `E2BFileStream.__aiter__`  (lines 197–197)

```
def __aiter__(self) -> AsyncIterator[bytes]
```

**Purpose**: Describes how a streamed file download can be read chunk by chunk using asynchronous iteration. This lets large files be consumed without loading the whole file into memory.

**Data flow**: The stream object goes in as the thing being iterated. It produces bytes over time, one chunk at a time, until the remote file has been fully read.

**Call relations**: E2BCarrier.read uses this behavior after asking E2B to open a streamed file read. The carrier then yields each chunk onward to its caller.


##### `E2BFileStream.aclose`  (lines 199–199)

```
async def aclose(self) -> None
```

**Purpose**: Describes the async close operation for a streamed file download. It matters because an open stream is also an open network connection that should be released.

**Data flow**: The open stream is the input. Calling this tells the SDK to close the underlying connection; it produces no file data and returns when cleanup is done.

**Call relations**: E2BCarrier.read calls this in a final cleanup step, even if the reader stops early or an error happens during streaming.


##### `E2BFiles.write`  (lines 203–203)

```
async def write(self, path: str, data: str | bytes, *, user: str | None=None) -> object
```

**Purpose**: Describes the E2B SDK method for writing data into a file in the sandbox. It is the path used when the system needs to upload bytes rather than squeeze data through a shell command.

**Data flow**: A sandbox path, text or bytes, and optionally a user go in. The SDK sends the content to E2B’s filesystem API and writes it at that path, returning only a generic result from the SDK.

**Call relations**: E2BCarrier.write uses this for user-visible uploads, and E2BCarrier._install_ca uses it to place the proxy certificate into the sandbox before installing it.


##### `E2BFiles.read`  (lines 205–205)

```
async def read(self, path: str, format: str) -> E2BFileStream
```

**Purpose**: Describes the E2B SDK method for reading a sandbox file, especially as a stream. This is how the carrier can download produced files in pieces.

**Data flow**: A sandbox path and requested format go in. The SDK opens a read operation and returns a stream-like object that yields bytes asynchronously.

**Call relations**: E2BCarrier.read calls this after renewing the sandbox lease. The returned stream is then consumed and closed by the carrier.


##### `E2BSandbox.get_host`  (lines 214–214)

```
def get_host(self, port: int) -> str
```

**Purpose**: Describes the SDK helper that formats the public host name for a port exposed from inside the sandbox. It lets outside clients reach a service that a command started inside E2B.

**Data flow**: A port number goes in. A host name for that sandbox port comes out, without necessarily making a network request.

**Call relations**: E2BCarrier.dial calls this after making sure the sandbox will stay awake long enough for the outside connection to use the address.


##### `E2BSdk.create`  (lines 218–227)

```
async def create(self, *, template: str, timeout: int, metadata: dict[str, str], lifecycle: SandboxLifecycle, network: SandboxNetworkOpts, api_key: str) -> E2BSandbox
```

**Purpose**: Describes the E2B SDK operation for creating a new cloud sandbox. The carrier uses it when there is no usable existing sandbox for a conversation.

**Data flow**: Template name, lease timeout, metadata, lifecycle settings, network settings, and API key go in. E2B creates a sandbox and returns an object representing it, including its id and command/file interfaces.

**Call relations**: E2BCarrier._resume_or_open calls this only after it cannot resume an existing sandbox. The returned sandbox is then prepared before being handed back to the rest of the system.


##### `E2BSdk.connect`  (lines 229–235)

```
async def connect(self, sandbox_id: str, *, timeout: int, api_key: str) -> E2BSandbox
```

**Purpose**: Describes the E2B SDK operation for reconnecting to an existing sandbox by id. In E2B, this also wakes a paused sandbox and sets how long it should stay alive.

**Data flow**: A sandbox id, desired timeout span, and API key go in. If E2B still has that sandbox, an active sandbox object comes out; if not, the SDK raises a not-found error.

**Call relations**: All reconnects are funneled through E2BCarrier._connected, which adds retry and timeout behavior around this SDK call.


##### `E2BCarrier.create`  (lines 270–345)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Opens the sandbox for a conversation and returns a handle the rest of the system can use. It may reuse an already known sandbox, reconnect to one saved from an earlier process, or create a new one.

**Data flow**: A SandboxSpec goes in, containing the conversation id, optional saved sandbox id, size, proxy settings, run token, and environment. The method builds proxy environment variables, chooses whether to resume or create, prepares the sandbox by installing trust and making /workspace ready, records a lease, and returns a SandboxHandle. It may log and continue if preparation is safely deferred on a known resumed sandbox, but strict preparation failures are raised.

**Call relations**: This is the main startup path for E2B sandboxes. It calls _egress_env, checks _leased, delegates opening to _resume_or_open, prepares through _prepare or _prepare_strictly, and stores the resulting lease for later exec, read, write, and dial calls.

*Call graph*: calls 5 internal fn (_leased, _prepare, _prepare_strictly, _resume_or_open, _egress_env); 5 external calls (__init__, __init__, timeout, emit_metric, log).


##### `E2BCarrier.attach`  (lines 347–370)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Reconnects to an existing sandbox for read-style access without creating a new one. It is used when absence should mean “not available” rather than “start from an empty workspace.”

**Data flow**: A SandboxSpec goes in. If it has no saved sandbox id, the method returns None. If it has an id, the method tries to connect to that exact sandbox, records a fresh lease if successful, and returns a SandboxHandle without egress environment. If E2B says the sandbox is gone, it clears any stale local lease and returns None.

**Call relations**: This path calls _connected directly instead of using the local cache. That keeps it honest: it reports what E2B currently has, not what this process remembers.

*Call graph*: calls 1 internal fn (_connected); 2 external calls (__init__, __init__).


##### `E2BCarrier._resume_or_open`  (lines 372–412)

```
async def _resume_or_open(self, spec: SandboxSpec, resume_id: str | None) -> E2BSandbox
```

**Purpose**: Chooses between reconnecting to an existing sandbox and creating a fresh one. It keeps resumed conversations tied to their saved workspace whenever possible.

**Data flow**: It receives the desired sandbox specification and an optional sandbox id to resume. If an id is present, it tries _connected; if E2B no longer has that sandbox, it logs the loss and continues. It then looks up the template for the requested size, creates a sandbox through the SDK, checks that traffic access was provided, and returns the sandbox.

**Call relations**: E2BCarrier.create calls this before preparation. It hands off reconnects to _connected and uses the SDK create operation only when reconnecting is impossible or not requested.

*Call graph*: calls 1 internal fn (_connected); called by 1 (create); 1 external calls (log).


##### `E2BCarrier._prepare_strictly`  (lines 414–430)

```
async def _prepare_strictly(self, sandbox: E2BSandbox, spec: SandboxSpec) -> None
```

**Purpose**: Runs sandbox preparation in the strict mode required for fresh or not-yet-proven sandboxes. It retries only the kind of transport failure that might be E2B dropping a connection during startup.

**Data flow**: A sandbox and its spec go in. The method calls _prepare; on success, nothing else changes. On retryable network transport errors, it logs, emits a metric, waits, and tries again. If attempts run out or the error is not retryable, it drops the local lease and raises the error.

**Call relations**: E2BCarrier.create uses this when the sandbox cannot be assumed already prepared. It calls _prepare for the real work and _drop when the local lease can no longer be trusted.

*Call graph*: calls 2 internal fn (_drop, _prepare); called by 1 (create); 3 external calls (sleep, emit_metric, log).


##### `E2BCarrier._connected`  (lines 432–494)

```
async def _connected(self, conversation_id: UUID, sandbox_id: str, span: int) -> E2BSandbox
```

**Purpose**: Reconnects to an E2B sandbox with bounded retries. It turns an unreliable off-cluster network call into a controlled operation with a clear total wait time.

**Data flow**: A conversation id, sandbox id, and desired lease span go in. The method repeatedly calls the SDK connect operation, retrying transport errors with increasing delay, while an overall timeout limits the whole process. A successful SDK response returns an active sandbox object; a provider not-found response passes through; repeated or timed-out transport failures are logged and raised.

**Call relations**: This is the single reconnect doorway used by _resume_or_open, _sandbox, and attach. Those callers decide what a successful reconnect means in their own context.

*Call graph*: called by 3 (_resume_or_open, _sandbox, attach); 4 external calls (sleep, timeout, ReadTimeout, log).


##### `E2BCarrier._prepare`  (lines 496–503)

```
async def _prepare(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: Makes a sandbox ready for normal work. It installs the proxy certificate into trusted system locations and ensures the shared workspace directory exists.

**Data flow**: A sandbox and certificate text go in. The method first calls _install_ca, then _ensure_workspace. It returns nothing when both setup steps complete, or lets setup errors rise to the caller.

**Call relations**: E2BCarrier.create may call this directly for a safely resumed sandbox with a short timeout. _prepare_strictly calls it for sandboxes that must be fully ready before use.

*Call graph*: calls 2 internal fn (_ensure_workspace, _install_ca); called by 2 (_prepare_strictly, create).


##### `E2BCarrier._leased`  (lines 505–520)

```
def _leased(self, conversation_id: UUID) -> _Lease | None
```

**Purpose**: Looks up the locally remembered lease for a conversation and clears old leases from memory. This prevents the process from keeping references forever to sandboxes it touched once.

**Data flow**: A conversation id goes in. The method reads the internal lease map, compares stored expiry times to the current clock, deletes expired entries, and returns the original lease for the requested conversation if one was present.

**Call relations**: E2BCarrier.create uses this to decide whether an in-process sandbox can be reused. E2BCarrier._sandbox uses it to decide whether a command, file operation, or dial needs a reconnect first.

*Call graph*: called by 2 (_sandbox, create).


##### `E2BCarrier._install_ca`  (lines 522–530)

```
async def _install_ca(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: Installs the egress proxy’s certificate authority inside the sandbox. A certificate authority, or CA, is what lets the sandbox trust HTTPS connections that pass through the proxy.

**Data flow**: A sandbox and CA certificate text go in. The method writes the certificate to a staging path as root, then runs an install command as root. If the command exits with failure, it turns the command output into a clear RuntimeError.

**Call relations**: _prepare calls this before workspace setup. Its work is required so later commands using the proxy can make trusted HTTPS requests.

*Call graph*: called by 1 (_prepare).


##### `E2BCarrier._ensure_workspace`  (lines 532–541)

```
async def _ensure_workspace(self, sandbox: E2BSandbox) -> None
```

**Purpose**: Makes sure /workspace exists in the sandbox and is owned by the normal sandbox user. This is the shared working directory where conversation files live.

**Data flow**: A sandbox goes in. The method runs a root command to create the directory if needed and set ownership. If that command fails, it raises a RuntimeError with the command’s output.

**Call relations**: _prepare calls this after installing the CA. Later command execution uses /workspace as the working directory.

*Call graph*: called by 1 (_prepare).


##### `E2BCarrier.exec`  (lines 543–583)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a command inside the sandbox and returns a standard ExecResult. It translates E2B-specific command failures and timeouts into the project’s common command result shape.

**Data flow**: A SandboxHandle, argument tuple, and timeout go in. The method renews or reconnects the sandbox through _sandbox, quotes the argument list into a shell command, runs it in /workspace with proxy and sandbox environment variables, and returns stdout, stderr, and exit code. Command exit errors become normal results; command timeouts become exit code 124 with timeout details; unexpected failures drop the lease and are raised.

**Call relations**: This is the main command-running path used by higher-level sandbox users. It depends on _sandbox for a live lease and uses _drop when a provider failure makes the cached lease unreliable.

*Call graph*: calls 2 internal fn (_drop, _sandbox); 3 external calls (__init__, join, emit_metric).


##### `E2BCarrier.write`  (lines 585–596)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Uploads bytes into a file inside the sandbox. It is used for file content that should not be passed through a shell command string.

**Data flow**: A SandboxHandle, destination path, and byte content go in. The method gets a live sandbox through _sandbox, writes the bytes through E2B’s filesystem API, and returns nothing on success. If the upload fails unexpectedly, it drops the lease and raises the error.

**Call relations**: Higher-level file upload flows call this through the carrier interface. It relies on _sandbox for connection and lease renewal, and calls _drop if the provider call breaks trust in the lease.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.read`  (lines 598–618)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the sandbox in chunks. This lets large files be downloaded without storing the whole file in this process at once.

**Data flow**: A SandboxHandle and file path go in. The method obtains a sandbox with a lease long enough for a stream, asks E2B for a streaming read, yields each byte chunk to the caller, and always closes the stream at the end. If E2B says the file is missing, it raises the normal Python FileNotFoundError; other unexpected failures drop the lease.

**Call relations**: This is the main download path for sandbox files. It uses _sandbox first, then the E2B file stream methods, and protects cleanup with the stream’s close operation.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.file_op`  (lines 620–625)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a structured file operation using the project’s sbxfs helper inside the sandbox. This gives the rest of the system a common way to ask for file listings or similar filesystem actions.

**Data flow**: A SandboxHandle, operation name, and parameter dictionary go in. The method passes them to sbxfs_file_op, which runs the helper command through this carrier, and returns a dictionary result.

**Call relations**: This is a thin adapter. It hands off the real work to the shared sandbox helper so E2B file operations behave like other carriers’ file operations.

*Call graph*: 1 external calls (sbxfs_file_op).


##### `E2BCarrier.dial`  (lines 627–648)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Returns the outside address for a service running on a port inside the sandbox. This is how a browser endpoint, preview server, or similar in-sandbox service can be reached from outside.

**Data flow**: A SandboxHandle and port number go in. The method renews the sandbox lease for a longer dial window, asks the sandbox for the host name for that port, adds E2B’s traffic access token as a header if present, and returns a DialTarget. If E2B says the sandbox is gone, it raises SandboxUnreachable instead of leaking the provider-specific error.

**Call relations**: Higher-level code calls this when it needs a network address into the sandbox. It depends on _sandbox to keep the container awake because no later carrier call may happen during the outside connection.

*Call graph*: calls 1 internal fn (_sandbox); 2 external calls (__init__, __init__).


##### `E2BCarrier._sandbox`  (lines 650–692)

```
async def _sandbox(self, handle: SandboxHandle, needed_seconds: int, span_floor: int=SANDBOX_LEASE_SECONDS) -> E2BSandbox
```

**Purpose**: Returns a sandbox object that is safe to use for the next operation. It either reuses a still-good local lease or reconnects to E2B and records a new lease.

**Data flow**: A SandboxHandle, the number of seconds the next operation needs, and an optional minimum lease span go in. The method checks the local lease map, verifies that it names the same sandbox and lasts long enough, and returns it if safe. Otherwise it removes the stale entry, reconnects through _connected with an adequate span, stores the renewed lease, logs the renewal, and returns the sandbox.

**Call relations**: E2BCarrier.exec, write, read, and dial all call this before touching E2B. It calls _leased to inspect cache state and _connected when the provider must be asked to resume or extend the sandbox.

*Call graph*: calls 2 internal fn (_connected, _leased); called by 4 (dial, exec, read, write); 2 external calls (__init__, log).


##### `E2BCarrier._drop`  (lines 694–699)

```
def _drop(self, conversation_id: UUID, during: str) -> None
```

**Purpose**: Forgets the cached lease for a conversation after a provider call fails. It does not delete the remote sandbox; it only stops trusting this process’s local belief about its awake-until time.

**Data flow**: A conversation id and a short label describing what was happening go in. The method removes that conversation from the local lease map and logs the drop. Nothing is returned.

**Call relations**: _prepare_strictly, exec, write, and read call this when a failure means the next operation should reconnect rather than reuse a possibly stale sandbox object.

*Call graph*: called by 4 (_prepare_strictly, exec, read, write); 1 external calls (log).


##### `sandbox_templates`  (lines 702–719)

```
def sandbox_templates(value: str) -> dict[str, str]
```

**Purpose**: Parses the E2B template configuration from an environment variable. It turns text such as size-to-template mappings into the dictionary needed to create sandboxes of each supported size.

**Data flow**: A comma-separated string goes in. The function splits each entry into a sandbox size and template reference, validates that every entry is well formed, checks that the configured sizes exactly match the project’s supported sandbox sizes, and returns a dictionary. Bad or incomplete configuration raises RuntimeError.

**Call relations**: build_e2b_carrier calls this during carrier construction. Its validation prevents the system from starting with a sandbox size option that E2B cannot actually serve.

*Call graph*: called by 1 (build_e2b_carrier).


##### `build_e2b_carrier`  (lines 722–729)

```
def build_e2b_carrier() -> E2BCarrier
```

**Purpose**: Builds an E2BCarrier from environment variables. This is the factory used when the deployment selects E2B as its sandbox backend.

**Data flow**: It reads the E2B API key and template mapping from the process environment. If either is missing, it raises a clear RuntimeError. Otherwise it parses the templates with sandbox_templates and returns a configured E2BCarrier.

**Call relations**: manifest registers this function as the carrier factory. The wider system calls it when loading the E2B carrier from the manifest.

*Call graph*: calls 1 internal fn (sandbox_templates); 1 external calls (__init__).


##### `manifest`  (lines 732–744)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the project’s plugin system. It tells the core system that a carrier named e2b exists and how to construct it.

**Data flow**: No input is required. The function creates a Manifest containing a CarrierSpec with the carrier name, factory function, off-cluster flag, and supported sizes, then returns it.

**Call relations**: The project’s extension loader calls this during plugin discovery. The returned manifest is what lets configuration such as backend = "e2b" find build_e2b_carrier.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/sandbox/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. That matters because other parts of the project can then refer to code inside this folder using names like `ufo.sandbox.some_module`.

There is no actual behavior here: no functions, no classes, and no setup code. Its value is structural. Think of it like a label on a drawer: the label does not do the work, but it lets everyone know the drawer exists and gives them a reliable way to find what is inside. Without this file, depending on the Python version and packaging setup, imports involving `ufo.sandbox` might fail or behave differently.


### Safety policy foundations
These modules constrain filesystem access, provide safe credential placeholders, and define the proxy rule package and policy derivation.

### `core/src/ufo/sandbox/containment.py`

`domain_logic` · `cross-cutting file access`

A filename like `report.txt` sounds harmless, but a hostile process can use path tricks such as `../..` or symbolic links, which are shortcuts that point somewhere else, to make a program read or overwrite files outside its allowed area. This file prevents that. It is like a guard at a building who does not just check the address on a package, but also walks the route, locks each door behind them, and refuses packages that turn out to be hidden redirects.

The main idea is that every important file operation must prove four things: the name is a usable relative path, the real location stays inside the approved root, every directory on the way is opened without following symlinks, and the final target is checked without following a final symlink. That last detail matters because an attacker could place a symlink where the final file should be and point it at a sensitive host file.

The `ContainedFile` object represents a file after these checks. It stores not only the visible path, but also an open file descriptor for the parent directory. A file descriptor is an operating-system handle to a real directory, so later renames or swaps cannot quietly redirect the operation. The module also offers lighter helpers for cases where the process cannot inspect the filesystem yet, such as validating a stored file key or cleaning a provided attachment name.

#### Function details

##### `contained_root`  (lines 87–100)

```
def contained_root(root: str | os.PathLike[str]) -> Path
```

**Purpose**: Checks that a sandbox root exists, is a real directory, and is not itself a symlink. This is used when the root might be controlled or changed by untrusted code, so following a root symlink would be unsafe.

**Data flow**: It receives a root path, turns it into a `Path`, inspects the path itself without following symlinks, and refuses it if it is missing or not a directory. If it passes, it returns the canonical resolved directory path that later checks can compare against.

**Call relations**: This is the first gate used by `contained_file` and `contained_dir`. Those higher-level functions rely on it before they inspect any requested file or directory below the root.

*Call graph*: called by 2 (contained_dir, contained_file); 4 external calls (__init__, __init__, Path, S_ISDIR).


##### `configured_root`  (lines 103–120)

```
def configured_root(root: str | os.PathLike[str], setting: str) -> Path
```

**Purpose**: Checks a root path that came from operator configuration, where a deliberate symlink is allowed. This supports normal deployment layouts such as a configured storage directory pointing to a mounted disk.

**Data flow**: It receives a configured path and the setting name it came from, follows the path to inspect the real target, and verifies that the target exists and is a directory. It returns the resolved directory path, or raises an error that names the broken setting.

**Call relations**: Unlike `contained_root`, this is not called by the file guards in this module. It is meant for setup code that validates trusted deployment configuration before later path checks use the resulting root.

*Call graph*: 4 external calls (__init__, __init__, Path, S_ISDIR).


##### `ContainedFile.lstat`  (lines 138–149)

```
def lstat(self) -> os.stat_result | None
```

**Purpose**: Looks at the target file itself without following a final symlink. It tells callers whether the target exists and confirms that, if it does exist, it is a normal file rather than a directory or special object.

**Data flow**: It uses the stored parent directory file descriptor and the target name to inspect exactly that entry. Missing target becomes `None`; a directory, symlink, device, or other non-regular file becomes a `NotRegularFile` error; a normal file returns its filesystem details.

**Call relations**: Callers use this after `contained_file` has created a safe `ContainedFile`. `contained_regular` uses it to require that an existing safe path really names a regular file.

*Call graph*: 4 external calls (__init__, stat, S_ISDIR, S_ISREG).


##### `ContainedFile.mode`  (lines 151–166)

```
def mode(self, default: int) -> int
```

**Purpose**: Finds the permission bits to use when replacing this file. It preserves permissions from an existing regular file, uses a supplied default when no normal file is there, and refuses directories.

**Data flow**: It reads the target entry through the pinned parent directory. If the file is missing, it returns the caller’s default mode; if it is a directory, it raises an error; if it is a regular file, it returns only the ordinary permission bits; other entry types fall back to the default.

**Call relations**: This is a helper for safe write flows that want replacement files to keep existing permissions where possible. It relies on the parent directory already being pinned by `contained_file`.

*Call graph*: 4 external calls (__init__, stat, S_ISDIR, S_ISREG).


##### `ContainedFile.open_bytes`  (lines 168–178)

```
def open_bytes(self) -> BufferedReader
```

**Purpose**: Opens the contained file for streaming bytes safely. This is useful for large files because callers can read a little at a time instead of loading the whole file into memory.

**Data flow**: It asks `_open_regular` to open the target without following symlinks and to prove it is a normal file. It then wraps the raw operating-system file descriptor in a buffered binary reader and returns that reader; if wrapping fails, it closes the descriptor before raising the error.

**Call relations**: This is the public streaming read method on `ContainedFile`. `read_bytes` calls it when it wants a simpler one-shot read.

*Call graph*: calls 1 internal fn (_open_regular); called by 1 (read_bytes); 2 external calls (close, fdopen).


##### `ContainedFile.read_bytes`  (lines 180–183)

```
def read_bytes(self, limit: int) -> bytes
```

**Purpose**: Reads up to a caller-chosen number of bytes from a safe contained file. It is the simple read method for code that does not need streaming.

**Data flow**: It receives a byte limit, opens the file safely through `open_bytes`, reads no more than that many bytes, closes the file automatically, and returns the bytes read.

**Call relations**: This builds on `open_bytes` for safety and resource cleanup. `read_text` calls it when the caller wants decoded text instead of raw bytes.

*Call graph*: calls 1 internal fn (open_bytes); called by 1 (read_text).


##### `ContainedFile.read_text`  (lines 185–186)

```
def read_text(self, limit: int) -> str
```

**Purpose**: Reads a safe contained file as text. Invalid UTF-8 byte sequences are replaced instead of causing the read to fail.

**Data flow**: It receives a byte limit, gets that many bytes through `read_bytes`, decodes them as UTF-8 text, replaces undecodable bytes with placeholder characters, and returns a string.

**Call relations**: This is the text-friendly layer above `read_bytes`. It depends on the same safe opening path provided by `open_bytes` and `_open_regular`.

*Call graph*: calls 1 internal fn (read_bytes).


##### `ContainedFile.chmod`  (lines 188–189)

```
def chmod(self, mode: int) -> None
```

**Purpose**: Changes the contained file’s ordinary permission bits. It applies the change to the target name relative to the already-pinned parent directory.

**Data flow**: It receives a numeric mode, keeps only the standard permission bits, and asks the operating system to apply them to the target entry without following symlinks.

**Call relations**: This is used after a caller has obtained a `ContainedFile` from `contained_file`. It does not redo containment checks because the parent directory handle already anchors the operation.

*Call graph*: 1 external calls (chmod).


##### `ContainedFile.unlink`  (lines 191–195)

```
def unlink(self) -> None
```

**Purpose**: Deletes the contained target if it exists. If the file is already gone, it treats that as success.

**Data flow**: It asks the operating system to remove the target name from the pinned parent directory. A missing file is ignored; other filesystem errors still surface.

**Call relations**: This is a safe delete operation for callers that already passed through `contained_file`. It uses the stored parent directory file descriptor so the delete cannot be redirected by changing path components.

*Call graph*: 1 external calls (unlink).


##### `ContainedFile.replace_with`  (lines 197–199)

```
def replace_with(self, source: ContainedFile) -> None
```

**Purpose**: Atomically renames another contained file onto this target. Atomic means readers see either the old file or the new file, not a half-written mix.

**Data flow**: It receives another `ContainedFile` as the source, then asks the operating system to replace this target name with the source name, using both files’ pinned parent directory descriptors.

**Call relations**: This connects two already-validated contained locations. It relies on both `ContainedFile` objects having been created by safe containment checks.

*Call graph*: 1 external calls (replace).


##### `ContainedFile.replace_text`  (lines 201–202)

```
def replace_text(self, text: str, mode: int) -> None
```

**Purpose**: Writes text to the contained target by replacing the whole file safely. It is a convenience wrapper for callers that have a string instead of bytes.

**Data flow**: It receives text and a permission mode, encodes the text into bytes, and passes those bytes to `replace_bytes`. The result is a complete file replacement at the target name.

**Call relations**: This is a thin layer above `replace_bytes`. All of the careful staging and atomic replacement work happens in `replace_bytes`.

*Call graph*: calls 1 internal fn (replace_bytes).


##### `ContainedFile.replace_bytes`  (lines 204–228)

```
def replace_bytes(self, data: bytes, mode: int) -> None
```

**Purpose**: Safely writes bytes by first creating a new temporary sibling file, then renaming it over the target. This avoids following a malicious symlink and prevents readers from seeing a partly written file.

**Data flow**: It receives bytes and a permission mode, creates a randomly named staged file in the pinned parent directory using flags that refuse existing names and symlinks, writes the bytes, sets permissions, and renames the staged file onto the target. In cleanup, it closes any open descriptor and removes the staged file if something went wrong.

**Call relations**: This is the main safe write operation on `ContainedFile`. `replace_text` calls it after encoding text, and other callers can use it directly for binary data.

*Call graph*: called by 1 (replace_text); 7 external calls (close, fchmod, fdopen, open, replace, unlink, uuid4).


##### `ContainedFile._open_regular`  (lines 230–246)

```
def _open_regular(self) -> int
```

**Purpose**: Opens the target for reading only if it is a real regular file and not a symlink. This is the low-level safety check behind streaming reads.

**Data flow**: It tries to open the target name through the pinned parent directory with symlink-following disabled. If the file is missing, it raises `PathNotFound`; if a symlink or non-regular object is encountered, it raises `NotRegularFile`; otherwise it returns the raw file descriptor.

**Call relations**: `open_bytes` calls this before wrapping the descriptor in a Python file object. Keeping this check private keeps the public read methods simpler and consistent.

*Call graph*: called by 1 (open_bytes); 6 external calls (__init__, __init__, close, fstat, open, S_ISREG).


##### `contained_file`  (lines 250–284)

```
def contained_file(path: str | os.PathLike[str], root: str | os.PathLike[str], *, create_parent: bool=False) -> Iterator[ContainedFile]
```

**Purpose**: This is the main safe entry point for reading or writing a single file under a root. It performs the full containment process and yields a `ContainedFile` whose parent directory is pinned by an open file descriptor.

**Data flow**: It receives a requested path, a root, and an option to create missing parent directories. It validates the root, anchors relative paths under that root, rejects unusable target names, resolves the parent location, checks that it stays inside the root, opens the root directory, descends each parent component without following symlinks, optionally creates missing directories as it goes, and yields a `ContainedFile`. When the caller is done, it closes the pinned directory descriptor.

**Call relations**: This pulls together `contained_root`, `rooted`, `_inside`, `_open_root`, and `_descend` into the complete file guard. `contained_regular` uses it when it needs a safe existing file path.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); called by 1 (contained_regular); 5 external calls (__init__, __init__, __init__, close, mkdir).


##### `contained_dir`  (lines 287–313)

```
def contained_dir(path: str | os.PathLike[str], root: str | os.PathLike[str], *, create: bool=False) -> Path
```

**Purpose**: Validates and returns the canonical path of a directory under a root. It is for directory enumeration or setup, where the caller needs a safe directory path rather than a file handle.

**Data flow**: It receives a directory path, a root, and an option to create missing directories. It validates the root, anchors the path, resolves it, checks that the resolved directory stays under the root, opens the root, walks each directory component without following symlinks, optionally creates components as needed, then closes the descriptor and returns the resolved path.

**Call relations**: This uses the same core helpers as `contained_file`, but its result is a path for a directory instead of a `ContainedFile`. `contained_glob` calls it to decide where a safe file listing should start.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); called by 1 (contained_glob); 3 external calls (__init__, close, mkdir).


##### `contained_regular`  (lines 316–325)

```
def contained_regular(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> Path
```

**Purpose**: Returns the safe canonical path of an existing regular file under a root. It is for cases where another tool or library needs a filename rather than an open file descriptor.

**Data flow**: It receives a path and root, runs them through `contained_file`, asks the resulting `ContainedFile` to inspect the target itself, and refuses missing or non-regular files. If all checks pass, it returns the contained file’s path.

**Call relations**: This is a path-returning wrapper around `contained_file`. It should be used when a caller cannot read through the safer file-descriptor based methods.

*Call graph*: calls 1 internal fn (contained_file); 1 external calls (__init__).


##### `contained_pattern`  (lines 328–345)

```
def contained_pattern(pattern: str, root: Path) -> str
```

**Purpose**: Checks and rewrites a glob pattern so it cannot escape the root. A glob pattern is a filename pattern such as `*.txt` or `logs/**/*.json` used to list matching files.

**Data flow**: It receives a pattern and root, rejects any pattern containing `..`, leaves safe relative patterns unchanged, and converts safe absolute patterns into root-relative patterns. If an absolute pattern points outside the root or names the root itself, it raises an error.

**Call relations**: `contained_glob` calls this after choosing the safe directory to enumerate. This keeps pattern safety in one place instead of making every listing caller remember the same rules.

*Call graph*: called by 1 (contained_glob); 3 external calls (__init__, __init__, PurePosixPath).


##### `contained_glob`  (lines 348–360)

```
def contained_glob(pattern: str, path: str | os.PathLike[str] | None, root: Path) -> tuple[Path, str]
```

**Purpose**: Prepares a safe directory and pattern for file enumeration under a root. It prevents absolute glob patterns from silently starting at the whole filesystem.

**Data flow**: It receives a pattern, an optional starting path, and the root. If the pattern is absolute, it starts from the root; otherwise it starts from the provided path or the root. It then validates the start directory with `contained_dir` and validates or rewrites the pattern with `contained_pattern`, returning both.

**Call relations**: This coordinates `contained_dir` and `contained_pattern` for safe listing flows. Enumeration code can call this once and then run the returned pattern in the returned directory.

*Call graph*: calls 2 internal fn (contained_dir, contained_pattern); 1 external calls (PurePosixPath).


##### `contained_relative`  (lines 363–388)

```
def contained_relative(path: str, root: str) -> str
```

**Purpose**: Performs a purely text-based containment check for paths that this process cannot inspect on disk yet. It proves that the path intends to name a file under a given root, but it does not prove anything about symlinks.

**Data flow**: It receives a path string and a root string, combines relative paths with the root, processes `.` and `..` parts like a path simplifier, rejects attempts to climb above the root, rejects naming the root itself, and returns the normalized absolute-looking path string.

**Call relations**: This is separate from the full filesystem guards because some callers only have a future file key, mount path, or container-internal path. Later actual reads or writes should still go through the stronger descent checks where the filesystem is available.

*Call graph*: 3 external calls (__init__, __init__, PurePosixPath).


##### `contained_leaf`  (lines 391–399)

```
def contained_leaf(raw: str, fallback: str) -> str
```

**Purpose**: Turns an externally supplied filename into one safe filename component. It drops directory parts so a provider-supplied name cannot choose folders.

**Data flow**: It receives a raw name and a fallback name, treats backslashes like slashes so Windows-style paths are also handled, keeps only the final filename part, and returns the fallback if the result is empty, `.` , or `..`.

**Call relations**: This is a lexical helper for inbound names such as attachments or content-disposition filenames. It does not replace full containment; callers still join the returned leaf under a root and write through the main guard.

*Call graph*: 1 external calls (PurePosixPath).


##### `is_contained_regular`  (lines 402–413)

```
def is_contained_regular(path: Path, root: Path) -> bool
```

**Purpose**: Checks whether an already-enumerated path is a regular file inside the root without having crossed a symlink. It is intended as a fast filter for directory listings.

**Data flow**: It receives a candidate path and root, rejects it if its own entry is not a regular file, resolves the path strictly, and returns true only when the resolved path is exactly the same path and lies inside the root. Any filesystem error becomes `False`.

**Call relations**: This uses `_inside` for the final root check. It is meant to decide what to list; actual reading of a listed file should still go through `contained_file`.

*Call graph*: calls 1 internal fn (_inside); 3 external calls (lstat, resolve, S_ISREG).


##### `rooted`  (lines 416–422)

```
def rooted(path: str | os.PathLike[str], root: Path) -> Path
```

**Purpose**: Anchors a caller-provided path under a root when it is relative. This prevents relative paths from accidentally being interpreted against the process’s current working directory.

**Data flow**: It receives a path and a root. If the path is already absolute, it returns it as a `Path`; otherwise it returns the root joined with that path.

**Call relations**: `contained_file` and `contained_dir` call this early so all later checks talk about the same intended location.

*Call graph*: called by 2 (contained_dir, contained_file); 1 external calls (Path).


##### `_inside`  (lines 425–426)

```
def _inside(path: Path, root: Path) -> bool
```

**Purpose**: Answers the simple question: is this path the root itself or somewhere below it? It is the common containment comparison used after paths have been resolved.

**Data flow**: It receives a path and root and checks whether the path equals the root or has the root among its parent directories. It returns a boolean answer.

**Call relations**: `contained_file`, `contained_dir`, and `is_contained_regular` call this after resolving paths. It is small, but it keeps the meaning of “inside the root” consistent.

*Call graph*: called by 3 (contained_dir, contained_file, is_contained_regular).


##### `_open_root`  (lines 429–433)

```
def _open_root(root: Path) -> int
```

**Purpose**: Opens the root directory as a directory handle without following a symlink. This creates the starting anchor for the safe component-by-component walk.

**Data flow**: It receives a root path and asks the operating system to open it with directory-only and no-symlink-following flags. If the root cannot be opened that way, it raises `NonDirectoryAncestor`; otherwise it returns the raw directory file descriptor.

**Call relations**: `contained_file` and `contained_dir` call this before descending into child directories. `_descend` then continues the walk from this starting descriptor.

*Call graph*: called by 2 (contained_dir, contained_file); 2 external calls (__init__, open).


##### `_descend`  (lines 436–449)

```
def _descend(descriptor: int, part: str, target: Path) -> int
```

**Purpose**: Moves one directory component deeper while refusing symlinks and non-directories. It also closes the previous directory handle so the walk does not leak resources.

**Data flow**: It receives the current directory descriptor, the next path component, and the full target path for error messages. It opens the child component as a directory without following symlinks, translates missing or invalid components into containment errors, closes the old descriptor, and returns the new child descriptor.

**Call relations**: `contained_file` and `contained_dir` call this repeatedly while walking from the root to the requested parent directory or directory target. Together with `_open_root`, it is the mechanism that pins each step to real directories instead of trusting path strings.

*Call graph*: called by 2 (contained_dir, contained_file); 5 external calls (__init__, __init__, __init__, close, open).


### `core/src/ufo/sandbox/exec_env.py`

`domain_logic` · `sandbox open / probe execution setup`

When the system opens a sandbox to run a probe, the command inside often needs access to outside services: Git hosts, connector command-line tools, or keyed providers such as monitoring APIs. This file decides exactly which environment variables to put into that sandbox.

The important safety idea is that the sandbox does not receive real passwords or API keys. It receives sentinels: harmless marker strings. Later, when traffic leaves the sandbox, an egress proxy recognizes those markers and swaps in the real credential only on the outgoing request. An everyday analogy is a coat-check ticket: the sandbox carries the ticket, but the valuable item stays behind the counter.

`ProbeEnv` is the main object. Its `exports` method gathers four kinds of values: the conversation id, Git configuration, connector CLI credential sentinels, and keyed provider environment variables. Git is special because it reads authentication through configuration entries rather than ordinary environment variables, so this file formats Git settings in the indexed style Git expects.

The helper functions are careful not to export half-working credentials. If a credential slot is empty, unavailable, or points to an unresolvable host, the environment variable is skipped and a warning is logged. For connector CLI grants, if more than one possible account would match the same static environment variable, the code refuses to guess and logs the ambiguity instead.

#### Function details

##### `ProbeEnv.exports`  (lines 60–74)

```
async def exports(self, conversation_id: UUID, probe_id: UUID, acting_member_id: UUID | None=None) -> dict[str, str]
```

**Purpose**: Builds the full set of environment variables for a probe running inside a sandbox. It combines the conversation id with safe credential placeholders for Git, connector command-line tools, and keyed providers.

**Data flow**: It receives a conversation id, a probe id, and optionally the member the probe is acting as. It reads the current workspace id, then asks helper functions to produce Git settings, CLI grant variables, and provider credential variables. It returns one dictionary of environment variable names to string values, ready to pass into the sandbox.

**Call relations**: This is the file’s main entry point. When a probe sandbox is opened, this method coordinates the smaller helpers: it gets the current workspace with `ws_current`, formats Git config through `_git_config_env`, asks `_git_credential_config` which Git hosts need sentinels, asks `_grant_cli_env` which connector accounts can be exposed, and asks `_keyed_provider_env` which provider variables should appear.

*Call graph*: calls 4 internal fn (_git_config_env, _git_credential_config, _grant_cli_env, _keyed_provider_env); 1 external calls (ws_current).


##### `_git_config_env`  (lines 77–84)

```
def _git_config_env(settings: tuple[tuple[str, str], ...]) -> dict[str, str]
```

**Purpose**: Turns Git configuration settings into the environment-variable format Git understands. This lets the system configure Git for a sandbox run without writing a Git config file inside the sandbox.

**Data flow**: It receives a tuple of Git setting pairs, where each pair is a setting name and value. It counts them, then creates `GIT_CONFIG_COUNT` plus numbered `GIT_CONFIG_KEY_*` and `GIT_CONFIG_VALUE_*` entries. It returns those entries as a dictionary.

**Call relations**: `ProbeEnv.exports` calls this after collecting the fixed Git proxy setting and any credential-related Git settings. This helper does not decide what the settings mean; it only packages them in Git’s expected shape.

*Call graph*: called by 1 (exports).


##### `_git_credential_config`  (lines 87–122)

```
async def _git_credential_config(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> tuple[tuple[str, str], ...]
```

**Purpose**: Finds the Git credential slots that are actually usable for this workspace and turns them into Git header settings containing safe sentinels. This allows Git commands in the sandbox to authenticate through the proxy without seeing real credentials.

**Data flow**: It receives an optional credential store, the declared credential slots, and the workspace id. For each slot, it checks whether the slot is meant for Git basic authentication, whether a credential is stored, and which host it applies to. Successful slots become Git `extraheader` settings; missing, failing, or unavailable slots are skipped, with warnings when something goes wrong. It returns a tuple of Git setting pairs.

**Call relations**: `ProbeEnv.exports` calls this before formatting Git environment variables. This helper relies on `slot_is_set` to avoid exporting unused slots, `credential_host` to resolve the real host name, and `warn` to record problems without stopping the whole sandbox setup.

*Call graph*: called by 1 (exports); 3 external calls (credential_host, slot_is_set, warn).


##### `_keyed_provider_env`  (lines 125–167)

```
async def _keyed_provider_env(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> dict[str, str]
```

**Purpose**: Creates environment variables for provider credentials that are declared as credential slots, such as an API key variable and possibly a provider host variable. It exports sentinels and host names, not secret key values.

**Data flow**: It receives an optional credential store, credential slot declarations, and the workspace id. It walks through the slots, keeps only those with an environment-variable target or host-variable target, checks that the workspace has a stored credential, and resolves the selected host. It returns a dictionary containing provider environment variables; empty, failing, or unavailable slots are skipped and warnings are recorded.

**Call relations**: `ProbeEnv.exports` calls this while assembling the sandbox environment. Like `_git_credential_config`, it uses `slot_is_set` and `credential_host` to avoid exporting misleading variables, and `warn` to make credential-slot problems visible without crashing the probe setup.

*Call graph*: called by 1 (exports); 3 external calls (credential_host, slot_is_set, warn).


##### `_grant_cli_env`  (lines 170–212)

```
async def _grant_cli_env(grants: GrantStore | None, clis: Mapping[str, CliCredential], acting_member_id: UUID | None, run_id: UUID) -> dict[str, str]
```

**Purpose**: Chooses which connector command-line credentials should be visible to the sandbox and exports them as sentinels. It prefers the acting member’s own connected account, then falls back to a workspace-shared connection.

**Data flow**: It receives an optional grant store, the known connector CLI credential declarations, the acting member id, and the run id. It asks the grant store for active grants, filters them by provider, separates private grants from shared grants, and chooses the private set if available or the shared set otherwise. If exactly one account matches, it sets the connector’s environment variable to that account’s sentinel; if several accounts match, it logs the ambiguity and exports nothing for that provider. It returns the resulting environment dictionary.

**Call relations**: `ProbeEnv.exports` calls this when a sandbox probe may need connector command-line tools. This helper gets current permissions through `GrantStore.active_grants`, creates safe account markers with `grant_sentinel`, and uses `log` when it refuses to guess between multiple possible accounts.

*Call graph*: calls 1 internal fn (active_grants); called by 1 (exports); 2 external calls (grant_sentinel, log).


### `core/src/ufo/sandbox/proxy/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means code elsewhere can refer to this folder using names like `ufo.sandbox.proxy` and then import the actual proxy-related modules inside it. Think of it like a label on a drawer: the label does not contain the tools, but it tells Python that the drawer is part of the organized toolkit. Without this file, depending on the Python version and packaging setup, imports from this folder might not work reliably or might behave differently. There are no functions, classes, settings, or startup actions here. Its value is structural: it helps make the sandbox proxy code discoverable as part of the larger `ufo` package.


### `core/src/ufo/sandbox/proxy/rules.py`

`domain_logic` · `sandbox turn setup and egress rule derivation`

A sandbox is meant to run untrusted or semi-trusted work without letting it freely reach the internet or see raw secrets. This file is the rule factory for the egress proxy, meaning the gatekeeper for outbound network traffic. Instead of letting code inside the sandbox decide what it can call, the system derives rules from trusted project state: the chosen model, installed manifests, workspace credentials, connector grants, and the blob store used for files.

The rules are small value objects. A scope rule says which exact host is allowed. An injection rule says, “if the sandbox sends this harmless placeholder, replace it on the wire with the real secret.” A meter rule says requests to a host should be counted for spending or usage. A forward rule says a request should be executed through the broker, a trusted server-side service that holds the real account token. An internet rule is broader: it allows public internet during live turns when an extension asks for it.

The important safety idea is that the sandbox usually sees only sentinels, which are fake marker values. The real credential is added later by the proxy or broker. Like handing someone a claim ticket instead of the vault key, this lets sandboxed code request access without ever possessing the secret itself.

#### Function details

##### `provider_host`  (lines 91–95)

```
def provider_host(model: str) -> str
```

**Purpose**: Finds which model provider host should be contacted for a given model name. For example, model names starting with OpenAI-style prefixes map to OpenAI’s API host, while Claude-style names map to Anthropic’s host.

**Data flow**: It receives a model name as text. It checks the known model-name prefixes in order, returns the matching provider host, and raises an error if the model name does not match any known provider.

**Call relations**: This is used by derive_model_rules when building the network rules for the selected model. It supplies the exact host that later becomes allowed, authenticated, and metered.

*Call graph*: called by 1 (derive_model_rules).


##### `derive_model_rules`  (lines 98–113)

```
def derive_model_rules(model: str, real_key: str) -> tuple[Rule, ...]
```

**Purpose**: Builds the rules that let the sandbox call the configured language model provider safely. It allows only the provider’s host, replaces the sandbox’s fake model key with the real key on outbound requests, and marks model usage for token metering.

**Data flow**: It receives a model name and the real API key. It asks provider_host for the correct provider host, chooses the right authentication header shape for that provider, then returns a small set of rules: allow that host, inject the real key in place of the sentinel value, and meter usage as tokens.

**Call relations**: This function is part of the rule-building flow for every run that needs model access. It delegates host selection to provider_host and then creates the scope, injection, and metering rules that the proxy will later read.

*Call graph*: calls 1 internal fn (provider_host); 3 external calls (__init__, __init__, __init__).


##### `derive_manifest_rules`  (lines 116–118)

```
def derive_manifest_rules(manifests: tuple[Manifest, ...]) -> tuple[InternetRule, ...]
```

**Purpose**: Checks whether any installed extension says the sandbox needs internet access. If so, it adds the broad rule that allows public internet during live turns.

**Data flow**: It receives the extension manifests. It looks for any manifest with sandbox internet enabled, then returns either one internet rule or no rules at all.

**Call relations**: This contributes one piece to the overall egress rule set. Other derivation functions create precise host-based rules, while this one adds broader internet permission only when a manifest explicitly asks for it.

*Call graph*: 1 external calls (__init__).


##### `derive_artifact_store_rules`  (lines 121–137)

```
async def derive_artifact_store_rules(blob: BlobStore) -> tuple[Rule, ...]
```

**Purpose**: Allows the sandbox to upload shared files when the artifact store is backed by Amazon S3. Without this, a sandbox could create a file but the proxy would block the network request needed to store or share it.

**Data flow**: It receives the active blob store. If the store is an S3-backed store, it asks that store for the host used for uploads, then returns rules allowing that exact host and metering requests to it. If the store is not network-backed in this way, it returns no rules.

**Call relations**: This is used when composing the full proxy permissions for a run. It asks the blob store for its upload host and hands the proxy a narrow exception, rather than opening the whole internet just to let file sharing work.

*Call graph*: 3 external calls (__init__, __init__, put_host).


##### `derive_credential_rules`  (lines 140–203)

```
async def derive_credential_rules(slots: tuple[CredentialSlot, ...], workspace_id: UUID, store: CredentialStore) -> tuple[Rule, ...]
```

**Purpose**: Builds safe outbound access rules for credentials declared by extensions. It lets the sandbox use stored workspace secrets without ever placing the raw secret inside the sandbox.

**Data flow**: It receives credential slots, a workspace identifier, and the credential store. For each slot that declares header injection, it tries to read the slot’s secret and resolve the allowed host for that workspace. If either is missing or fails, it logs a warning and skips only that slot. For successful slots, it creates injection rules, groups them by host, adds one allow rule per host, and adds metering where the slot asks for it. For Git basic authentication, it converts the username and secret into the Basic authorization format before injection.

**Call relations**: This function is a major part of the proxy rule derivation flow. It calls the credential store helpers to resolve secrets and hosts, records warnings instead of crashing the whole turn when one slot fails, and emits the concrete scope, injection, and meter rules the proxy will use.

*Call graph*: 7 external calls (__init__, __init__, __init__, b64encode, credential_host, slot_secret, warn).


##### `derive_grant_rules`  (lines 206–223)

```
def derive_grant_rules(grants: tuple[Grant, ...], transfer_hosts: 'ConnectorTransferHosts | None'=None) -> tuple[Rule, ...]
```

**Purpose**: Builds network rules for connector grants, which are permissions to use external accounts through the broker. It allows the connector provider host and any needed broker file-transfer hosts, and meters requests to them.

**Data flow**: It receives active grants and, optionally, a lookup object for connector transfer hosts. For each grant, it gathers the grant’s provider host plus any transfer hosts, removes empty and duplicate hosts while preserving order, then returns allow and request-metering rules for those hosts. It does not inject secrets because the broker keeps and uses the real account token server-side.

**Call relations**: This function fits into the egress setup for connector-enabled runs. If transfer host information is available, it asks ConnectorTransferHosts.of for the extra hosts connected to a provider, then produces the rules the proxy needs for brokered connector traffic.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_cli_rules`  (lines 226–246)

```
def derive_cli_rules(grants: tuple[Grant, ...], acting_member_id: UUID | None, clis: Mapping[str, CliCredential]) -> tuple[Rule, ...]
```

**Purpose**: Creates forwarding rules for connector command-line credentials. These rules let eligible sentinel-bearing requests be sent through the broker, where the real credential is applied outside the sandbox.

**Data flow**: It receives active grants, the acting member’s identifier if there is one, and a map of connector CLI credential declarations. It keeps only grants whose provider has a CLI credential and whose account the acting member is allowed to use. For those, it turns the grant account into a sentinel value and returns forward rules containing the host, header, sentinel, account id, and broker forwarding function.

**Call relations**: This complements derive_grant_rules. The grant rules open and meter the host, while these forwarding rules explain how eligible authenticated requests should be routed through the broker instead of being sent directly from the sandbox.

*Call graph*: 2 external calls (__init__, grant_sentinel).


##### `ConnectorTransferHosts.of`  (lines 260–261)

```
def of(self, provider: str) -> tuple[str, ...]
```

**Purpose**: Looks up which file-transfer hosts should be allowed for a connector provider. If a provider was explicitly listed, it uses that provider’s declared hosts; otherwise it falls back to the default hosts for the open connector namespace.

**Data flow**: It receives a provider name. It checks the explicit provider-to-host mapping and returns the matching tuple of hosts, or returns the default tuple if the provider is not explicitly present.

**Call relations**: derive_grant_rules calls this when it needs to know which extra broker file-store hosts belong to a grant. This keeps the grant rule builder simple and hides the defaulting behavior in one small lookup.


##### `connector_transfer_hosts`  (lines 264–275)

```
def connector_transfer_hosts(manifests: tuple[Manifest, ...]) -> ConnectorTransferHosts
```

**Purpose**: Builds the lookup table that tells grant rule derivation which file-transfer hosts belong to which connector providers. It uses installed manifests as the trusted source of connector declarations.

**Data flow**: It receives the deploy’s manifests. It scans every manifest connector, mapping each connector provider to its declared transfer hosts. It also asks for the open connector namespace and uses its transfer hosts as the default for providers that are not explicitly registered. It returns a ConnectorTransferHosts object containing both pieces.

**Call relations**: This prepares data for derive_grant_rules. It delegates namespace discovery to open_connector_namespace, then packages the explicit and default host information so grant processing can ask a simple provider-to-hosts question later.

*Call graph*: 2 external calls (__init__, open_connector_namespace).


### Session access and changes
These files select the active sandbox backend, expose the controlled session API, and preserve summaries of workspace file changes.

### `core/src/ufo/sandbox/select.py`

`orchestration` · `startup`

A sandbox is the isolated place where agent work can run without freely touching the host machine. This file is the small switchboard that decides which kind of sandbox to use for this process. There is always a built-in local sandbox, and extensions can add more choices, such as Docker, a hosted sandbox, or another remote runner.

The important job here is to turn a name from configuration, like `[sandbox] backend = local`, into one real sandbox carrier object. A carrier is the backend adapter that knows how to create and talk to sandboxes of that kind. The code first builds a menu of available carriers. It starts with the built-in `local` option, then adds any carriers advertised by extension manifests. If two carriers try to use the same name, it stops immediately, because otherwise the same configuration name could mean two different things.

After that, it looks up the configured backend name. If nothing registered that name, it raises a clear error instead of silently falling back. For remote backends, it also checks that a public HTTPS proxy URL is configured. This matters because a remote sandbox cannot reach a process-local proxy, and traffic leaving the sandbox needs to be credential-injected, denied by default, metered, and encrypted in transit. In short: this file prevents ambiguous, missing, or unsafe sandbox selection.

#### Function details

##### `select_carrier`  (lines 12–51)

```
def select_carrier(config: Config, manifests: tuple[Manifest, ...]) -> tuple[Carrier, CarrierSpec]
```

**Purpose**: Chooses the single sandbox carrier that this process will run. It combines the built-in local carrier with carriers supplied by extensions, checks that the configured backend name is valid, and enforces extra safety rules for remote sandboxes.

**Data flow**: It receives the loaded configuration and a tuple of extension manifests. It builds a dictionary from backend names to carrier specifications, beginning with a built-in `local` specification created with `CarrierSpec`. It adds each extension-provided carrier, rejecting duplicate names. It then reads `config.sandbox.backend` to find the selected carrier. If the name is unknown, it raises `NotRegisteredError`. If the selected carrier runs outside the local cluster, it reads `config.sandbox.proxy_public_url`, parses it with `urlparse`, and requires it to be a valid HTTPS URL. If all checks pass, it creates the carrier by calling the selected factory and returns both the carrier instance and its specification.

**Call relations**: This function is used at setup time as the place where sandbox configuration becomes a live backend object. It calls `CarrierSpec.__init__` to define the built-in local option, may call `NotRegisteredError.__init__` when the requested backend was never registered, and calls `urllib.parse.urlparse` to inspect the public proxy URL for remote backends. After it returns, the wider runtime can hold onto the chosen carrier and its metadata knowing there is exactly one matching backend and that remote access has the required secure proxy setting.

*Call graph*: 3 external calls (__init__, __init__, urlparse).


### `core/src/ufo/sandbox/session.py`

`domain_logic` · `cross-cutting: sandbox creation, per-turn tool execution, file access, and proxy authorization`

A sandbox is like a rented workshop for one conversation: tools can build files and run commands there, but they should not be able to wander into the rest of the system. This file defines that boundary. It names the shared workspace path, describes what a sandbox provider must be able to do, and wraps those abilities in a per-turn SandboxSession that tools use.

The file also protects network access. Sandboxed commands may need to go through an egress proxy, which is a controlled gateway for outbound traffic. To prove which conversation and turn a request belongs to, the sandbox uses signed tokens. RunTokenCodec and ProbeTokenCodec create and verify those tokens so the proxy can reject forged or wrong-kind credentials.

Another important job is path safety. Functions such as workspace_path normalize user-supplied paths and reject attempts to escape /workspace, much like a guard checking that every requested room is still inside the workshop. File operations then go through the carrier, which is the pluggable backend interface implemented by Docker, local, or remote sandbox systems. This lets the rest of UFO use one stable sandbox API while different deployments swap out how sandboxes are actually created and reached.

#### Function details

##### `_basic_username`  (lines 74–80)

```
def _basic_username(header: str) -> str
```

**Purpose**: Extracts the username from a Basic authentication header. In this system, that username is where the signed sandbox token is carried.

**Data flow**: It receives an HTTP Proxy-Authorization header string. It checks that the header uses Basic authentication, decodes the base64 text, takes the part before the colon, and returns that as the username. If the header is missing or not Basic auth, it raises an error instead of guessing.

**Call relations**: The run-token and probe-token decoders call this first when the proxy receives credentials. After this function pulls out the username, those decoders verify that the username is a valid signed token.

*Call graph*: called by 2 (from_proxy_auth, from_proxy_auth); 1 external calls (b64decode).


##### `RunTokenCodec.from_env`  (lines 99–103)

```
def from_env(cls) -> 'RunTokenCodec'
```

**Purpose**: Builds a RunTokenCodec from the deployment secret stored in the environment. This secret is what lets the server mint tokens that the proxy can later trust.

**Data flow**: It reads the configured token-secret environment variable. If the value is present, it turns it into bytes and returns a codec. If it is missing, it stops startup with a clear error because sandbox run tokens could not be signed safely.

**Call relations**: The main serving paths call this during startup, including the proxy server and the main serve routine. That gives the running process a shared token signer before sandboxes or proxy checks begin.

*Call graph*: called by 2 (serve, run).


##### `RunTokenCodec.encode`  (lines 105–108)

```
def encode(self, run: RunToken) -> str
```

**Purpose**: Creates a signed token that identifies one sandbox run: the workspace, the turn, and optionally the member acting in that turn. The proxy uses this later to decide what outbound network access belongs to.

**Data flow**: It receives a RunToken object. It turns the IDs into a compact text payload, uses '-' when there is no acting member, signs the payload with the codec secret, and returns the signed text.

**Call relations**: The sandbox-opening flow calls this when preparing a sandbox for a turn. The resulting token is placed into the sandbox's proxy environment so outbound requests can prove which turn they come from.

*Call graph*: called by 1 (_open_sandbox); 1 external calls (sign_token).


##### `RunTokenCodec.from_proxy_auth`  (lines 110–122)

```
def from_proxy_auth(self, header: str) -> RunToken
```

**Purpose**: Verifies a proxy authentication header and turns it back into a RunToken. This is how the proxy confirms that a sandbox request really came from a token minted by this deployment.

**Data flow**: It receives a proxy authorization header. It extracts the Basic-auth username, verifies the signature with the codec secret, checks that the token is specifically a run token, parses the stored IDs, and returns a RunToken. If decoding, signing, type checking, or ID parsing fails, it raises a single invalid-token error.

**Call relations**: The proxy-side code uses this after _basic_username has extracted the credential. It hands back a trusted RunToken that downstream authorization checks can use.

*Call graph*: calls 1 internal fn (_basic_username); 3 external calls (__init__, verify_token, UUID).


##### `ProbeTokenCodec.encode`  (lines 157–163)

```
def encode(self, probe: ProbeToken) -> str
```

**Purpose**: Creates a signed token for an off-turn probe command. A probe is not tied to a currently running turn, so its token carries its own expiry time.

**Data flow**: It receives a ProbeToken containing workspace, conversation, probe, optional member, and expiry information. It formats those values with the probe-token kind marker, signs the payload, and returns the signed token text.

**Call relations**: This pairs with ProbeTokenCodec.from_proxy_auth. Code that starts a probe can mint a short-lived credential, and the proxy can later verify that credential without relying on a live turn row.

*Call graph*: 1 external calls (sign_token).


##### `ProbeTokenCodec.from_proxy_auth`  (lines 165–181)

```
def from_proxy_auth(self, header: str) -> ProbeToken
```

**Purpose**: Verifies a proxy authentication header for a probe and reconstructs the trusted ProbeToken. It refuses run tokens, forged tokens, malformed IDs, and wrong token domains.

**Data flow**: It receives a Basic proxy authorization header. It extracts the username, verifies the signed payload, checks that the payload says it is a probe token, parses all UUIDs and the expiry time, and returns a ProbeToken. Any invalid step becomes a clear invalid signed probe token error.

**Call relations**: This follows the same pattern as the run-token decoder but for probe traffic. It calls _basic_username to get the credential and then uses the shared token-signing verifier to prove it is genuine.

*Call graph*: calls 1 internal fn (_basic_username); 3 external calls (__init__, verify_token, UUID).


##### `sandbox_handle_id`  (lines 256–261)

```
def sandbox_handle_id(backend: str, value: str) -> str | None
```

**Purpose**: Pulls the raw sandbox ID out of a stored handle only if it belongs to the requested backend. This prevents one sandbox provider from trying to resume another provider's container.

**Data flow**: It receives a backend name and a stored handle string. If the string starts with the expected backend prefix and separator, it returns the ID after the prefix. Otherwise it returns None.

**Call relations**: Carrier implementations and resume logic can use this when reading a durable sandbox handle. It acts as a small safety check during attach-or-resume decisions.


##### `Carrier.create`  (lines 299–299)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Defines the contract for creating or attaching to a sandbox for a conversation. A concrete carrier, such as Docker or a remote provider, must implement this.

**Data flow**: It receives a SandboxSpec describing the conversation, image, workspace, proxy, token, and related settings. The carrier turns that specification into a live sandbox and returns a SandboxHandle that refers to it.

**Call relations**: This is part of the Carrier protocol, so this file does not provide the actual backend work. Higher-level sandbox-opening code depends on this shape, while each carrier supplies the real implementation.


##### `Carrier.attach`  (lines 301–307)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Defines how to reconnect to an already-existing sandbox without creating a new one. This matters for read-only access or after a server restart, where silently provisioning a fresh empty sandbox would be wrong.

**Data flow**: It receives a SandboxSpec, usually with a resume ID. The carrier checks whether that sandbox exists and is reachable. It returns a SandboxHandle if it can attach, or None if the sandbox is absent.

**Call relations**: This is a protocol method implemented by carriers. File-browsing or resume flows can ask for an existing sandbox and get a truthful absent answer instead of accidentally starting a new one.


##### `Carrier.exec`  (lines 309–311)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Defines how to run one command inside a sandbox. Tools use this indirectly when they ask a SandboxSession to run shell, Python, or file-system helper commands.

**Data flow**: It receives a sandbox handle, a command expressed as separate argument strings, and a timeout. The carrier runs the command inside the sandbox and returns an ExecResult containing standard output, standard error, exit code, and timeout information.

**Call relations**: sbxfs_file_op calls this to run the sandbox file helper. SandboxSession methods also rely on carrier implementations of this method to execute bash, sh, Python, and maintenance commands.

*Call graph*: called by 1 (sbxfs_file_op).


##### `Carrier.write`  (lines 313–325)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Defines how to copy bytes from the host into a file under the sandbox workspace. It is designed to avoid unsafe shell redirects and to keep writes confined to /workspace.

**Data flow**: It receives a sandbox handle, an absolute workspace path, and raw bytes. The carrier writes those bytes to the target path, creating parent directories when needed, and reports failure by raising an error.

**Call relations**: SandboxSession.write_file prepares a safe workspace path and then hands the actual transfer to the carrier. Each backend implements the safest way to perform the write in its own environment.


##### `Carrier.read`  (lines 327–338)

```
def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines how to stream a file out of the sandbox workspace without loading the whole file into memory. This is important for large files produced by tools.

**Data flow**: It receives a sandbox handle and an absolute workspace path. The carrier returns an asynchronous stream of byte chunks from that file, or raises an appropriate error if the path cannot be read.

**Call relations**: SandboxSession.read_file scopes the requested path and then delegates the actual copy-out to the carrier. Different carriers may stream through different mechanisms, but callers see one common interface.


##### `Carrier.dial`  (lines 340–349)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Defines how outside code can reach a service running on a port inside the sandbox. For example, a browser debugger or local preview server may need an externally dialable address.

**Data flow**: It receives a sandbox handle and an internal port number. The carrier returns a DialTarget with the host, whether TLS is used, and any required headers. If the sandbox cannot be reached, it raises SandboxUnreachable.

**Call relations**: SandboxSession.dial passes port requests to this protocol method. Carrier implementations hide the differences between local, Docker, and remote networking.


##### `Carrier.file_op`  (lines 351–361)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Defines a bounded, structured way to run file operations inside the sandbox, such as reads, writes, edits, search, and change listing. It keeps large file work inside the sandbox instead of pulling everything to the host.

**Data flow**: It receives a sandbox handle, an operation name, and a dictionary of parameters. The carrier runs the operation against the workspace and returns a parsed JSON-like dictionary. Recoverable tool errors become ValueError, while unexpected failures become RuntimeError.

**Call relations**: SandboxSession.run_sbxfs prepares safe parameters and then calls this method. Carriers that use the baked-in sbxfs command can share sbxfs_file_op as their implementation.


##### `sbxfs_file_op`  (lines 364–387)

```
async def sbxfs_file_op(carrier: Carrier, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Implements Carrier.file_op for carriers that have the sbxfs command installed in the sandbox. sbxfs is the in-sandbox helper that performs safe, bounded file operations.

**Data flow**: It receives a carrier, handle, operation name, and parameters. It serializes the parameters to JSON, runs sbxfs through Carrier.exec, trims and parses stdout as JSON, checks that the result is an object, converts reported tool errors into ValueError, and returns the parsed object on success.

**Call relations**: Carrier implementations can reuse this helper instead of repeating the command-running and JSON-parsing logic. It depends on Carrier.exec to do the actual in-sandbox execution.

*Call graph*: calls 1 internal fn (exec); 2 external calls (dumps, loads).


##### `workspace_path`  (lines 390–398)

```
def workspace_path(path: str) -> str
```

**Purpose**: Turns a tool-supplied path into a safe absolute path under /workspace. It rejects attempts to climb out of the workspace with path tricks like '..'.

**Data flow**: It receives a path string that may be relative or absolute. It places relative paths under /workspace, normalizes dot and dot-dot path parts, checks that the final result is still inside /workspace, and returns the safe absolute path. If the path escapes, it raises ValueError.

**Call relations**: SandboxSession file-related methods call this before touching files. It relies on _resolve_parts for the path cleanup, then hands safe paths to carrier reads, writes, file existence checks, and sbxfs operations.

*Call graph*: calls 1 internal fn (_resolve_parts); called by 4 (file_exists, read_file, run_sbxfs, write_file); 1 external calls (PurePosixPath).


##### `_resolve_parts`  (lines 401–410)

```
def _resolve_parts(parts: tuple[str, ...]) -> list[str]
```

**Purpose**: Normalizes path pieces while preventing movement above the workspace root. It is the small helper that makes workspace_path's escape check strict and predictable.

**Data flow**: It receives path parts, walks through them like a stack, ignores empty and current-directory pieces, pops one level for '..', and raises an error if '..' would climb too high. It returns the cleaned list of path parts.

**Call relations**: workspace_path calls this during path validation. Callers do not normally use it directly; it exists to keep the path-normalizing rule in one place.

*Call graph*: called by 1 (workspace_path).


##### `SandboxSession.authorize`  (lines 422–447)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> 'SandboxSession'
```

**Purpose**: Creates a new SandboxSession with updated network authority for a specific execution context. It rewrites proxy environment variables so sandbox traffic carries the right run token.

**Data flow**: It receives a new run token, a set of environment variable names to remove, and additional environment values. It checks that the current handle has a run token and that proxy environment values contain it, replaces the old token with the new one in proxy settings, removes cleared variables, adds new environment values, and returns a new SandboxSession with a new SandboxHandle.

**Call relations**: This is used when a shared sandbox must run under fresh per-turn or per-member authority. It does not create a container; it builds a safer session view over the same carrier and container.

*Call graph*: 2 external calls (__init__, __init__).


##### `SandboxSession.bash`  (lines 449–454)

```
async def bash(self, command: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a command string inside the sandbox using bash. This is the familiar shell entry point for tools that need normal command-line behavior.

**Data flow**: It receives a command string and an optional timeout. It builds a bash command, chooses the default timeout when none is supplied, sends it to the carrier's exec method, and returns the ExecResult.

**Call relations**: The sandbox Chrome extension calls this when leasing browser-related resources. More generally, it is one of the main conveniences that turns a carrier's low-level exec ability into a tool-friendly session method.

*Call graph*: called by 1 (lease).


##### `SandboxSession.sh`  (lines 456–464)

```
async def sh(self, script: str, *args: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a POSIX sh script inside the sandbox with arguments passed safely as separate command arguments. This avoids mixing user data into the script text through shell quoting.

**Data flow**: It receives a script, zero or more argument strings, and an optional timeout. It builds an sh -c command where the extra values become positional parameters, sends it to the carrier, and returns the ExecResult.

**Call relations**: This is a convenience wrapper over Carrier.exec. It is useful when code wants shell scripting but still wants arguments to remain separate pieces of data.


##### `SandboxSession.python`  (lines 466–480)

```
async def python(self, program: str, *args: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a Python program inside the sandbox with the containment guard importable and Python isolated from workspace-planted modules. This is used for safer helper programs that need to inspect or write paths.

**Data flow**: It receives Python source text, optional arguments, and an optional timeout. It prepends bootstrap code that finds the sandbox guard beside sbxfs, runs python3 in isolated mode, passes arguments separately, and returns the carrier's ExecResult.

**Call relations**: This wraps Carrier.exec for Python helper programs. The bootstrap and isolated mode are important because the workspace is writable by the agent, so imports must not be hijacked by files placed there.


##### `SandboxSession.write_file`  (lines 482–483)

```
async def write_file(self, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes to a file in the sandbox workspace after first making sure the path cannot escape /workspace.

**Data flow**: It receives a path and byte content. It converts the path with workspace_path, then asks the carrier to write those bytes to that safe location. It returns nothing if the write succeeds.

**Call relations**: The skill runtime calls this when mounting skill content into the sandbox. It is the session-level wrapper that combines path safety with the carrier's backend-specific write operation.

*Call graph*: calls 1 internal fn (workspace_path); called by 1 (mount_skill).


##### `SandboxSession.ensure_tool_output_dir`  (lines 485–507)

```
async def ensure_tool_output_dir(self) -> bool
```

**Purpose**: Makes sure the engine's private .tool-output directory exists in the workspace. If a file or broken link is squatting on that exact name, it removes it and recreates the directory.

**Data flow**: It runs a small shell script inside the sandbox against the fixed tool-output path. The script exits cleanly if the directory already exists, removes a non-directory occupant when present, creates the directory, and prints a marker if it reclaimed a squatter. The method raises OSError on failure and returns true only when reclaiming happened.

**Call relations**: This uses Carrier.exec through the session handle. It protects later output offloading from being blocked by a workspace entry with the reserved engine directory name.


##### `SandboxSession.file_exists`  (lines 509–514)

```
async def file_exists(self, path: str) -> bool
```

**Purpose**: Checks whether a regular file exists at a safe path inside the workspace. It is a small helper for code that needs a yes-or-no answer before another file action.

**Data flow**: It receives a path, converts it to a safe /workspace path, runs test -f inside the sandbox, and returns true when the command exits successfully. It returns false for missing paths or non-regular files.

**Call relations**: It calls workspace_path before using Carrier.exec, so even this simple existence check follows the same workspace boundary as reads and writes.

*Call graph*: calls 1 internal fn (workspace_path).


##### `SandboxSession.run_sbxfs`  (lines 516–527)

```
async def run_sbxfs(self, op: str, args: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one structured sbxfs file operation through the carrier after adding the correct workspace boundary. This gives tools a safe way to do file reads, edits, searches, and similar operations.

**Data flow**: It receives an operation name and argument dictionary. It copies the arguments, scopes a string path argument with workspace_path when present, sets the workspace root to /workspace, calls the carrier's file_op method, and returns the resulting dictionary.

**Call relations**: This is the session-level entry to Carrier.file_op. It decides what workspace the operation may touch, while the carrier decides how the operation is actually performed inside the sandbox.

*Call graph*: calls 1 internal fn (workspace_path).


##### `SandboxSession.read_file`  (lines 529–532)

```
def read_file(self, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file's bytes out of the sandbox workspace after validating the path. It avoids loading the whole file into the host process at once.

**Data flow**: It receives a path, converts it to a safe /workspace path, and returns the asynchronous byte stream provided by the carrier. The caller can then consume the file chunk by chunk.

**Call relations**: It calls workspace_path and then delegates to Carrier.read. This mirrors write_file: the session enforces the boundary, and the carrier performs the backend-specific transfer.

*Call graph*: calls 1 internal fn (workspace_path).


##### `SandboxSession.dial`  (lines 534–537)

```
async def dial(self, port: int) -> DialTarget
```

**Purpose**: Asks the carrier for an externally reachable address for a service running on a sandbox port. Callers use this to connect to things started inside the sandbox.

**Data flow**: It receives a port number. It passes the current sandbox handle and port to the carrier, then returns the DialTarget containing host, TLS choice, and any required headers.

**Call relations**: The sandbox Chrome extension calls this while leasing browser connectivity. The method keeps callers independent of each carrier's networking details.

*Call graph*: called by 1 (lease).


### `core/src/ufo/workspace_changes.py`

`domain_logic` · `turn-end recording and later change lookup`

A conversation may edit files in a temporary workspace, and later the user interface or another part of the system needs to show what changed. This file creates a durable snapshot of those changes. Instead of guessing from tool calls, it asks the sandbox file service what git sees as changed, then stores that answer in the database.

The key idea is that the workspace is shared. A subagent may work inside its parent conversation's sandbox, so the stored answer belongs to the sandbox-owning conversation, not necessarily the individual turn or message stream. The file also treats this information as a “projection”: a saved view of the current workspace state. If scanning fails, the system logs the failure but does not fail the already-finished turn. A stale answer is considered better than breaking the user flow.

The data models set safety limits: each changed path and patch has a maximum size, and only a limited number of changes are stored. `WorkspaceChangeRecorder` performs the end-of-turn refresh: scan the sandbox, validate the result, and upsert it into the database. `recorded_workspace_changes` reads the latest saved scan, resolving subagents back to their owning conversation when needed. If nothing has been recorded, it returns an explicit “nothing changed” value.

#### Function details

##### `WorkspaceChangeRecorder.record`  (lines 67–76)

```
async def record(self) -> None
```

**Purpose**: This is the safe public entry point for refreshing the saved workspace-change snapshot after a turn finishes. It tries to scan the sandbox and store the result, but if anything goes wrong it logs the problem instead of disrupting a turn that has already completed.

**Data flow**: It starts with the recorder's sandbox, workspace id, and conversation id. It asks `_scan` for the current list of changed files, then passes that result to `_store` so it can be saved in the database. If scanning, validation, or storing fails, nothing new is written and a log entry records the error details.

**Call relations**: This method coordinates the two private steps in this file: `_scan` gathers the change report, and `_store` persists it. When either step raises an error, it hands the details to `ufo.o11y.log` so operators can see that the refresh failed without making the completed turn fail.

*Call graph*: calls 2 internal fn (_scan, _store); 1 external calls (log).


##### `WorkspaceChangeRecorder._scan`  (lines 78–83)

```
async def _scan(self) -> WorkspaceChanges
```

**Purpose**: This asks the sandbox file service for the current git-style view of changed files. It also checks that the sandbox returned data in the exact shape this system expects.

**Data flow**: It sends the sandbox a `changes` request with no extra options. The sandbox returns raw data describing changed paths, patches, and whether the answer was shortened. `_scan` validates that raw data as a `WorkspaceChanges` object; if the data is malformed, it turns the validation problem into a clear runtime error.

**Call relations**: `record` calls this first when refreshing the saved projection. Its output is meant to go straight into `_store`, but only after validation has proved that the sandbox response is safe and well-formed.

*Call graph*: called by 1 (record).


##### `WorkspaceChangeRecorder._store`  (lines 85–98)

```
async def _store(self, scanned: WorkspaceChanges) -> None
```

**Purpose**: This saves the latest scanned workspace changes into the database. If a scan for the same workspace and conversation already exists, it replaces the old scan with the new one.

**Data flow**: It receives a validated `WorkspaceChanges` object. Inside a workspace database transaction, it converts the object into JSON-friendly data, builds an insert statement for the `conversation_change` table, and uses an “upsert” operation: insert if missing, update if already present. The database is changed, but the function returns no value.

**Call relations**: `record` calls this after `_scan` succeeds. It uses `workspace_tx` to get a database connection and picks the right insert helper for PostgreSQL or SQLite, so the same higher-level behavior works in both database engines.

*Call graph*: called by 1 (record); 2 external calls (model_dump, workspace_tx).


##### `recorded_workspace_changes`  (lines 101–125)

```
async def recorded_workspace_changes(conversation_id: UUID) -> WorkspaceChanges
```

**Purpose**: This reads the most recently stored workspace-change snapshot for a conversation. It hides the shared-workspace detail by resolving a subagent conversation back to the parent conversation that owns the sandbox.

**Data flow**: It receives a conversation id. First it looks up the conversation's sandbox owner: either `sandbox_conversation_id` if present, or the conversation's own id. If there is no such conversation, it returns the shared `NOTHING_CHANGED` value. Otherwise it looks for a saved scan for that owner conversation, validates it as `WorkspaceChanges`, and returns it; if no scan exists, it also returns `NOTHING_CHANGED`.

**Call relations**: This function is the read side of the workflow. `WorkspaceChangeRecorder._store` writes the latest scan, and later callers use `recorded_workspace_changes` to fetch it for display or reporting. It uses SQLAlchemy queries inside `workspace_tx` so the lookup happens through the same workspace database layer as the writer.

*Call graph*: 2 external calls (select, workspace_tx).

## 📊 State Registers Touched

- `reg-effective-config` — The deployment’s active settings, such as enabled services, limits, paths, providers, and safety options.
- `reg-agent-directory` — The saved assistants in each workspace and their settings, such as model behavior, sandbox size, and internet access.
- `reg-credential-vault` — Encrypted workspace secrets and short-lived brokered credentials used without exposing raw secrets to tools.
- `reg-connection-grants` — Connected third-party accounts and permissions saying which agents may use which external accounts.
- `reg-sandbox-workspace` — The per-conversation isolated workbench, including its handle, files, execution backend, and recorded file changes.
- `reg-sandbox-network-policy` — The per-run rules and proxy state that decide what sandboxed code may reach on the internet and when secrets may be injected.
- `reg-browser-session` — The leased browser instance for a turn, including tabs, page state, downloads, dialogs, and provider connection details.
- `reg-accounting-ledger` — Usage, cost, spend limits, prepaid balances, billing exports, and price versions for workspace spending.
- `reg-artifact-store` — Generated files, previews, blobs, and signed shared-artifact links that outlive a single message.
- `reg-hosted-site-state` — Hosted sandbox sites, their public addresses, generations, ports, visibility, and unhosting status.
- `reg-observability-context` — Trace IDs, metrics, logs, and sanitized operational events used to understand work across services and turns.
