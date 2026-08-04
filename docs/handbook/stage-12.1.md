# Egress Proxy and Credential Injection  `stage-12.1`

This stage is the system’s guarded exit door for sandboxed agent work. It runs during the main work loop, whenever an agent inside a workspace tries to contact the outside network. Instead of letting the agent call any website or service directly, the request must pass through the egress proxy.

The rules file builds the rulebook. It looks at workspace settings, declared permissions, available credentials, connector grants, model access, and storage options. From these, it creates simple decisions such as “this host is allowed,” “this host is blocked,” “this destination may receive this secret,” or “this call should be counted for billing.”

The server file is the doorway that enforces that rulebook. It checks each outgoing request, denies unsafe hosts, and only injects sealed secrets when the destination is approved. For some connector traffic, it forwards the request through a grant broker, like a trusted middleman. It also records usage for audits and metering, then cleans up proxy resources when the sandbox stops.

## Files in this stage

### Proxy Enforcement and Rules
Implements the sandbox egress proxy that enforces outbound access decisions, credential injection, brokered forwarding, metering, and the rule derivation it relies on.

### `core/src/ufo/sandbox/proxy/server.py`

`io_transport` · `startup, request handling, shutdown`

Agent code runs in a sandbox, so it cannot be allowed to freely call the internet or see raw credentials. This file is the gatekeeper. Think of it like a supervised mailroom: every outgoing envelope must show a valid badge, the mailroom checks where it is going, may add a secret stamp that the sender never gets to see, and records that the envelope was sent.

The proxy receives HTTP CONNECT requests from sandboxed processes. It reads a run token from the proxy authorization header, checks that the referenced turn is still running, and resolves the rules for that turn's agent and workspace. These rules say which hosts are allowed, whether normal internet access is enabled, whether a credential should be injected, whether a request should be sent through a broker, and what should be counted.

If the host needs no inspection, the proxy opens a plain tunnel and simply relays encrypted bytes. If a secret or broker rule applies, it performs a controlled man-in-the-middle step: it presents a temporary certificate trusted inside the sandbox, reads the decrypted HTTP request, swaps only the matching sentinel value for the real credential or forwards through a broker, then opens its own verified TLS connection upstream. It also parses model responses when needed to recover token usage and writes egress and token records asynchronously so network traffic is not slowed by accounting.

#### Function details

##### `_ContentDecoder.unconsumed_tail`  (lines 138–138)

```
def unconsumed_tail(self) -> bytes
```

**Purpose**: This protocol property describes the extra compressed bytes a decompressor has not consumed yet. It lets the proxy work with any decoder object that behaves like Python's zlib decoder.

**Data flow**: A decoder object already holding compressed input exposes any leftover bytes through this property. Nothing is changed by reading it; callers use the returned bytes as the next pending input.

**Call relations**: The token-usage parser relies on this shape while decoding compressed model responses. It is part of the small contract used by HttpTokenUsage._decode rather than a concrete implementation.


##### `_ContentDecoder.decompress`  (lines 140–140)

```
def decompress(self, data: bytes, max_length: int=0) -> bytes
```

**Purpose**: This protocol method describes how compressed response bytes are turned into ordinary body bytes. The optional limit prevents the parser from expanding a response without bound.

**Data flow**: Compressed bytes and an optional maximum output size go in. The decoder returns decompressed bytes and keeps any not-yet-read input available through unconsumed_tail.

**Call relations**: HttpTokenUsage._decode calls this behavior when a model response uses gzip or deflate compression. The protocol keeps that code independent of the exact decoder class.


##### `_ContentDecoder.flush`  (lines 142–142)

```
def flush(self) -> bytes
```

**Purpose**: This protocol method describes how to ask a decompressor for any final bytes after the response has ended. It is needed so token usage is not missed at the end of a compressed stream.

**Data flow**: The decoder's current internal state goes in implicitly. It returns remaining decompressed bytes and may mark the decoder as finished.

**Call relations**: HttpTokenUsage._finish_decoder uses this at the end of parsing. That final flush happens before HttpTokenUsage.usage reports what it found.


##### `generate_ca`  (lines 148–171)

```
async def generate_ca() -> tuple[str, str]
```

**Purpose**: This creates a temporary certificate authority, which is a root certificate the sandbox can trust. The proxy uses it later to mint per-host certificates when it needs to inspect HTTPS safely.

**Data flow**: No caller-supplied data is needed. The function creates temporary certificate and key files with OpenSSL, reads them back as text, and returns the certificate plus private key.

**Call relations**: Startup code calls this before constructing or launching the proxy. It delegates the actual command execution to _openssl and uses a temporary directory so the intermediate files disappear.

*Call graph*: calls 1 internal fn (_openssl); 2 external calls (Path, TemporaryDirectory).


##### `_openssl`  (lines 174–180)

```
async def _openssl(*argv: str) -> None
```

**Purpose**: This is the small wrapper that runs the external openssl command-line tool. It keeps certificate generation out of Python crypto libraries and turns command failures into Python errors.

**Data flow**: OpenSSL arguments go in. The function starts the process, waits for it, ignores normal output, and either returns nothing on success or raises an error containing OpenSSL's failure text.

**Call relations**: generate_ca, EgressProxy.start, and EgressProxy._leaf_context all use this whenever they need keys, certificate requests, or signed certificates.

*Call graph*: called by 3 (_leaf_context, start, generate_ca); 1 external calls (create_subprocess_exec).


##### `PerAgentRules.resolve`  (lines 206–230)

```
async def resolve(self, run: RunToken | None) -> tuple[Rule, ...]
```

**Purpose**: This builds the network rule list for one run token. It combines the base workspace rules with the specific agent's internet setting, stored credentials, active grants, and command-line credentials.

**Data flow**: A run token, or no token, goes in. With no token or no matching turn, only the base rules come out. With a valid turn, it reads the turn's agent, enters the workspace and agent context, derives any credential and grant rules, and returns the complete rule tuple.

**Call relations**: EgressProxy receives this function as its rule resolver. Before a connection is allowed through, EgressProxy._rules_for calls it, and it first asks PerAgentRules._turn_of which agent and policy apply.

*Call graph*: calls 1 internal fn (_turn_of); 5 external calls (agent, derive_cli_rules, derive_credential_rules, derive_grant_rules, ws).


##### `PerAgentRules._turn_of`  (lines 232–256)

```
async def _turn_of(self, run: RunToken) -> tuple[UUID, bool] | None
```

**Purpose**: This looks up which agent owns a turn and whether that agent had internet access enabled. It is the database read that ties a run token to the right agent policy.

**Data flow**: A run token supplies workspace and turn identifiers. The function queries the workspace database for the matching turn and agent, then returns the agent id plus the internet-access flag, or nothing if no matching row exists.

**Call relations**: PerAgentRules.resolve calls this before adding agent-specific rules. If it finds no turn, resolution falls back to base rules instead of guessing.

*Call graph*: called by 1 (resolve); 2 external calls (select, workspace_tx).


##### `PerAgentRules.turn_live`  (lines 258–275)

```
async def turn_live(self, run: RunToken) -> bool
```

**Purpose**: This answers the security question: is the turn named by this token still running? It prevents old or ended tokens from continuing to reach hosts or draw credentials.

**Data flow**: A run token goes in. The function reads the turn status from the workspace database and returns true only when the status is RUNNING.

**Call relations**: This is meant to be supplied to EgressProxy as its authorizer. EgressProxy._turn_authorized calls it for each connection, separate from the cached rule lookup, so liveness is checked fresh.

*Call graph*: 3 external calls (select, workspace_tx, ws).


##### `EgressProxy.start`  (lines 303–319)

```
async def start(self, bind_host: str=PROXY_BIND_HOST, port: int=0, public_url: str | None=None) -> ProxyEndpoint
```

**Purpose**: This starts the proxy listener and prepares the certificate files it needs for HTTPS interception. It returns the endpoint information that sandbox processes use to configure their proxy settings.

**Data flow**: A bind host, port, and optional public URL go in. The function creates a work directory, writes the proxy certificate authority files, generates a reusable leaf key, starts an asyncio server, and returns the bound port plus CA certificate.

**Call relations**: Deployment or tests call this during startup. Later incoming sockets are handed to EgressProxy._handle by the asyncio server, and EgressProxy.stop cleans up what start created.

*Call graph*: calls 1 internal fn (_openssl); 4 external calls (__init__, start_server, Path, TemporaryDirectory).


##### `EgressProxy.stop`  (lines 321–358)

```
async def stop(self, graceful_shutdown_seconds: int=0) -> None
```

**Purpose**: This shuts the proxy down without hanging forever. It stops accepting new connections, gives current work a bounded chance to finish, cancels leftovers, flushes metering, and removes temporary certificate files.

**Data flow**: A grace period in seconds goes in. The function closes the server, waits for active connection tasks, cancels unresolved rule work if needed, stops the metering worker, and clears local resources.

**Call relations**: Shutdown code calls this after the proxy has been running. It coordinates with tasks created by EgressProxy._handle, EgressProxy._rules_for, and EgressProxy._enqueue_meter.

*Call graph*: 3 external calls (gather, wait, monotonic).


##### `EgressProxy._handle`  (lines 360–466)

```
async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None
```

**Purpose**: This is the main per-connection decision point. It reads one proxy CONNECT request, authenticates the run, checks capacity and rules, then chooses either an opaque tunnel or an inspected HTTPS path.

**Data flow**: A client stream reader and writer go in. The function reads the request headers, extracts the target host and proxy authorization token, validates the token and live turn, resolves rules, checks host permission, optionally resolves public internet hosts, and then either calls _tunnel or _mitm. It updates active connection counters while the connection is open.

**Call relations**: The asyncio server created by EgressProxy.start calls this for every new socket. It hands off detailed work to _turn_authorized, _rules_for, _tunnel, _mitm, _read_request_head, and _respond.

*Call graph*: calls 7 internal fn (_mitm, _rules_for, _run_token, _tunnel, _turn_authorized, _read_request_head, _respond); 3 external calls (__init__, close, current_task).


##### `EgressProxy._turn_authorized`  (lines 468–481)

```
async def _turn_authorized(self, run: RunToken) -> bool
```

**Purpose**: This wraps the turn-liveness check and logs failures in one place. It keeps authorization errors visible while letting the caller return a generic service-unavailable response.

**Data flow**: A run token goes in. The configured authorizer is called; its true or false result comes back unchanged. If the authorizer crashes, the function logs the workspace, turn, and error class, then raises the error again.

**Call relations**: EgressProxy._handle calls this before resolving rules or connecting upstream. The actual policy check is supplied from outside, commonly PerAgentRules.turn_live.

*Call graph*: called by 1 (_handle); 1 external calls (log_error).


##### `EgressProxy._rules_for`  (lines 483–500)

```
async def _rules_for(self, run: RunToken | None) -> tuple[Rule, ...]
```

**Purpose**: This returns the rule set for a run while avoiding repeated expensive lookups. It caches successful results briefly and lets simultaneous requests for the same run share one lookup.

**Data flow**: A run token, or no token, goes in. With no token it directly calls the resolver. With a token it checks the cache, starts or joins a background resolution task on a miss, and returns the resolved rules.

**Call relations**: EgressProxy._handle calls this after authorization. It delegates cache misses to EgressProxy._resolve_rules and shields the shared task so one cancelled connection does not cancel every waiter.

*Call graph*: calls 1 internal fn (_resolve_rules); called by 1 (_handle); 3 external calls (create_task, shield, monotonic).


##### `EgressProxy._resolve_rules`  (lines 502–523)

```
async def _resolve_rules(self, run: RunToken) -> tuple[Rule, ...]
```

**Purpose**: This performs the actual rule resolution behind the cache. It records failures once and stores successful results with an expiry time.

**Data flow**: A run token goes in. The configured resolver produces rules; on success they are stored in the bounded cache and returned. On failure the error is logged and re-raised, and the in-flight task entry is removed either way.

**Call relations**: Only EgressProxy._rules_for starts this task. The task has _read_fault attached so abandoned failures do not leak through asyncio's default logging.

*Call graph*: called by 1 (_rules_for); 4 external calls (__init__, current_task, monotonic, log_error).


##### `EgressProxy._run_token`  (lines 525–531)

```
def _run_token(self, proxy_auth: str) -> RunToken | None
```

**Purpose**: This turns the Proxy-Authorization header into a trusted run token, or rejects it as absent or invalid. It is the first step from raw wire text to project identity.

**Data flow**: A header value string goes in. If it is empty or cannot be decoded by the run token codec, the function returns None; otherwise it returns a RunToken object.

**Call relations**: EgressProxy._handle calls this after parsing the CONNECT headers. A None result causes the connection to be denied before any host rules or secrets are considered.

*Call graph*: called by 1 (_handle).


##### `EgressProxy._resolve_public_address`  (lines 533–565)

```
async def _resolve_public_address(self, host: str, port: int) -> str
```

**Purpose**: This checks and resolves a host for general internet access. It only allows globally routable IPv4 addresses, blocking private networks, IPv6, multicast, and malformed names.

**Data flow**: A host and port go in, though the port is not used for DNS itself. The function accepts literal IPv4 addresses or resolves DNS A records, verifies all resulting addresses are public, and returns the first allowed address as a string.

**Call relations**: EgressProxy._handle uses this when a host is not exactly scoped but internet access is allowed. Callers may replace it with a custom resolver through resolve_public.

*Call graph*: 1 external calls (IPv4Address).


##### `EgressProxy._tunnel`  (lines 567–594)

```
async def _tunnel(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int, run: RunToken, rules: tuple[Rule, ...], connect_host: str) -> None
```

**Purpose**: This opens a plain TCP tunnel to an allowed host when the proxy does not need to inspect or change the HTTPS traffic. The sandbox and upstream server keep their encryption end-to-end.

**Data flow**: Client streams, destination details, the run token, rules, and the already chosen connect host go in. The function opens the upstream connection, replies with CONNECT success, records metering if configured, and relays bytes both ways.

**Call relations**: EgressProxy._handle calls this when there are no injection or broker forwarding rules for the host. It uses _relay for byte copying and _respond if the upstream connection fails.

*Call graph*: calls 4 internal fn (_meter, _meter_ledger, _relay, _respond); called by 1 (_handle); 4 external calls (drain, write, open_connection, wait_for).


##### `EgressProxy._mitm`  (lines 596–656)

```
async def _mitm(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int, injections: list[InjectionRule], forwards: list[ForwardRule], run: RunToken, rules: tuple[Rule,
```

**Purpose**: This handles allowed HTTPS traffic that must be inspected because a credential may need to be injected or a grant broker may need to receive the request. It decrypts only inside the proxy boundary and re-encrypts to the real upstream server.

**Data flow**: Client streams, host, port, applicable injection and forwarding rules, the run token, and all rules go in. The function creates or reuses a host certificate, upgrades the client side to TLS, reads one HTTP request, forwards through a broker if a sentinel matches, or otherwise connects upstream, rewrites headers, relays the response, and optionally accumulates token usage.

**Call relations**: EgressProxy._handle calls this for hosts with InjectionRule or ForwardRule entries. It hands broker cases to _forward_broker, certificate work to _leaf_context, header swapping to _inject, and streaming to _relay.

*Call graph*: calls 11 internal fn (_forward_broker, _leaf_context, _meter, _meter_ledger, _meter_tokens, _forward_match, _inject, _read_request_head, _relay, _respond (+1 more)); called by 1 (_handle); 3 external calls (__init__, open_connection, wait_for).


##### `EgressProxy._forward_broker`  (lines 658–702)

```
async def _forward_broker(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, rule: ForwardRule, request: tuple[bytes, list[bytes]], host: str, run: RunToken, rules: tuple[
```

**Purpose**: This sends one sentinel-marked request through a grant broker instead of directly to the provider. That keeps the real account credential on the broker side, outside the sandbox and outside this proxy's outgoing request.

**Data flow**: The decrypted client request, broker rule, host, run token, and rules go in. The function reads a bounded full request body, records metering, calls the broker with cleaned headers and URL, and writes the broker's reconstructed HTTP response back to the client. If the body is unacceptable or the broker fails, it writes a clear refusal.

**Call relations**: EgressProxy._mitm calls this when _forward_match identifies a matching ForwardRule. It relies on _read_request_body, _forward_headers, _forward_response_bytes, _respond, and _drain_refused_body.

*Call graph*: calls 7 internal fn (_meter, _meter_ledger, _drain_refused_body, _forward_headers, _forward_response_bytes, _read_request_body, _respond); called by 1 (_mitm); 3 external calls (drain, write, log).


##### `EgressProxy._leaf_context`  (lines 704–749)

```
async def _leaf_context(self, host: str) -> ssl.SSLContext
```

**Purpose**: This creates and caches a TLS server certificate for one hostname. The certificate is signed by the proxy's temporary certificate authority so the sandbox can trust it during controlled inspection.

**Data flow**: A host name goes in. The function returns a cached SSL context if one exists; otherwise it writes certificate request files, asks OpenSSL to sign a host certificate, builds a server-side SSL context, caches it, and returns it.

**Call relations**: EgressProxy._mitm calls this before upgrading a CONNECT tunnel into decrypted HTTPS. It uses the CA material prepared by EgressProxy.start and the _openssl helper.

*Call graph*: calls 1 internal fn (_openssl); called by 1 (_mitm); 2 external calls (Path, SSLContext).


##### `EgressProxy._meter`  (lines 751–754)

```
def _meter(self, host: str, rules: tuple[Rule, ...]) -> None
```

**Purpose**: This emits an in-process metric when a rule says the host should be counted. It is the fast, immediate part of egress observability.

**Data flow**: A host and rule list go in. For each matching MeterRule, the function emits a sandbox egress metric tagged with the host and counting dimension. It returns nothing and does not write the database.

**Call relations**: The tunnel, direct MITM, and broker-forward paths call this once they have an admitted request or connection. Database-backed accounting is handled separately by _meter_ledger and _meter_tokens.

*Call graph*: called by 3 (_forward_broker, _mitm, _tunnel); 1 external calls (emit_metric).


##### `EgressProxy._meter_ledger`  (lines 756–762)

```
async def _meter_ledger(self, host: str, run: RunToken, rules: tuple[Rule, ...]) -> None
```

**Purpose**: This queues a database accounting record for ordinary egress requests. It avoids writing the ledger directly on the network relay path.

**Data flow**: A host, run token, and rules go in. If a non-token MeterRule applies to the host, the function wraps the run token in an egress meter record and puts it on the metering queue; otherwise it does nothing.

**Call relations**: EgressProxy._tunnel, EgressProxy._mitm, and EgressProxy._forward_broker call this after a permitted egress event. It hands the actual write to _enqueue_meter and the background metering loop.

*Call graph*: calls 1 internal fn (_enqueue_meter); called by 3 (_forward_broker, _mitm, _tunnel); 1 external calls (__init__).


##### `EgressProxy._meter_tokens`  (lines 764–770)

```
async def _meter_tokens(self, run: RunToken, accumulator: 'HttpTokenUsage') -> None
```

**Purpose**: This queues accounting for model token usage found in a proxied response. It keeps sandbox model usage separate from other turn-loop model billing.

**Data flow**: A run token and HttpTokenUsage accumulator go in. The function asks the accumulator for parsed model and token counts; if none are found it logs that absence, and if found it queues a token meter record.

**Call relations**: EgressProxy._mitm calls this after relaying a metered model response. The queued record is later processed by _meter_loop and _write_meter_batch.

*Call graph*: calls 1 internal fn (_enqueue_meter); called by 1 (_mitm); 2 external calls (__init__, log).


##### `EgressProxy._enqueue_meter`  (lines 772–779)

```
async def _enqueue_meter(self, record: _MeterRecord) -> None
```

**Purpose**: This puts an accounting item onto the background queue and starts the writer task if needed. It prevents request handling from waiting on database writes.

**Data flow**: One metering record goes in. The function ensures the metering worker exists and is healthy, then enqueues the record, waiting only if the queue is full.

**Call relations**: _meter_ledger and _meter_tokens both call this. It starts EgressProxy._meter_loop, which batches and writes records.

*Call graph*: calls 1 internal fn (_meter_loop); called by 2 (_meter_ledger, _meter_tokens); 1 external calls (create_task).


##### `EgressProxy._meter_loop`  (lines 781–813)

```
async def _meter_loop(self) -> None
```

**Purpose**: This is the background worker that batches metering records. Batching reduces database overhead when many proxy events happen close together.

**Data flow**: Records arrive through the proxy's queue. The loop takes the first item, briefly waits for more, gathers up to the batch limit, writes the batch, marks queue items done, and exits when it receives a stop marker.

**Call relations**: EgressProxy._enqueue_meter starts this worker on demand. It calls _write_meter_batch for the database work and is stopped by EgressProxy.stop.

*Call graph*: calls 1 internal fn (_write_meter_batch); called by 1 (_enqueue_meter); 2 external calls (sleep, log_error).


##### `EgressProxy._write_meter_batch`  (lines 815–858)

```
async def _write_meter_batch(self, records: list[_MeterRecord]) -> None
```

**Purpose**: This writes accumulated egress counts and token usage to the workspace ledger. It groups records by run so each turn is billed and audited correctly.

**Data flow**: A list of metering records goes in. The function totals egress request counts and sums token usage by model for each run, enters the correct workspace, and writes egress and sandbox-token records. Failures for one run are logged without stopping the whole batch.

**Call relations**: EgressProxy._meter_loop calls this after collecting a batch. It is the final step for records queued by _meter_ledger and _meter_tokens.

*Call graph*: called by 1 (_meter_loop); 6 external calls (__init__, record_egress_request, record_sandbox_tokens, workspace_tx, log_error, ws).


##### `_read_fault`  (lines 861–867)

```
def _read_fault(task: asyncio.Task[tuple[Rule, ...]]) -> None
```

**Purpose**: This consumes an exception from a background rule-resolution task if nobody else is left to await it. That prevents asyncio from logging a raw unhandled exception message.

**Data flow**: A completed or cancelled task goes in. If it was not cancelled, the function reads its exception value for side effect and returns nothing.

**Call relations**: EgressProxy._rules_for attaches this as a completion callback to rule-resolution tasks. Waiting callers still receive the exception normally; this only covers abandoned tasks.


##### `_start_tls_server`  (lines 870–886)

```
async def _start_tls_server(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, context: ssl.SSLContext) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]
```

**Purpose**: This turns an accepted CONNECT tunnel into a server-side TLS session. It lets the proxy read the sandbox's HTTPS request in decrypted form after sending the normal CONNECT success response.

**Data flow**: The existing stream reader, writer, and SSL context go in. The function pauses plaintext reading, sends CONNECT 200, upgrades the transport to TLS using the provided certificate context, patches the stream objects to use the TLS transport, and returns them.

**Call relations**: EgressProxy._mitm calls this after obtaining a per-host certificate from _leaf_context. The returned streams are then used with _read_request_head and later response writing.

*Call graph*: called by 1 (_mitm); 3 external calls (drain, write, get_running_loop).


##### `_read_request_head`  (lines 889–922)

```
async def _read_request_head(reader: asyncio.StreamReader) -> tuple[bytes, list[bytes]] | _HeaderRefusal | None
```

**Purpose**: This reads an HTTP request line and headers with time and size limits. It protects the proxy from clients that send headers forever, too slowly, or too large.

**Data flow**: A stream reader goes in. The function reads the first line and header lines until the blank separator, returning the request line and header list; if the input is empty it returns None, and if it times out or exceeds the limit it returns a refusal object.

**Call relations**: EgressProxy._handle uses this for the initial proxy CONNECT request, and EgressProxy._mitm uses it for the decrypted inner HTTP request.

*Call graph*: called by 2 (_handle, _mitm); 3 external calls (__init__, readline, timeout).


##### `_forward_match`  (lines 925–939)

```
def _forward_match(headers: list[bytes], candidates: list[ForwardRule]) -> ForwardRule | None
```

**Purpose**: This checks whether a decrypted request carries a grant-forwarding sentinel. A sentinel is a placeholder secret value that identifies which granted account should be used without revealing the real credential.

**Data flow**: Request headers and candidate ForwardRule objects go in. The function scans header values for an exact sentinel match, allowing common scheme prefixes like Bearer, and returns the matching rule or None.

**Call relations**: EgressProxy._mitm calls this before deciding how to send the request. A match goes to _forward_broker; no match continues to the direct upstream path with possible header injection.

*Call graph*: called by 1 (_mitm).


##### `_read_request_body`  (lines 956–1009)

```
async def _read_request_body(reader: asyncio.StreamReader, headers: list[bytes]) -> bytes | _Refusal
```

**Purpose**: This reads the full body for a broker-forwarded request, but only when the body has a clear and acceptable Content-Length. The broker path handles one complete request envelope, not an open-ended stream.

**Data flow**: A stream reader and headers go in. The function inspects length and transfer-encoding headers, returns the body bytes if valid and within the size limit, or returns a refusal explaining chunked, missing, malformed, negative, too-large, or truncated input.

**Call relations**: EgressProxy._forward_broker calls this before contacting a grant broker. If a refusal comes back, the broker is not called and _drain_refused_body helps the client receive the error cleanly.

*Call graph*: called by 1 (_forward_broker); 2 external calls (__init__, readexactly).


##### `_drain_refused_body`  (lines 1012–1029)

```
async def _drain_refused_body(reader: asyncio.StreamReader, pending: int) -> None
```

**Purpose**: This discards request-body bytes after the proxy has already decided to refuse the request. It gives the client a chance to finish sending and then read the useful error response.

**Data flow**: A reader and an upper bound on pending bytes go in. The function reads and throws away chunks until the bound, a timeout, or client close is reached. It buffers no full body and returns nothing.

**Call relations**: EgressProxy._forward_broker calls this after writing a refusal for a bad forwarded body. It is a cleanup step for clearer client behavior, not part of successful forwarding.

*Call graph*: called by 1 (_forward_broker); 2 external calls (read, timeout).


##### `_forward_headers`  (lines 1032–1044)

```
def _forward_headers(headers: list[bytes], rule: ForwardRule) -> dict[str, str]
```

**Purpose**: This prepares safe request headers for a broker-forwarded call. It removes headers the broker or proxy must own, including the sentinel-bearing credential header.

**Data flow**: Original header lines and the matching ForwardRule go in. The function drops connection, proxy, host, content-length, and sentinel headers, decodes the remaining names and values, and returns a plain dictionary for the broker.

**Call relations**: EgressProxy._forward_broker calls this when building the broker request. The broker then supplies the real credential on its own side.

*Call graph*: called by 1 (_forward_broker).


##### `_forward_response_bytes`  (lines 1047–1065)

```
def _forward_response_bytes(response: ForwardedResponse) -> bytes
```

**Purpose**: This turns a broker's reconstructed provider response into HTTP bytes for the sandbox client. It also filters dangerous or proxy-owned headers.

**Data flow**: A ForwardedResponse goes in, containing status, headers, and body. The function builds an HTTP/1.1 status line, copies safe headers, adds the measured content length and connection close, and appends the body bytes.

**Call relations**: EgressProxy._forward_broker calls this after a successful broker call. It uses _has_crlf to prevent header injection through newline characters.

*Call graph*: calls 1 internal fn (_has_crlf); called by 1 (_forward_broker); 1 external calls (HTTPStatus).


##### `_has_crlf`  (lines 1068–1069)

```
def _has_crlf(value: str) -> bool
```

**Purpose**: This checks whether a string contains carriage-return or line-feed characters. Those characters are unsafe inside HTTP header names or values because they can split one header into many.

**Data flow**: A string goes in. The function returns true if it contains either newline form, otherwise false.

**Call relations**: _forward_response_bytes calls this while copying broker response headers. Headers that fail this check are dropped.

*Call graph*: called by 1 (_forward_response_bytes).


##### `_inject`  (lines 1072–1097)

```
def _inject(headers: list[bytes], candidates: list[InjectionRule]) -> bytes
```

**Purpose**: This rewrites request headers by replacing an exact sentinel placeholder with the real secret for the matching account. It deliberately leaves non-matching sentinels untouched so one account cannot accidentally receive another account's token.

**Data flow**: Original header lines and candidate InjectionRule objects go in. The function skips connection headers, searches for a header whose name and value exactly match a rule's header and sentinel, writes the real value for that rule, copies other headers unchanged, and adds Connection: close.

**Call relations**: EgressProxy._mitm calls this on the direct upstream path after it has decrypted one request. The rewritten headers are sent to the real HTTPS server.

*Call graph*: called by 1 (_mitm).


##### `_relay`  (lines 1100–1134)

```
async def _relay(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, upstream_reader: asyncio.StreamReader, upstream_writer: asyncio.StreamWriter, on_downstream: Callable[[bytes]
```

**Purpose**: This copies bytes in both directions between the sandbox client and the upstream server. It is the core pipe used for opaque tunnels and streamed MITM responses.

**Data flow**: Client streams, upstream streams, and an optional callback for downstream response chunks go in. The function starts one pump each way, lets request EOF still allow response progress, optionally reports each response chunk, and closes the upstream writer when done.

**Call relations**: EgressProxy._tunnel uses this for raw CONNECT tunnels, and EgressProxy._mitm uses it after sending an inspected request upstream. It delegates each one-way copy to _pump.

*Call graph*: calls 1 internal fn (_pump); called by 2 (_mitm, _tunnel); 7 external calls (Event, can_write_eof, close, write_eof, create_task, timeout, wait).


##### `_pump`  (lines 1137–1152)

```
async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, on_chunk: Callable[[bytes], None] | None=None, on_progress: Callable[[], None] | None=None) -> None
```

**Purpose**: This performs one direction of byte copying between two streams. It is the simple worker behind the two-way relay.

**Data flow**: A reader, writer, and optional callbacks go in. The function repeatedly reads chunks, writes them to the destination, flushes the write, and reports progress or chunk contents if callbacks were provided.

**Call relations**: _relay creates _pump tasks for client-to-upstream and upstream-to-client traffic. Errors and cancellations are treated as normal connection endings.

*Call graph*: called by 1 (_relay); 3 external calls (read, drain, write).


##### `_int_field`  (lines 1155–1157)

```
def _int_field(usage: dict[str, object], name: str) -> int
```

**Purpose**: This safely extracts an integer token count from a parsed usage dictionary. It treats missing values, non-integers, and booleans as zero.

**Data flow**: A dictionary and field name go in. The function reads that field and returns its integer value only if it is a real integer; otherwise it returns 0.

**Call relations**: HttpTokenUsage._absorb_anthropic and HttpTokenUsage._openai use this to normalize provider usage fields before building Usage records.

*Call graph*: called by 2 (_absorb_anthropic, _openai).


##### `HttpTokenUsage.feed`  (lines 1183–1220)

```
def feed(self, chunk: bytes) -> None
```

**Purpose**: This accepts raw response bytes as they pass through the proxy and starts turning them into parseable model-usage data. It understands HTTP headers, compression choices, and where the body begins.

**Data flow**: One downstream response chunk goes in. The function accumulates headers until complete, rejects oversized headers or unsupported encodings, sets up gzip or deflate decoding if needed, then passes body bytes onward for chunk and usage parsing.

**Call relations**: EgressProxy._mitm gives this method to _relay as the downstream callback for token-metered model hosts. It hands body data to _feed_wire_body and records failure through _fail.

*Call graph*: calls 2 internal fn (_fail, _feed_wire_body); 1 external calls (decompressobj).


##### `HttpTokenUsage.usage`  (lines 1222–1233)

```
def usage(self) -> tuple[str, Usage] | None
```

**Purpose**: This returns the model name and token counts found so far, if any. It is the final readout after a response has been streamed.

**Data flow**: The accumulator's internal buffered response state goes in implicitly. The function finishes decompression, tries to parse a whole JSON body if streaming events did not reveal usage, and returns a model plus Usage object or None.

**Call relations**: EgressProxy._meter_tokens calls this after _relay finishes. It relies on _finish_decoder and _maybe_json_body to catch late or non-streamed usage data.

*Call graph*: calls 2 internal fn (_finish_decoder, _maybe_json_body); 1 external calls (__init__).


##### `HttpTokenUsage._feed_wire_body`  (lines 1235–1281)

```
def _feed_wire_body(self, chunk: bytes) -> None
```

**Purpose**: This converts the HTTP response body as it appears on the wire into actual payload bytes. It understands normal bodies and chunked transfer encoding.

**Data flow**: A body chunk goes in. For non-chunked bodies it sends bytes straight to the decoder; for chunked bodies it parses chunk sizes, extracts payload bytes, checks required separators, and stops cleanly at the terminating chunk.

**Call relations**: HttpTokenUsage.feed calls this after headers are complete. It passes payload to _decode and calls _finish_decoder or _fail when chunk framing ends or breaks.

*Call graph*: calls 3 internal fn (_decode, _fail, _finish_decoder); called by 1 (feed).


##### `HttpTokenUsage._decode`  (lines 1283–1299)

```
def _decode(self, chunk: bytes) -> None
```

**Purpose**: This decompresses payload bytes when the response body is compressed, then forwards plain body text for parsing. It also protects against decoder stalls and excessive expansion.

**Data flow**: Compressed or plain payload bytes go in. If there is no decompressor, the bytes are forwarded unchanged; otherwise the decompressor produces bounded plain bytes, which are passed to _feed_body. Bad compressed data marks the accumulator failed.

**Call relations**: HttpTokenUsage._feed_wire_body calls this for each payload chunk. It uses the decoder contract described by _ContentDecoder and sends results to _feed_body.

*Call graph*: calls 2 internal fn (_fail, _feed_body); called by 1 (_feed_wire_body).


##### `HttpTokenUsage._finish_decoder`  (lines 1301–1310)

```
def _finish_decoder(self) -> None
```

**Purpose**: This completes decompression at the end of a response. It makes sure no final bytes are left trapped inside the decoder before usage is reported.

**Data flow**: The accumulator's decompressor state goes in implicitly. If not already finished, it flushes any remaining decoded bytes into the body parser; if flushing fails, it marks parsing as failed.

**Call relations**: HttpTokenUsage._feed_wire_body calls this when a chunked body ends, and HttpTokenUsage.usage calls it before returning final results.

*Call graph*: calls 2 internal fn (_fail, _feed_body); called by 2 (_feed_wire_body, usage).


##### `HttpTokenUsage._feed_body`  (lines 1312–1323)

```
def _feed_body(self, chunk: bytes) -> None
```

**Purpose**: This accumulates decoded response body text and splits it into lines for event parsing. It enforces a maximum buffer size so a huge response cannot grow memory forever.

**Data flow**: Decoded body bytes go in. The function appends them to the body buffer, sends each complete line to _consume, keeps any partial line for later, and fails if the buffer grows beyond the limit.

**Call relations**: HttpTokenUsage._decode and _finish_decoder call this with plain bytes. It passes line-level parsing to HttpTokenUsage._consume.

*Call graph*: calls 2 internal fn (_consume, _fail); called by 2 (_decode, _finish_decoder).


##### `HttpTokenUsage._consume`  (lines 1325–1342)

```
def _consume(self, line: bytes) -> None
```

**Purpose**: This examines one decoded response line for model usage information. It supports server-sent events, which are lines like 'data: {...}' used by streaming APIs, and also opportunistically checks plain JSON lines.

**Data flow**: One line of bytes goes in. If it is a JSON server-sent event, the function parses it and dispatches to the Anthropic or OpenAI parser based on host; if it is not an event line, it may try whole-body JSON parsing.

**Call relations**: HttpTokenUsage._feed_body calls this for each complete line. It delegates provider-specific meaning to _anthropic, _openai, and _maybe_json_body.

*Call graph*: calls 3 internal fn (_anthropic, _maybe_json_body, _openai); called by 1 (_feed_body); 1 external calls (loads).


##### `HttpTokenUsage._maybe_json_body`  (lines 1344–1359)

```
def _maybe_json_body(self, payload: bytes) -> None
```

**Purpose**: This tries to parse a non-streamed JSON response body for usage information. It covers model APIs that return one JSON object instead of server-sent event lines.

**Data flow**: A byte payload goes in. If usage has not already been seen and the payload looks like JSON, the function parses it, then extracts Anthropic or OpenAI fields depending on the host.

**Call relations**: HttpTokenUsage._consume calls this for non-event lines, and HttpTokenUsage.usage calls it on the remaining buffered body before giving up.

*Call graph*: calls 2 internal fn (_absorb_anthropic, _openai); called by 2 (_consume, usage); 1 external calls (loads).


##### `HttpTokenUsage._anthropic`  (lines 1361–1371)

```
def _anthropic(self, event: dict[str, object]) -> None
```

**Purpose**: This reads Anthropic streaming events and extracts model and token usage fields from the event types that carry them.

**Data flow**: A parsed event dictionary goes in. For message_start it records the model and initial input/cache usage; for message_delta it updates output usage. Other event types are ignored.

**Call relations**: HttpTokenUsage._consume calls this when the response host is Anthropic. It delegates field extraction to _absorb_anthropic.

*Call graph*: calls 1 internal fn (_absorb_anthropic); called by 1 (_consume).


##### `HttpTokenUsage._absorb_anthropic`  (lines 1373–1383)

```
def _absorb_anthropic(self, usage: object, initial: bool) -> None
```

**Purpose**: This copies Anthropic usage numbers into the accumulator. It knows which fields represent input, output, cache reads, and cache writes.

**Data flow**: A usage object and a flag saying whether it is initial usage go in. If the object is a dictionary, initial calls set input and cache counts, every valid call may update output tokens, and the accumulator is marked as having seen usage.

**Call relations**: HttpTokenUsage._anthropic and _maybe_json_body call this for Anthropic responses. It uses _int_field to avoid bad or missing numeric fields.

*Call graph*: calls 1 internal fn (_int_field); called by 2 (_anthropic, _maybe_json_body).


##### `HttpTokenUsage._openai`  (lines 1385–1394)

```
def _openai(self, event: dict[str, object]) -> None
```

**Purpose**: This reads OpenAI-style model and token usage fields from a parsed response object.

**Data flow**: A parsed event dictionary goes in. The function records the model string when present, then reads prompt and completion token counts from the usage dictionary and marks usage as seen.

**Call relations**: HttpTokenUsage._consume and _maybe_json_body call this for OpenAI responses. It uses _int_field for safe numeric extraction.

*Call graph*: calls 1 internal fn (_int_field); called by 2 (_consume, _maybe_json_body).


##### `HttpTokenUsage._fail`  (lines 1396–1400)

```
def _fail(self) -> None
```

**Purpose**: This marks token-usage parsing as failed and clears accumulated buffers. It is a safety stop for malformed, unsupported, or oversized responses.

**Data flow**: No external input is needed. The function sets the overflow/failure flag and empties header, body, and chunk buffers so future feed calls do no more work.

**Call relations**: Several HttpTokenUsage parsing steps call this when they detect bad framing, bad compression, unsupported encoding, or too much buffered data. After failure, usage will not report token counts.

*Call graph*: called by 5 (_decode, _feed_body, _feed_wire_body, _finish_decoder, feed).


##### `_respond`  (lines 1403–1421)

```
async def _respond(writer: asyncio.StreamWriter, status: int, message: str) -> None
```

**Purpose**: This writes a clear HTTP error or refusal response to the client and closes the conversation cleanly. The explanatory message is placed in the body, where HTTP clients are likely to show it.

**Data flow**: A stream writer, status code, and message go in. The function builds a plain-text HTTP response with content length and connection close, writes it, drains the writer, and ignores routine socket errors from clients that already disconnected.

**Call relations**: EgressProxy._handle uses this for denied or malformed CONNECT requests, _tunnel uses it for upstream failures, _mitm uses it for inspected-header refusals, and _forward_broker uses it for broker or body errors.

*Call graph*: called by 4 (_forward_broker, _handle, _mitm, _tunnel); 3 external calls (drain, write, HTTPStatus).


### `core/src/ufo/sandbox/proxy/rules.py`

`domain_logic` · `turn setup before sandbox network requests`

A sandbox is meant to run useful work without freely exposing secrets or the whole internet. This file is the rule factory for that boundary. It does not register new permissions directly; instead, it derives plain rule values that the egress proxy later reads, like a guard checking a guest list at a door.

The rules answer a few practical questions. Which exact hosts may the sandbox contact? Should a fake placeholder secret, called a sentinel, be replaced with a real secret only when the request leaves the sandbox? Should requests to a host be counted for billing or spend tracking? Should a request be forwarded through the broker instead of sent directly, because the real account token lives only on the server side?

The file covers several sources of permission. Model use opens the model provider host and injects the model API key. Extension manifests may allow public internet during live turns. S3 artifact storage opens only the bucket host needed for file sharing. Credential slots open their configured service hosts and inject per-workspace secrets. Grants open connector-related hosts and meter those requests, while CLI credential grants can create forwarding rules instead of local secret injection.

A key safety behavior is that credential failures are contained. If one credential slot cannot be read or resolved, the code logs a warning and skips only that slot. It does not accidentally fall back to another secret, and it does not break all other network rules for the turn.

#### Function details

##### `provider_host`  (lines 91–95)

```
def provider_host(model: str) -> str
```

**Purpose**: This function chooses the network host for a model name. For example, model names starting with OpenAI-style prefixes map to the OpenAI API host, while Claude-style names map to the Anthropic API host.

**Data flow**: It receives a model name as text, checks it against known prefixes, and returns the matching provider host. If no prefix matches, it raises an error instead of guessing, so the sandbox does not get an unclear or unsafe network permission.

**Call relations**: It is used by derive_model_rules when building the rules for model access. That caller needs the host first so it can allow the right destination, inject the right kind of API key, and meter model usage.

*Call graph*: called by 1 (derive_model_rules).


##### `derive_model_rules`  (lines 98–113)

```
def derive_model_rules(model: str, real_key: str) -> tuple[Rule, ...]
```

**Purpose**: This function builds the basic rules that let the sandbox call the chosen language model provider. It also makes sure the real model API key is inserted only at the proxy boundary, not exposed inside the sandbox.

**Data flow**: It takes a model name and the real provider key. It finds the provider host, chooses the right authentication header shape, then returns three rules: allow that host, replace the sandbox's sentinel key with the real key on outgoing traffic, and meter usage under tokens.

**Call relations**: It calls provider_host to identify the provider. It then creates the scope, injection, and metering rules that the proxy later enforces when the sandbox tries to contact the model API.

*Call graph*: calls 1 internal fn (provider_host); 3 external calls (__init__, __init__, __init__).


##### `derive_manifest_rules`  (lines 116–118)

```
def derive_manifest_rules(manifests: tuple[Manifest, ...]) -> tuple[InternetRule, ...]
```

**Purpose**: This function checks whether any installed extension says the sandbox needs public internet access during live turns. If so, it adds the rule that permits that broader internet access.

**Data flow**: It receives the loaded manifests and looks for one marked as needing sandbox internet. If at least one manifest asks for it, the output is a single InternetRule; otherwise the output is an empty tuple.

**Call relations**: It is part of the larger rule-building flow for a sandbox turn. Other functions add exact host permissions, while this one contributes the broader public-internet permission only when an extension manifest explicitly requires it.

*Call graph*: 1 external calls (__init__).


##### `derive_artifact_store_rules`  (lines 121–137)

```
async def derive_artifact_store_rules(blob: BlobStore) -> tuple[Rule, ...]
```

**Purpose**: This function allows the sandbox to upload shared artifacts, such as produced files, when the artifact store is backed by S3. It opens only the exact S3 host needed, rather than granting general internet access.

**Data flow**: It receives the configured blob store. If the store is an S3BlobStore, it asks the store for the host used for uploads, then returns rules allowing that host and counting requests to it. If the store is not S3, it returns no network rules because there is no external artifact host to allow.

**Call relations**: During rule derivation, this function adds the special network path needed for file sharing. It calls the blob store's put_host method to learn the exact host, then hands back scope and metering rules for the proxy.

*Call graph*: 3 external calls (__init__, __init__, put_host).


##### `derive_credential_rules`  (lines 140–203)

```
async def derive_credential_rules(slots: tuple[CredentialSlot, ...], workspace_id: UUID, store: CredentialStore) -> tuple[Rule, ...]
```

**Purpose**: This function turns declared credential slots into safe network rules for one workspace. It allows only the resolved credential host, injects the real secret in place of the sandbox's placeholder, and optionally meters that host.

**Data flow**: It receives credential slot declarations, a workspace ID, and the credential store. For each slot with an injection target, it tries to read the workspace's secret and resolve the target host. If either is missing or fails, it logs a warning and skips that slot. If the slot is for Git basic authentication, it combines the configured username and secret into a Basic Authorization header value. It groups injections by host, then returns rules that allow each host, inject the needed headers, and add metering where configured.

**Call relations**: This is one of the main safety gates for secrets. It calls slot_secret to read the per-workspace secret, credential_host to resolve the allowed host, b64encode when Git needs a Basic auth value, and warn when a slot cannot be used. Its output is consumed by the proxy rule set so secrets are swapped in only as traffic leaves the sandbox.

*Call graph*: 7 external calls (__init__, __init__, __init__, b64encode, credential_host, slot_secret, warn).


##### `derive_grant_rules`  (lines 206–223)

```
def derive_grant_rules(grants: tuple[Grant, ...], transfer_hosts: 'ConnectorTransferHosts | None'=None) -> tuple[Rule, ...]
```

**Purpose**: This function turns active connector grants into host allowlist and metering rules. A grant permits the sandbox to reach the connector's provider host and any file-transfer hosts related to that connector, but it does not inject account tokens.

**Data flow**: It receives grants and, optionally, a ConnectorTransferHosts lookup. For each grant, it collects the grant's own provider host plus any transfer hosts, removes empty values and duplicates, then returns a scope rule for those hosts and a request-metering rule for each host.

**Call relations**: This function is used when connector grants are folded into the sandbox's egress permissions. If transfer host information is available, it asks ConnectorTransferHosts.of for provider-specific file-store hosts before creating the rules the proxy will enforce.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_cli_rules`  (lines 226–246)

```
def derive_cli_rules(grants: tuple[Grant, ...], acting_member_id: UUID | None, clis: Mapping[str, CliCredential]) -> tuple[Rule, ...]
```

**Purpose**: This function creates forwarding rules for connector CLI credentials. Instead of putting a real token in the sandbox, matching requests are sent through the broker, which holds and uses the account credential server-side.

**Data flow**: It receives grants, the acting member ID if there is one, and the known CLI credential declarations. For each grant that has a matching CLI credential and is usable by the acting member, it builds a ForwardRule containing the host, header, sentinel value, account ID, and broker forwarder. Private grants owned by someone else are skipped.

**Call relations**: This function fits beside grant rule derivation. derive_grant_rules opens and meters hosts, while derive_cli_rules adds the authenticated forwarding path for allowed CLI-style connector traffic. It calls grant_sentinel to build the placeholder value that the sandbox request must carry.

*Call graph*: 2 external calls (__init__, grant_sentinel).


##### `ConnectorTransferHosts.of`  (lines 260–261)

```
def of(self, provider: str) -> tuple[str, ...]
```

**Purpose**: This method answers which broker file-transfer hosts apply to a connector provider. It uses a provider-specific answer when one is known, otherwise it falls back to the default hosts for open connector namespaces.

**Data flow**: It receives a provider name. It looks that name up in the explicit provider-to-hosts mapping; if present, it returns that tuple, even if it is empty. If the provider is not present, it returns the default tuple.

**Call relations**: derive_grant_rules calls this method when it needs to add file-transfer hosts for a grant. This keeps the grant rule builder simple: it asks this object for the extra hosts and then turns them into allowlist and metering rules.


##### `connector_transfer_hosts`  (lines 264–275)

```
def connector_transfer_hosts(manifests: tuple[Manifest, ...]) -> ConnectorTransferHosts
```

**Purpose**: This function builds the lookup table used to find connector file-transfer hosts. It reads the deployed extension manifests and records which hosts each registered connector declares.

**Data flow**: It receives manifests. It walks through each connector in each manifest and maps the connector's provider name to its declared transfer hosts. It also asks for the open connector namespace, if one exists, and uses that namespace's transfer hosts as the default for providers without an explicit connector entry. It returns a ConnectorTransferHosts object containing both pieces.

**Call relations**: This function prepares data for later grant rule derivation. It calls open_connector_namespace to find default broker transfer hosts, constructs ConnectorTransferHosts, and that object is later queried by derive_grant_rules through ConnectorTransferHosts.of.

*Call graph*: 2 external calls (__init__, open_connector_namespace).
