# Security boundaries, secrets, grants, and policy enforcement  `stage-17` (cross-cutting infrastructure)

This stage is shared safety plumbing, used during startup, normal work, and operator access. It is like the locks, badges, and guarded doors in a building. Bearer, gateway, and artifact token code creates signed short-lived “passes” that prove a member, hosted user, or file download is allowed. Operator session code checks special admin-style tools and limits which workspace they may view. Workspace context and database row-level security keep every request tied to the right workspace, so one workspace cannot read another’s rows.

Sandbox safety is handled by a locked workspace folder, short-lived storage credentials, and an outgoing web proxy. The sandbox session code blocks access to private transcripts and bookkeeping files. Proxy rules decide which websites may be contacted, which requests need broker forwarding, and how usage is counted; the proxy server enforces those rules.

Credential code seals secrets, shows only safe credential status, and unseals values only in trusted paths. Connectors, direct source access, and Pipedream proxy support outside providers without leaking provider tokens. Governance adds a final policy gate by requiring proposed prompt changes to be approved before they take effect.

## Files in this stage

### Sandbox boundaries
These files define the sandbox's network, filesystem, and storage limits so untrusted code can work without escaping its authorized workspace.

### `core/src/ufo/sandbox/proxy/server.py`

`io_transport` · `startup and request handling`

A sandboxed agent should not be able to freely call any website or see real API keys. This file solves that by acting like a checkpoint at the only road out. Every outgoing HTTPS connection arrives here first as a CONNECT request. The proxy reads the run token, finds the rules for that turn's agent, and denies the request unless the target host is allowed. For ordinary allowed hosts, it simply opens a tunnel and passes encrypted bytes through. For hosts that need credentials, it becomes a trusted middle point: it presents a temporary certificate signed by its own local certificate authority, reads the request, and either swaps a harmless placeholder token for the real secret or sends the request through a grant broker that holds the credential server-side. It also checks that the turn is still running before any real key can be used, so stale or fake tokens cannot draw secrets. Along the way it emits metrics and writes ledger rows. Model API responses get special treatment: streamed or JSON usage data is read from the response as it passes by, so sandbox model tokens can be billed separately. Without this file, sandbox agents would either have no network access, unsafe unrestricted access, or direct exposure to credentials.

#### Function details

##### `generate_ca`  (lines 83–106)

```
async def generate_ca() -> tuple[str, str]
```

**Purpose**: Creates a temporary certificate authority, which is a root certificate and key the proxy uses to sign per-host certificates. This lets the proxy safely inspect approved HTTPS requests from sandboxes that trust this local authority.

**Data flow**: It starts with no input, creates a temporary folder, asks the openssl command-line tool to make a self-signed certificate and private key, reads both files back as text, and returns them as a pair.

**Call relations**: This is used before the proxy starts so EgressProxy.start can later write the authority files to disk and EgressProxy._leaf_context can mint host-specific certificates from them.

*Call graph*: calls 1 internal fn (_openssl); 2 external calls (Path, TemporaryDirectory).


##### `_openssl`  (lines 109–115)

```
async def _openssl(*argv: str) -> None
```

**Purpose**: Runs the external openssl program and turns failures into clear Python errors. It is the small wrapper that keeps certificate and key creation in one place.

**Data flow**: It receives openssl command arguments, starts the openssl process, waits for it to finish, ignores normal output, and raises an error if openssl reports failure.

**Call relations**: generate_ca uses it to create the root certificate, EgressProxy.start uses it to create a reusable leaf key, and EgressProxy._leaf_context uses it to create per-host certificates.

*Call graph*: called by 3 (_leaf_context, start, generate_ca); 1 external calls (create_subprocess_exec).


##### `PerAgentRules.resolve`  (lines 134–146)

```
async def resolve(self, run: RunToken | None) -> tuple[Rule, ...]
```

**Purpose**: Builds the exact network rule set for one sandbox turn. It combines the base workspace rules with only the OAuth grants and CLI credentials that belong to the agent and acting member for that turn.

**Data flow**: It receives an optional run token, reads the turn identity when possible, asks the grant store for active grants, turns those grants into proxy rules, and returns the combined rule list. If there is no token, no grant store, or no matching turn, it returns only the base rules.

**Call relations**: EgressProxy gets this function through its resolver callback. It calls PerAgentRules._turn_of to learn which agent and member the turn belongs to, then hands grant information to rule-derivation helpers.

*Call graph*: calls 1 internal fn (_turn_of); 2 external calls (derive_cli_rules, derive_grant_rules).


##### `PerAgentRules._turn_of`  (lines 148–173)

```
async def _turn_of(self, run: RunToken) -> tuple[UUID, UUID | None] | None
```

**Purpose**: Looks up which agent owns a turn and which member is acting for it. This matters because private credentials must only be available during the right member's own turns.

**Data flow**: It receives a run token, opens a workspace database transaction, selects the turn's agent and member fields, chooses the speaker member when present or the on-behalf-of member otherwise, and returns that pair. If the turn is not found, it returns nothing.

**Call relations**: PerAgentRules.resolve calls this before deriving grant and CLI rules, so the proxy can keep one agent's or member's grants from leaking into another agent's request.

*Call graph*: called by 1 (resolve); 2 external calls (select, workspace_tx).


##### `PerAgentRules.turn_live`  (lines 175–191)

```
async def turn_live(self, run: RunToken) -> bool
```

**Purpose**: Checks whether a run token still names a turn that is actively running. This is the final gate before the proxy injects or forwards any real credential.

**Data flow**: It receives a run token, reads the turn status from the database, compares it with the running status value, and returns true only for a currently running turn.

**Call relations**: This is intended to be passed into EgressProxy as its authorization callback. EgressProxy._handle uses that callback before allowing credential-bearing MITM traffic.

*Call graph*: 2 external calls (select, workspace_tx).


##### `EgressProxy.start`  (lines 208–222)

```
async def start(self, bind_host: str=PROXY_BIND_HOST, port: int=0, public_url: str | None=None) -> ProxyEndpoint
```

**Purpose**: Starts listening for sandbox proxy connections and returns the connection details that sandboxes need. It also prepares certificate files used for HTTPS interception.

**Data flow**: It receives a bind host, port, and optional public URL, creates a work directory, writes the certificate authority files there, creates a leaf private key, starts an asyncio TCP server, and returns a ProxyEndpoint containing the bound port and CA certificate.

**Call relations**: Startup code calls this to bring the proxy online. The server it creates calls EgressProxy._handle for each incoming sandbox connection.

*Call graph*: calls 1 internal fn (_openssl); 4 external calls (__init__, start_server, Path, TemporaryDirectory).


##### `EgressProxy.stop`  (lines 224–233)

```
async def stop(self) -> None
```

**Purpose**: Shuts the proxy down cleanly. It closes the listening server, waits for background metering writes to finish, and removes temporary certificate files.

**Data flow**: It reads the proxy's stored server, background task set, and work directory, closes or waits on each as needed, and clears those fields afterward.

**Call relations**: Teardown code calls this when the proxy is no longer needed, making sure accounting tasks started by metering functions are not abandoned.

*Call graph*: 1 external calls (gather).


##### `EgressProxy._handle`  (lines 235–272)

```
async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None
```

**Purpose**: Processes one incoming proxy connection from a sandbox. It decides whether to deny, tunnel blindly, or inspect and rewrite the HTTPS request.

**Data flow**: It reads the CONNECT request and Proxy-Authorization header, parses the run token, loads rules, checks whether the host is allowed, and then chooses a path. Plain allowed hosts go to _tunnel; credential or brokered hosts require a live turn and then go to _mitm; denied or malformed cases get an HTTP refusal.

**Call relations**: The asyncio server created by EgressProxy.start calls this for each connection. It is the main dispatcher that calls _rules_for, _tunnel, _mitm, _run_token, and _respond.

*Call graph*: calls 5 internal fn (_mitm, _rules_for, _tunnel, _respond, _run_token); 2 external calls (readline, close).


##### `EgressProxy._rules_for`  (lines 274–294)

```
async def _rules_for(self, run: RunToken | None) -> tuple[Rule, ...]
```

**Purpose**: Gets the resolved rules for a run token, using a small cache so repeated requests in the same turn do not keep hitting the database. If rule lookup fails, it falls back to the safe base behavior instead of opening access wider.

**Data flow**: It receives an optional run token, returns uncached base rules for missing tokens, checks the cache for known tokens, otherwise calls the configured resolver. Successful results are cached up to a fixed limit; failures are logged and replaced with base rules without caching the failure.

**Call relations**: EgressProxy._handle calls this before deciding whether a host is allowed and whether credentials may be used.

*Call graph*: calls 1 internal fn (encode); called by 1 (_handle); 1 external calls (log).


##### `EgressProxy._tunnel`  (lines 296–321)

```
async def _tunnel(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int, proxy_auth: str, rules: tuple[Rule, ...]) -> None
```

**Purpose**: Creates a plain encrypted tunnel to an allowed host when the proxy does not need to see or alter the request. This is used for allowed egress where no local secret injection is needed.

**Data flow**: It receives the sandbox connection, target host and port, authorization text, and rules. It opens a connection to the host, replies that the tunnel is established, records metrics and ledger work if needed, and then relays bytes both ways. If the host cannot be reached, it returns a bad-gateway response.

**Call relations**: EgressProxy._handle calls this for allowed hosts without injection or forwarding rules. It hands the actual byte copying to _relay and records usage through _meter and _meter_ledger.

*Call graph*: calls 4 internal fn (_meter, _meter_ledger, _relay, _respond); called by 1 (_handle); 4 external calls (drain, write, open_connection, wait_for).


##### `EgressProxy._mitm`  (lines 323–376)

```
async def _mitm(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int, injections: list[InjectionRule], forwards: list[ForwardRule], proxy_auth: str, rules: tuple[Rule
```

**Purpose**: Inspects an approved HTTPS request so it can safely inject a real credential or route the request through a grant broker. MITM here means "man in the middle": the proxy temporarily acts like the server to the sandbox and like the client to the real service.

**Data flow**: It receives the sandbox connection, host, credential rules, authorization header, and all rules. It creates or reuses a host certificate, upgrades the sandbox side to TLS, reads the HTTP request head, checks for a broker-forwarding sentinel, and either calls _forward_broker or opens a verified TLS connection upstream. For direct upstream calls it rewrites headers with _inject, meters the request, relays the response, and may parse token usage while relaying.

**Call relations**: EgressProxy._handle calls this only after host allow-listing and live-turn authorization pass. It coordinates _leaf_context, _start_tls_server, _read_request_head, _forward_match, _forward_broker, _inject, _relay, and token metering.

*Call graph*: calls 10 internal fn (_forward_broker, _leaf_context, _meter, _meter_ledger, _meter_tokens, _forward_match, _inject, _read_request_head, _relay, _start_tls_server); called by 1 (_handle); 3 external calls (__init__, open_connection, wait_for).


##### `EgressProxy._forward_broker`  (lines 378–420)

```
async def _forward_broker(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, rule: ForwardRule, request: tuple[bytes, list[bytes]], host: str, proxy_auth: str, rules: tupl
```

**Purpose**: Sends a sentinel-marked request through a grant broker instead of sending it directly to the provider. The real account credential stays inside the broker rather than in the sandbox or proxy-rewritten wire request.

**Data flow**: It receives the client TLS stream, the matched forwarding rule, the request head, host, proxy authorization, and rules. It reads a bounded request body, records metering, calls the broker with cleaned headers and the target URL, converts the broker response into HTTP bytes, and writes it back to the sandbox. Oversized or unsupported bodies are refused, and broker errors become a 502 response.

**Call relations**: EgressProxy._mitm calls this when _forward_match finds a grant sentinel. It relies on _read_request_body, _forward_headers, and _forward_response_bytes to prepare the broker exchange.

*Call graph*: calls 6 internal fn (_meter, _meter_ledger, _forward_headers, _forward_response_bytes, _read_request_body, _respond); called by 1 (_mitm); 3 external calls (drain, write, log).


##### `EgressProxy._leaf_context`  (lines 422–467)

```
async def _leaf_context(self, host: str) -> ssl.SSLContext
```

**Purpose**: Creates or reuses a TLS server context for a specific host. This is what lets the proxy present a certificate for the requested host while inspecting approved traffic.

**Data flow**: It receives a host name, checks the in-memory cache, and if missing creates certificate files signed by the proxy's local certificate authority. It loads those files into an SSL context, stores it by host, and returns it.

**Call relations**: EgressProxy._mitm calls this before upgrading the sandbox connection to TLS. It uses _openssl to ask the openssl tool to create the certificate request and signed certificate.

*Call graph*: calls 1 internal fn (_openssl); called by 1 (_mitm); 2 external calls (Path, SSLContext).


##### `EgressProxy._meter`  (lines 469–472)

```
def _meter(self, host: str, rules: tuple[Rule, ...]) -> None
```

**Purpose**: Emits a live metric for allowed egress to a metered host. Metrics are lightweight counters used for observation and dashboards.

**Data flow**: It receives a host and rule list, finds matching MeterRule entries, and emits a metric labeled with the host and billing dimension.

**Call relations**: The tunnel, MITM, and broker-forwarding paths call this once they know an allowed request or connection is actually being made.

*Call graph*: called by 3 (_forward_broker, _mitm, _tunnel); 1 external calls (emit_metric).


##### `EgressProxy._meter_ledger`  (lines 474–486)

```
def _meter_ledger(self, host: str, proxy_auth: str, rules: tuple[Rule, ...]) -> None
```

**Purpose**: Schedules persistent accounting for non-token egress without slowing down the network relay. It deliberately skips model-token dimensions, because those are billed from parsed token usage instead.

**Data flow**: It receives a host, proxy authorization string, and rules. If the rules say this host should be counted as normal egress, it starts a background task to write the ledger row and tracks that task so stop can wait for it.

**Call relations**: EgressProxy._tunnel, EgressProxy._mitm, and EgressProxy._forward_broker call this after metered egress begins. It hands the database write to _write_egress.

*Call graph*: calls 1 internal fn (_write_egress); called by 3 (_forward_broker, _mitm, _tunnel); 1 external calls (ensure_future).


##### `EgressProxy._write_egress`  (lines 488–494)

```
async def _write_egress(self, host: str, proxy_auth: str) -> None
```

**Purpose**: Writes one egress request record to the accounting ledger. If anything goes wrong, it logs the failure instead of disrupting the already-started network request.

**Data flow**: It receives the host and proxy authorization text, decodes the run token, opens a workspace transaction, and records egress against the workspace and turn. Errors are caught and logged.

**Call relations**: EgressProxy._meter_ledger starts this as a background task so database latency does not block sandbox traffic.

*Call graph*: calls 1 internal fn (from_proxy_auth); called by 1 (_meter_ledger); 3 external calls (record_egress_request, workspace_tx, log).


##### `EgressProxy._meter_tokens`  (lines 496–507)

```
def _meter_tokens(self, proxy_auth: str, accumulator: 'SseTokenUsage') -> None
```

**Purpose**: Schedules billing for sandbox model tokens after a model response has been observed. If no token usage was found in the response, it logs that fact and does not charge.

**Data flow**: It receives proxy authorization text and an SseTokenUsage accumulator. It asks the accumulator for parsed model and token counts; when present, it starts a background task to write the sandbox token accounting record.

**Call relations**: EgressProxy._mitm calls this after relaying a metered model-host response through an accumulator. It delegates the database write to _write_sandbox_tokens.

*Call graph*: calls 1 internal fn (_write_sandbox_tokens); called by 1 (_mitm); 2 external calls (ensure_future, log).


##### `EgressProxy._write_sandbox_tokens`  (lines 509–517)

```
async def _write_sandbox_tokens(self, proxy_auth: str, model: str, usage: Usage) -> None
```

**Purpose**: Records model token usage caused by sandbox egress. This separates sandbox model calls from other host-side model billing.

**Data flow**: It receives proxy authorization text, model name, and a Usage object, decodes the run token, opens a workspace transaction, and records token usage using the configured pricing table. Errors are logged.

**Call relations**: EgressProxy._meter_tokens starts this in the background after SseTokenUsage has recovered usage information from the response stream.

*Call graph*: calls 1 internal fn (from_proxy_auth); called by 1 (_meter_tokens); 3 external calls (record_sandbox_tokens, workspace_tx, log).


##### `_start_tls_server`  (lines 520–536)

```
async def _start_tls_server(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, context: ssl.SSLContext) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]
```

**Purpose**: Turns an accepted CONNECT tunnel into a TLS server connection from the sandbox's point of view. In plain terms, it says "connection established" and then starts speaking HTTPS with the sandbox.

**Data flow**: It receives the current stream reader and writer plus a TLS context, pauses raw reading so no TLS bytes are accidentally consumed too early, writes the CONNECT success line, upgrades the transport with TLS, patches the stream objects to use the encrypted transport, and returns them.

**Call relations**: EgressProxy._mitm calls this after getting a host certificate from _leaf_context and before reading the decrypted HTTP request.

*Call graph*: called by 1 (_mitm); 3 external calls (drain, write, get_running_loop).


##### `_read_request_head`  (lines 539–557)

```
async def _read_request_head(reader: asyncio.StreamReader) -> tuple[bytes, list[bytes]] | None
```

**Purpose**: Reads the HTTP request line and headers from a decrypted client request, with a size limit to prevent memory abuse.

**Data flow**: It receives a stream reader, reads the first line and then header lines until the blank line, counts the total header bytes, and returns the request line plus headers. Empty input or too-large headers return nothing.

**Call relations**: EgressProxy._mitm calls this immediately after TLS is established so it can decide whether to broker-forward, inject credentials, or stop.

*Call graph*: called by 1 (_mitm); 1 external calls (readline).


##### `_forward_match`  (lines 560–574)

```
def _forward_match(headers: list[bytes], candidates: list[ForwardRule]) -> ForwardRule | None
```

**Purpose**: Finds whether a request carries a broker-forwarding sentinel in the expected header. A sentinel is a harmless placeholder value that identifies which granted account should be used.

**Data flow**: It receives request headers and possible ForwardRule entries. It scans header values, accepts either a bare sentinel or common scheme-prefixed forms like Bearer, and returns the matching rule if the header name and sentinel match exactly.

**Call relations**: EgressProxy._mitm calls this after reading headers. A match sends the flow to _forward_broker; no match leaves the request on the direct upstream injection path.

*Call graph*: called by 1 (_mitm).


##### `_read_request_body`  (lines 577–601)

```
async def _read_request_body(reader: asyncio.StreamReader, headers: list[bytes]) -> bytes | None
```

**Purpose**: Reads the full body for a broker-forwarded request while enforcing safety limits. Broker forwarding is treated as one bounded API call, not an unlimited stream.

**Data flow**: It receives the client reader and headers, reads Content-Length, rejects chunked transfer, invalid lengths, negative lengths, too-large bodies, or truncated input, and returns the body bytes when valid.

**Call relations**: EgressProxy._forward_broker calls this before sending the request to the broker. A None result causes a clear refusal response.

*Call graph*: called by 1 (_forward_broker); 1 external calls (readexactly).


##### `_forward_headers`  (lines 604–616)

```
def _forward_headers(headers: list[bytes], rule: ForwardRule) -> dict[str, str]
```

**Purpose**: Builds the header set that is safe to pass to a grant broker. It removes the sentinel credential header and connection-specific headers that the proxy or broker should recreate.

**Data flow**: It receives raw request header lines and the matched ForwardRule, drops host, content-length, connection headers, and the rule's sentinel header, decodes the rest, and returns a dictionary of forwarded headers.

**Call relations**: EgressProxy._forward_broker calls this while preparing the broker request, so the broker can inject the real credential itself.

*Call graph*: called by 1 (_forward_broker).


##### `_forward_response_bytes`  (lines 619–637)

```
def _forward_response_bytes(response: ForwardedResponse) -> bytes
```

**Purpose**: Converts a broker's response object into a complete HTTP response for the sandbox. It also protects the response stream from unsafe header names or values containing line breaks.

**Data flow**: It receives a ForwardedResponse with status, headers, and body. It chooses a reason phrase when possible, filters headers that should be recalculated or could be unsafe, adds content-length and connection-close headers, and returns the final bytes.

**Call relations**: EgressProxy._forward_broker calls this after the broker returns. It uses _has_crlf to reject headers that could split or inject extra HTTP headers.

*Call graph*: calls 1 internal fn (_has_crlf); called by 1 (_forward_broker); 1 external calls (HTTPStatus).


##### `_has_crlf`  (lines 640–641)

```
def _has_crlf(value: str) -> bool
```

**Purpose**: Checks whether a string contains carriage-return or line-feed characters. In HTTP headers, those characters can be used for header injection, so they are treated as unsafe.

**Data flow**: It receives a string and returns true if it contains either newline style, otherwise false.

**Call relations**: _forward_response_bytes calls this while filtering broker-provided headers before writing them back to the sandbox.

*Call graph*: called by 1 (_forward_response_bytes).


##### `_run_token`  (lines 644–652)

```
def _run_token(proxy_auth: str) -> RunToken | None
```

**Purpose**: Safely extracts a RunToken from the Proxy-Authorization header. Missing or malformed headers become no token, which means the proxy falls back to base rules rather than broad access.

**Data flow**: It receives the raw proxy authorization value, returns None if it is empty or cannot be parsed, and otherwise returns the decoded RunToken.

**Call relations**: EgressProxy._handle calls this near the start of each connection to decide which rule set should apply.

*Call graph*: calls 1 internal fn (from_proxy_auth); called by 1 (_handle).


##### `_inject`  (lines 655–680)

```
def _inject(headers: list[bytes], candidates: list[InjectionRule]) -> bytes
```

**Purpose**: Rewrites request headers by replacing an exact sentinel value with the matching real secret. It never guesses: if the sentinel does not match one of the candidate rules, the header is left unchanged.

**Data flow**: It receives request headers and injection rules, removes connection-specific headers, searches for a matching header name and sentinel value, writes the real credential for exact matches, copies other headers as-is, adds connection-close, and returns the rebuilt header block.

**Call relations**: EgressProxy._mitm calls this on the direct upstream path after no broker-forwarding sentinel matched.

*Call graph*: called by 1 (_mitm).


##### `_relay`  (lines 683–699)

```
async def _relay(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, upstream_reader: asyncio.StreamReader, upstream_writer: asyncio.StreamWriter, on_downstream: Callable[[bytes]
```

**Purpose**: Copies bytes between the sandbox and the upstream service until one side finishes. It is the shared pipe used for both opaque tunnels and inspected HTTPS requests.

**Data flow**: It receives client and upstream readers and writers, starts one pump in each direction, waits until either side completes, cancels the other pump, and closes the upstream writer. If given a callback, it tees downstream response chunks to that callback after forwarding them.

**Call relations**: EgressProxy._tunnel uses this for blind tunnels, and EgressProxy._mitm uses it for direct upstream responses. It relies on _pump for each one-way copy.

*Call graph*: calls 1 internal fn (_pump); called by 2 (_mitm, _tunnel); 3 external calls (close, create_task, wait).


##### `_pump`  (lines 702–714)

```
async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, on_chunk: Callable[[bytes], None] | None=None) -> None
```

**Purpose**: Performs one direction of byte copying between a reader and writer. It optionally lets another observer see each chunk without delaying the forwarding design.

**Data flow**: It repeatedly reads chunks from one stream, writes and drains them to the other stream, and calls an optional chunk callback. Socket errors and cancellation are treated as normal shutdown.

**Call relations**: _relay creates two _pump tasks, one for upstream-to-client and one for client-to-upstream. Token metering uses the callback on the downstream side.

*Call graph*: called by 1 (_relay); 3 external calls (read, drain, write).


##### `_int_field`  (lines 717–719)

```
def _int_field(usage: dict[str, object], name: str) -> int
```

**Purpose**: Safely reads an integer token count from a JSON-like dictionary. It treats missing values, non-integers, and booleans as zero.

**Data flow**: It receives a usage dictionary and field name, looks up the value, checks that it is a real integer and not a boolean, and returns either that integer or zero.

**Call relations**: SseTokenUsage._absorb_anthropic and SseTokenUsage._openai use this to normalize provider usage fields before creating accounting records.

*Call graph*: called by 2 (_absorb_anthropic, _openai).


##### `SseTokenUsage.feed`  (lines 746–756)

```
def feed(self, chunk: bytes) -> None
```

**Purpose**: Accepts response bytes as they pass through the proxy and breaks them into lines for token-usage parsing. It avoids buffering an unlimited response.

**Data flow**: It receives a response chunk, appends it to a small buffer, repeatedly removes complete lines and sends them to _consume, and stops parsing if an unfinished line grows beyond the configured limit.

**Call relations**: EgressProxy._mitm passes this as the downstream callback to _relay for model hosts, so usage can be observed while the client still receives the stream promptly.

*Call graph*: calls 1 internal fn (_consume).


##### `SseTokenUsage.usage`  (lines 758–768)

```
def usage(self) -> tuple[str, Usage] | None
```

**Purpose**: Returns the model name and token counts recovered from a response, if any were found. It also gives compact non-streaming JSON bodies one last chance to be parsed.

**Data flow**: It checks whether usage has already been seen; if not, it tries to parse the remaining buffer as a JSON body. If usage is still absent, it returns nothing; otherwise it creates and returns a Usage object with input, output, cache-read, and cache-write counts.

**Call relations**: EgressProxy._meter_tokens calls this after relaying a model response. It may call _maybe_json_body to handle non-streaming responses.

*Call graph*: calls 1 internal fn (_maybe_json_body); 1 external calls (__init__).


##### `SseTokenUsage._consume`  (lines 770–787)

```
def _consume(self, line: bytes) -> None
```

**Purpose**: Parses one line from a response stream and routes it to the correct provider-specific usage parser. It understands server-sent event lines, where data is carried after a "data:" prefix.

**Data flow**: It receives one line of bytes, strips it, and if it is not a data event it tries the JSON-body parser. For JSON data events, it decodes the payload and calls the Anthropic or OpenAI parser depending on the host.

**Call relations**: SseTokenUsage.feed calls this for each complete line. It hands work to _anthropic, _openai, or _maybe_json_body.

*Call graph*: calls 3 internal fn (_anthropic, _maybe_json_body, _openai); called by 1 (feed); 1 external calls (loads).


##### `SseTokenUsage._maybe_json_body`  (lines 789–810)

```
def _maybe_json_body(self, payload: bytes) -> None
```

**Purpose**: Parses token usage from a non-streaming JSON response body. This covers model calls that return one JSON object instead of server-sent events.

**Data flow**: It receives candidate JSON bytes, ignores them if usage was already seen or the bytes do not look like JSON, decodes the object, then extracts usage using the Anthropic or OpenAI path for the current host.

**Call relations**: SseTokenUsage._consume calls this for non-event lines, and SseTokenUsage.usage calls it for any final buffered body. It delegates provider-specific fields to _absorb_anthropic or _openai.

*Call graph*: calls 2 internal fn (_absorb_anthropic, _openai); called by 2 (_consume, usage); 1 external calls (loads).


##### `SseTokenUsage._anthropic`  (lines 812–822)

```
def _anthropic(self, event: dict[str, object]) -> None
```

**Purpose**: Extracts token information from Anthropic streaming events. Anthropic reports initial input/cache usage near message start and output usage later in message deltas.

**Data flow**: It receives a decoded Anthropic event dictionary, checks the event type, stores the model name from message_start when present, and passes usage blocks to _absorb_anthropic with the right initial-or-delta flag.

**Call relations**: SseTokenUsage._consume calls this for Anthropic host data events. It relies on _absorb_anthropic to normalize the actual token fields.

*Call graph*: calls 1 internal fn (_absorb_anthropic); called by 1 (_consume).


##### `SseTokenUsage._absorb_anthropic`  (lines 824–834)

```
def _absorb_anthropic(self, usage: object, initial: bool) -> None
```

**Purpose**: Stores Anthropic token counts from a usage block. It separates input tokens, output tokens, and cache-related tokens for later billing.

**Data flow**: It receives a usage object and a flag saying whether this is the initial usage block. For initial blocks it records input, cache-read, and cache-write counts; whenever output_tokens is present it records output count; then it marks usage as seen.

**Call relations**: SseTokenUsage._anthropic and SseTokenUsage._maybe_json_body call this after finding Anthropic usage data. It uses _int_field to read integer fields safely.

*Call graph*: calls 1 internal fn (_int_field); called by 2 (_anthropic, _maybe_json_body).


##### `SseTokenUsage._openai`  (lines 836–845)

```
def _openai(self, event: dict[str, object]) -> None
```

**Purpose**: Extracts token usage from OpenAI-style response objects. It reads the model name and the prompt and completion token counts.

**Data flow**: It receives a decoded event dictionary, stores the model name when present, checks for a usage dictionary, reads prompt_tokens and completion_tokens safely, and marks usage as seen.

**Call relations**: SseTokenUsage._consume calls this for OpenAI stream events, and SseTokenUsage._maybe_json_body calls it for non-streaming OpenAI JSON bodies.

*Call graph*: calls 1 internal fn (_int_field); called by 2 (_consume, _maybe_json_body).


##### `_respond`  (lines 848–855)

```
async def _respond(writer: asyncio.StreamWriter, status: int, message: str) -> None
```

**Purpose**: Writes a simple final HTTP response to the client, usually for refusals or proxy errors. It ignores vanished clients because the connection is closing anyway.

**Data flow**: It receives a stream writer, status code, and message, writes an HTTP status line followed by a blank line, drains the writer, and silently swallows socket errors.

**Call relations**: EgressProxy._handle uses this for denied or unsupported CONNECT requests, EgressProxy._tunnel uses it when upstream cannot be reached, and EgressProxy._forward_broker uses it for broker/body errors.

*Call graph*: called by 3 (_forward_broker, _handle, _tunnel); 2 external calls (drain, write).


### `core/src/ufo/sandbox/fs_creds.py`

`domain_logic` · `sandbox startup / workspace mount`

A sandbox needs to see its own workspace files, but it should not receive the project's full storage keys. This file solves that by minting a temporary, tightly limited credential for each conversation. Think of it like giving a visitor a one-hour keycard that opens only one room, not the whole building.

The workspace path is always built as `conversations/<conversation id>/workspace`. The conversation transcript and other framework records live above that folder, so a credential scoped to the workspace cannot reach them. The file builds an AWS-style policy, which is a small JSON rule document saying exactly which objects may be read, written, deleted, or listed.

`SandboxFsCredentialMinter` is the main piece. At sandbox bring-up, it asks STS, the Security Token Service that issues temporary cloud credentials, to assume a role with this extra restrictive policy attached. The same shape works for AWS STS and MinIO STS. The returned access key, secret key, and session token are packaged as `SandboxFsCredentials`, ready for the sandbox filesystem mount to use.

The important safety behavior is that even if these credentials leak from inside the sandbox, they only expose that sandbox's own workspace prefix and expire after a short time.

#### Function details

##### `StsClient.assume_role`  (lines 36–38)

```
async def assume_role(self, *, RoleArn: str, RoleSessionName: str, Policy: str, DurationSeconds: int) -> Any
```

**Purpose**: This is the expected shape of any STS client used by the credential minter. It says: given a role, session name, limiting policy, and lifetime, the client must return temporary credentials.

**Data flow**: It receives the role name, a short session label, a policy string, and a duration in seconds. A real implementation sends those details to an STS service and returns the service response containing temporary credentials.

**Call relations**: This protocol lets `SandboxFsCredentialMinter.mint` work with either the real `AwsStsClient` or another compatible STS client, such as a test double or MinIO-backed client.


##### `workspace_key_prefix`  (lines 41–46)

```
def workspace_key_prefix(conversation_id: UUID) -> str
```

**Purpose**: This builds the storage path for one conversation's sandbox workspace. It is the single prefix that the sandbox is allowed to mount and access.

**Data flow**: It takes a conversation UUID and formats it into `conversations/<id>/workspace`. The output is a plain string used as the safe storage boundary for that conversation.

**Call relations**: `SandboxFsCredentialMinter.mint` calls this when preparing a new credential. The returned prefix is then passed into `workspace_prefix_policy` so the temporary credential is locked to this exact workspace.

*Call graph*: called by 1 (mint).


##### `workspace_prefix_policy`  (lines 49–78)

```
def workspace_prefix_policy(bucket: str, key_prefix: str) -> str
```

**Purpose**: This creates the restrictive storage policy for a sandbox credential. The policy allows object reads, writes, deletes, and folder listing only inside one workspace prefix.

**Data flow**: It receives the bucket name and the allowed key prefix. It turns those into a compact JSON policy string that names the allowed object path and limits bucket listing to that same prefix.

**Call relations**: `SandboxFsCredentialMinter.mint` calls this just before asking STS for credentials. Internally it uses `json.dumps` to turn the policy data into the JSON text that STS expects.

*Call graph*: called by 1 (mint); 1 external calls (dumps).


##### `AwsStsClient.assume_role`  (lines 90–99)

```
async def assume_role(self, *, RoleArn: str, RoleSessionName: str, Policy: str, DurationSeconds: int) -> Any
```

**Purpose**: This is the real STS call used to request temporary credentials from AWS STS or a compatible MinIO STS endpoint. It wraps the network client so the rest of the code does not need to know the details of opening and closing that client.

**Data flow**: It receives the role ARN, session name, policy, and duration. It opens an STS client, sends an `assume_role` request with those values, waits for the response, and returns that response unchanged.

**Call relations**: This function is the concrete implementation of the `StsClient.assume_role` protocol. `SandboxFsCredentialMinter.mint` can call it through the protocol, and this function uses `AwsStsClient._client` to create the actual service connection.

*Call graph*: calls 1 internal fn (_client).


##### `AwsStsClient._client`  (lines 101–104)

```
def _client(self) -> ClientCreatorContext
```

**Purpose**: This prepares a fresh asynchronous STS client for one credential request. It chooses the configured endpoint and region, defaulting the region when none is given.

**Data flow**: It reads the client's stored endpoint URL and region. It asks `aiobotocore` for a session and creates an STS client configured for AWS or MinIO, then returns a context object that can be used with `async with`.

**Call relations**: `AwsStsClient.assume_role` calls this right before making the network request. This keeps client creation in one place and mirrors the idea that each minting operation uses a fresh service client.

*Call graph*: called by 1 (assume_role); 1 external calls (get_session).


##### `SandboxFsCredentialMinter.mint`  (lines 122–134)

```
async def mint(self, conversation_id: UUID) -> SandboxFsCredentials
```

**Purpose**: This creates a new short-lived credential for one conversation's sandbox filesystem mount. The credential is usable only for that conversation's workspace folder.

**Data flow**: It receives a conversation UUID. It builds the workspace prefix, builds a policy for that prefix, asks STS to assume the configured role with that policy and lifetime, then extracts the access key, secret key, and session token from the response and returns them as `SandboxFsCredentials`.

**Call relations**: This is called during workspace mounting by `core/src/ufo/loop/queue._workspace_mount`. It coordinates the helper functions `workspace_key_prefix` and `workspace_prefix_policy`, then hands the resulting request to the configured STS client so the sandbox can mount storage without receiving broad storage access.

*Call graph*: calls 2 internal fn (workspace_key_prefix, workspace_prefix_policy); called by 1 (_workspace_mount); 1 external calls (__init__).


### `core/src/ufo/sandbox/proxy/rules.py`

`domain_logic` · `per-deploy and per-turn proxy rule derivation`

The sandbox runs untrusted or semi-trusted work, so it should not be able to call any internet address it wants or see raw credentials. This file is the rule factory for that boundary. Think of it like writing a temporary visitor pass: the pass says which doors can be opened, which badge number should be swapped for a real key at the front desk, and which visits must be logged.

The rules here are deliberately derived from trusted facts, not registered by outside code. A selected model, such as a GPT or Claude model, implies a known provider host and a known authentication header. From that, the file creates rules that allow only that provider, replace a harmless sentinel value with the real model API key on the wire, and count usage as token spending.

OAuth grants work differently. A grant allows access to a connector's host, and sometimes to extra file-transfer hosts declared by connector manifests. These requests are metered as ordinary requests. But grants do not inject tokens into the sandbox. If a connector supports command-line-style credential forwarding, matching requests are sent through the broker, which holds the real account token server-side. This keeps secrets out of the sandbox while still letting approved work happen.

#### Function details

##### `provider_host`  (lines 80–84)

```
def provider_host(model: str) -> str
```

**Purpose**: This function looks at a model name and chooses the internet host for the model provider. For example, names starting with GPT-style prefixes go to OpenAI, while Claude-style names go to Anthropic.

**Data flow**: It receives a model name as text. It compares that name against known prefixes, and when one matches, it returns the matching provider host. If no prefix is recognized, it stops with an error instead of guessing, because allowing the wrong host would weaken the proxy's safety rules.

**Call relations**: It is used by derive_model_rules when building proxy rules for the deploy's selected model. It supplies the provider host that later rules use for allowlisting, credential injection, and metering.

*Call graph*: called by 1 (derive_model_rules).


##### `derive_model_rules`  (lines 87–102)

```
def derive_model_rules(model: str, real_key: str) -> tuple[Rule, ...]
```

**Purpose**: This function builds the proxy rules needed for the sandbox to call the deploy's model provider safely. It allows the provider host, arranges for the real API key to be swapped in only outside the sandbox, and marks the traffic as token-metered.

**Data flow**: It receives a model name and the real provider API key. First it asks provider_host which provider host belongs to the model. Then it chooses the right authentication header format for that provider. It returns three rules: one allowing the host, one replacing the sandbox's sentinel value with the real key on outgoing traffic, and one counting requests to that host under token usage.

**Call relations**: This is the model-provider branch of rule creation. It calls provider_host to identify the destination, then creates ScopeRule, InjectionRule, and MeterRule values that the egress proxy can later read when deciding whether and how to send network traffic.

*Call graph*: calls 1 internal fn (provider_host); 3 external calls (__init__, __init__, __init__).


##### `derive_grant_rules`  (lines 105–119)

```
def derive_grant_rules(grants: tuple[Grant, ...], transfer_hosts: Mapping[str, tuple[str, ...]] | None=None) -> tuple[Rule, ...]
```

**Purpose**: This function builds proxy rules from active OAuth grants. A grant means the sandbox may reach that connector's host, plus any declared file-transfer hosts, and all such traffic should be counted as request spending.

**Data flow**: It receives a tuple of grants and, optionally, a map of extra transfer hosts by provider. For each grant, it gathers the grant's main host and any transfer hosts for that provider. It then produces an allow rule for those hosts and a request-metering rule for each host. It does not create any secret-injection rule, because grant credentials stay with the broker.

**Call relations**: This is used when the system turns a user's currently approved connector access into proxy permissions. It creates ScopeRule and MeterRule values, which means granted hosts can be contacted and measured, while ungranted hosts remain blocked by default.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_cli_rules`  (lines 122–142)

```
def derive_cli_rules(grants: tuple[Grant, ...], acting_member_id: UUID | None, clis: Mapping[str, CliCredential]) -> tuple[Rule, ...]
```

**Purpose**: This function creates forwarding rules for grants whose connectors expose a command-line-style credential. Instead of placing a real token in the sandbox, it tells the proxy to send matching requests through the broker, where the real credential is held safely.

**Data flow**: It receives active grants, the member currently acting if there is one, and a map of CLI credentials by provider. For each grant, it checks whether that provider has a CLI credential and whether the acting member is allowed to use the grant. Allowed means the grant is shared, or it belongs to that member. For each allowed match, it returns a ForwardRule containing the host, header, sentinel value, account id, and forwarding function.

**Call relations**: This is the broker-forwarding branch of rule creation. It uses grant_sentinel to produce the harmless marker value that sandbox traffic will carry. When the proxy later sees that marker in the right header for the right host, the ForwardRule tells it to hand the request to the broker's forwarder instead of sending it directly.

*Call graph*: 2 external calls (__init__, grant_sentinel).


##### `connector_transfer_hosts`  (lines 145–154)

```
def connector_transfer_hosts(manifests: tuple[Manifest, ...]) -> dict[str, tuple[str, ...]]
```

**Purpose**: This function reads installed connector manifests and extracts the extra file-transfer hosts each connector says it needs. These are hosts used for broker file storage, such as fetching tool outputs or staging file inputs.

**Data flow**: It receives connector manifests. It walks through every connector in every manifest, keeps only connectors that declare transfer hosts, and returns a dictionary keyed by provider name. Each value is the tuple of transfer hosts for that provider.

**Call relations**: This prepares live manifest information for derive_grant_rules. By deriving transfer hosts from the current manifests instead of relying on an old stored copy, grant-based proxy permissions stay aligned with the connector deployment that is actually running.


### `core/src/ufo/sandbox/session.py`

`domain_logic` · `per-turn sandbox setup and tool execution`

Think of this file as the front desk for a rented workshop. Tools are allowed to use the workshop, run commands, and move files in and out, but only through this desk and only inside the assigned room. The file defines the shared language between the rest of the system and whatever sandbox backend is being used, such as Docker, a remote sandbox, or another carrier later on. A carrier is the piece that actually creates containers, runs commands, copies files, and exposes ports.

The file also carries important identity and safety information. `RunToken` marks each network request made from the sandbox with the exact workspace and turn that caused it, so billing or logging can tie traffic to the right conversation turn. `SandboxSpec` describes how to start or resume a sandbox, including what workspace to mount and how outbound proxy traffic should work. `SandboxHandle` is the returned reference to the live sandbox.

The `SandboxSession` class is what tools actually use during a turn. It offers simple actions like running bash, writing a file, checking a file, exporting a file, or asking for a reachable host. Before file paths are used, `workspace_path` cleans and checks them so tricks like `../` cannot escape `/workspace`. Without this file, tools would either depend directly on one sandbox implementation, or worse, could accidentally read or change files outside the workspace.

#### Function details

##### `RunToken.encode`  (lines 39–41)

```
def encode(self) -> str
```

**Purpose**: Turns a workspace ID and turn ID into a compact, URL-safe token. This lets the sandbox proxy identify which conversation turn caused an outbound network request.

**Data flow**: It starts with two UUID values: the workspace ID and turn ID. It joins them with a slash, converts that text to bytes, encodes it using URL-safe base64, removes padding characters, and returns the token string.

**Call relations**: When the egress proxy builds rules for sandbox network traffic, it calls this method to create the token that will be carried in proxy authentication. Later, the proxy can decode the same token to write traffic records against the right workspace and turn.

*Call graph*: called by 1 (_rules_for); 1 external calls (urlsafe_b64encode).


##### `RunToken.from_proxy_auth`  (lines 44–56)

```
def from_proxy_auth(cls, header: str) -> 'RunToken'
```

**Purpose**: Reads a `Proxy-Authorization` header and recovers the workspace and turn that made the request. It deliberately raises an error if the header is missing, not Basic authentication, or malformed, because untracked sandbox traffic is treated as a serious wiring mistake.

**Data flow**: It receives the raw proxy authorization header text. It checks that the scheme is Basic authentication, decodes the Basic-auth value, takes the username as the run token, restores base64 padding if needed, decodes the token into `workspace_id/turn_id`, converts both pieces into UUID objects, and returns a `RunToken`.

**Call relations**: The egress proxy uses this when writing egress records and sandbox token records, and through its run-token helper. In the larger flow, outbound sandbox traffic arrives with a header, this function identifies the turn behind it, and the proxy can then account for that request correctly.

*Call graph*: called by 3 (_write_egress, _write_sandbox_tokens, _run_token); 3 external calls (b64decode, urlsafe_b64decode, UUID).


##### `format_sandbox_handle`  (lines 136–140)

```
def format_sandbox_handle(backend: str, container_id: str) -> str
```

**Purpose**: Builds the stored sandbox handle string that combines the backend name with that backend’s sandbox ID. The backend prefix matters because a sandbox created by one backend should not be resumed or cleaned up by another.

**Data flow**: It receives a backend name and a container or sandbox ID. It joins them with the handle separator and returns one durable string, such as a label that can be saved on a conversation record.

**Call relations**: This helper is used by sandbox carrier implementations when they need to persist a handle for later resume or cleanup. Its paired helper, `sandbox_handle_id`, later checks whether a saved handle belongs to the current backend before using it.


##### `sandbox_handle_id`  (lines 143–147)

```
def sandbox_handle_id(backend: str, value: str) -> str | None
```

**Purpose**: Extracts the sandbox ID from a stored handle, but only if it belongs to the backend currently asking. This prevents a newly deployed backend from accidentally touching sandboxes owned by an older or different backend.

**Data flow**: It receives the current backend name and a stored handle string. If the handle starts with that backend name plus the separator, it returns the remaining sandbox ID; otherwise it returns `None`.

**Call relations**: This is the reverse side of `format_sandbox_handle`. Carrier code can use it before resuming or reaping a saved sandbox, so it only acts on handles that were written by the same kind of carrier.


##### `Carrier.create`  (lines 162–162)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Defines the contract for creating or attaching to a sandbox for a conversation. The real work is done by a carrier implementation, such as Docker or a remote sandbox service.

**Data flow**: It receives a `SandboxSpec`, which describes the conversation, image, workspace mount, proxy setup, run token, resume ID, and environment. An implementation uses that information to start or reconnect to a sandbox and returns a `SandboxHandle` that refers to it.

**Call relations**: The queue-opening flow calls this when it needs a sandbox for a turn. After creation, the returned handle is wrapped in a `SandboxSession`, which tools use for commands and files without knowing which backend created the sandbox.

*Call graph*: called by 1 (_open_sandbox).


##### `Carrier.exec`  (lines 164–166)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int) -> ExecResult
```

**Purpose**: Defines how to run a command inside the sandbox. It is the shared doorway used by higher-level session methods for shell commands and file operations.

**Data flow**: It receives a sandbox handle, command arguments, optional standard input bytes, and a timeout. A carrier implementation runs the command in the sandbox and returns an `ExecResult` containing standard output, standard error, and the exit code.

**Call relations**: The session methods such as `bash`, `write_file`, `ensure_tool_output_dir`, `file_exists`, and `run_sbxfs` rely on this contract. Each method turns a user-friendly action into a concrete sandbox command, then hands it to the carrier through `exec`.


##### `Carrier.export`  (lines 168–173)

```
async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None
```

**Purpose**: Defines how to copy a file from the sandbox workspace into blob storage without loading the whole file into the host process. This is important for large attachments or generated outputs.

**Data flow**: It receives the sandbox handle, a workspace path, a blob store, and the destination key. A carrier implementation streams the file from wherever the workspace lives into the blob store under that key, and returns nothing when complete.

**Call relations**: The `SandboxSession.export_file` method first checks and normalizes the path, then calls this carrier method. Different carriers can implement the copy differently, but tools get the same safe export behavior.


##### `Carrier.destroy`  (lines 175–175)

```
async def destroy(self, handle: SandboxHandle) -> None
```

**Purpose**: Defines how to reclaim or shut down a sandbox when it is no longer needed. The workspace is treated as durable, while the sandbox itself is disposable working space.

**Data flow**: It receives a sandbox handle. A carrier implementation uses that handle to stop, remove, or otherwise release the sandbox resources, and returns nothing.

**Call relations**: Cleanup and reaper flows can call this through the carrier abstraction. Because this is part of the shared carrier contract, the rest of the system does not need to know whether cleanup means removing a Docker container or closing a remote sandbox.


##### `Carrier.host`  (lines 177–183)

```
async def host(self, handle: SandboxHandle, port: int) -> str
```

**Purpose**: Defines how to get an outside-reachable address for a service running on a port inside the sandbox. This is used when something inside the sandbox, like a browser debugging endpoint or preview server, needs to be reached by the main process.

**Data flow**: It receives a sandbox handle and an internal port number. A carrier implementation maps that internal port to a host name or host-and-port string that an outside caller can dial, or raises an error if that carrier cannot expose ports.

**Call relations**: The session’s `host` method delegates to this contract. The sandbox Chrome extension calls the session method when it needs to connect to a browser service that was started inside the sandbox.


##### `workspace_path`  (lines 186–194)

```
def workspace_path(path: str) -> str
```

**Purpose**: Turns a tool-supplied file path into a safe absolute path under `/workspace`. It blocks path tricks that try to escape the allowed workspace area.

**Data flow**: It receives a path string, treats relative paths as being inside `/workspace`, breaks the path into parts, asks `_resolve_parts` to process `.` and `..`, then verifies that the result is still `/workspace` or below it. It returns the cleaned path or raises `ValueError` if the path escapes.

**Call relations**: File-related session methods call this before touching any caller-supplied path. It is the common guard used by writing files, checking files, running sandbox file operations, and exporting files.

*Call graph*: calls 1 internal fn (_resolve_parts); called by 4 (export_file, file_exists, run_sbxfs, write_file); 1 external calls (PurePosixPath).


##### `_resolve_parts`  (lines 197–206)

```
def _resolve_parts(parts: tuple[str, ...]) -> list[str]
```

**Purpose**: Cleans the pieces of a POSIX-style path and detects attempts to climb above the workspace root. It is the low-level path stack used by `workspace_path`.

**Data flow**: It receives the path split into pieces. It walks through them, ignores empty pieces and `.`, pops one saved part for `..`, and raises an error if `..` would climb too far upward. It returns the cleaned list of path parts.

**Call relations**: `workspace_path` calls this while building a safe workspace path. This helper keeps the escape-detection logic small and separate from the final workspace-root check.

*Call graph*: called by 1 (workspace_path).


##### `SandboxSession.bash`  (lines 218–224)

```
async def bash(self, command: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a shell command inside the sandbox for the current turn. This gives tools a simple way to execute normal command-line work without knowing how the sandbox backend works.

**Data flow**: It receives a command string and an optional timeout. It wraps the command as `bash -lc`, sends no standard input, chooses the given timeout or the default timeout, and returns the carrier’s `ExecResult` with output, errors, and exit code.

**Call relations**: The sandbox Chrome extension uses this through its run helper when it needs to execute commands in the sandbox. Internally, this method hands the actual execution to the carrier’s `exec` method.

*Call graph*: called by 1 (_run).


##### `SandboxSession.write_file`  (lines 226–235)

```
async def write_file(self, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes to a file inside the workspace, creating parent folders if needed. It refuses paths outside the workspace before asking the sandbox to write anything.

**Data flow**: It receives a target path and byte content. It first converts the path with `workspace_path`, then runs a small shell command in the sandbox that creates the parent directory and writes the bytes from standard input into the file. If the command fails, it raises `OSError`; otherwise it returns nothing.

**Call relations**: The skill runtime uses this when mounting a skill’s files into the sandbox. This method protects the path, then delegates the actual write to the carrier’s `exec` command channel.

*Call graph*: calls 1 internal fn (workspace_path); called by 1 (mount_skill).


##### `SandboxSession.ensure_tool_output_dir`  (lines 237–260)

```
async def ensure_tool_output_dir(self) -> bool
```

**Purpose**: Makes sure the engine’s private `.tool-output` directory exists inside the workspace. If a file or broken link is squatting on that name, it removes that squatter and creates the directory.

**Data flow**: It runs a fixed shell script against the fixed tool-output path. If the path is already a directory, nothing changes. If a file or symlink is there, it removes it, prints a marker, and creates the directory. It returns `true` if something was reclaimed, `false` otherwise, and raises `OSError` if the command fails.

**Call relations**: This is used when the engine needs a reliable place for tool output offloading. Unlike methods that accept user paths, it only touches the engine-owned output directory, then delegates the filesystem work to the carrier through `exec`.


##### `SandboxSession.file_exists`  (lines 262–267)

```
async def file_exists(self, path: str) -> bool
```

**Purpose**: Checks whether a regular file exists inside the workspace. It uses the same workspace path guard as other file actions.

**Data flow**: It receives a path string, converts it with `workspace_path`, and runs `test -f` inside the sandbox. It returns `true` if the command exits successfully and `false` otherwise.

**Call relations**: Other code can use this session method when it needs a simple yes-or-no file check. The method turns that question into a safe sandbox command and sends it through the carrier’s `exec` path.

*Call graph*: calls 1 internal fn (workspace_path).


##### `SandboxSession.run_sbxfs`  (lines 269–297)

```
async def run_sbxfs(self, op: str, args: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a structured in-sandbox file operation through the `sbxfs` command-line tool and returns its JSON result. This lets large or complex file work happen inside the sandbox instead of dragging whole files into the host process.

**Data flow**: It receives an operation name and a dictionary of arguments. If the arguments contain a string `path`, it first scopes that path to the workspace. It serializes the arguments as compact JSON, runs `sbxfs` in the sandbox, trims and parses the JSON output, checks that it is an object, turns reported `error` values into `ValueError`, and returns the parsed dictionary.

**Call relations**: This is the session’s doorway to richer file operations such as windowed reads, searches, or renders performed by `sbxfs`. It depends on `workspace_path` for path safety and on the carrier’s `exec` method for running the actual sandbox command.

*Call graph*: calls 1 internal fn (workspace_path); 2 external calls (dumps, loads).


##### `SandboxSession.export_file`  (lines 299–300)

```
async def export_file(self, path: str, blob: BlobStore, key: str) -> None
```

**Purpose**: Exports one workspace file into blob storage. It keeps the path safe, then lets the carrier stream the file out efficiently.

**Data flow**: It receives a path, a blob store, and a destination key. It converts the path with `workspace_path`, then calls the carrier’s `export` method with the sandbox handle, safe path, blob store, and key. It returns nothing after the export completes.

**Call relations**: This method is the safe public session wrapper around the carrier export contract. It ensures callers cannot export files outside `/workspace`, while still allowing each carrier to choose the best way to stream the file.

*Call graph*: calls 1 internal fn (workspace_path).


##### `SandboxSession.host`  (lines 302–306)

```
async def host(self, port: int) -> str
```

**Purpose**: Asks the carrier for an address that the main process can use to reach a service running inside the sandbox on a given port.

**Data flow**: It receives a port number. It passes the current sandbox handle and that port to the carrier, waits for the carrier’s mapping, and returns the reachable host string.

**Call relations**: The sandbox Chrome CDP provider calls this when it leases a browser debugging connection. This session method does not decide how networking works; it delegates that backend-specific mapping to the carrier.

*Call graph*: called by 1 (lease).


##### `SandboxSession.traffic_token`  (lines 309–312)

```
def traffic_token(self) -> str | None
```

**Purpose**: Returns the sandbox’s traffic token when the carrier requires one for public port access. Some remote backends use this token as an extra connection header.

**Data flow**: It reads the `traffic_token` stored on the session’s sandbox handle and returns that string, or `None` if the carrier did not provide one.

**Call relations**: Code that dials a host returned by `SandboxSession.host` can also read this property when it needs the matching token. The value is supplied by the carrier when the sandbox handle is created.


### Workspace isolation
These files establish workspace-scoped execution and database row-level protections for multi-tenant safety.

### `core/src/ufo/workspace.py`

`orchestration` · `cross-cutting during request handling and background jobs`

This file is the system’s “you are here” marker for workspace-bound work. A workspace is the customer or tenant area a request or background job belongs to. Many important actions depend on that identity: reading the right secret key, charging usage to the right account, and making database queries see only the right workspace’s data.

The main idea is simple: code enters a block using `ws(workspace_id)`, and everything inside that block can ask `ws_current()` for the active workspace. This is like putting on a visitor badge at the door; once inside, every guarded room can check the badge instead of asking you to repeat your name each time.

Secrets are fetched through `WorkspaceScope.credential`. It first tries the workspace’s own stored credential, often called BYOK (“bring your own key”, meaning the customer supplied their own key). If none is stored, it falls back to a platform-wide environment variable. If neither exists, it fails clearly.

Billing works through `billable_event()`. Code records model usage during the block, but nothing is written until the block finishes successfully. If an error happens, the usage is discarded, so failed work is not billed. This file therefore protects against three serious mistakes: using another workspace’s secret, billing the wrong workspace, or doing workspace-scoped work with no workspace at all.

#### Function details

##### `init_workspace_credentials`  (lines 27–31)

```
def init_workspace_credentials(store: CredentialStore | None) -> None
```

**Purpose**: Installs the credential store that knows how to read and write workspace-owned secrets. It is meant to be called once during startup so later workspace code can look up customer-provided credentials.

**Data flow**: It receives either a credential store object or `None`. It saves that value in this module so future credential lookups can use it. If `None` is saved, workspace-specific stored credentials are unavailable and lookups can only fall back to environment variables.

**Call relations**: Startup code calls this before normal work begins. Later, `WorkspaceScope.credential`, `WorkspaceScope.rotate_credential`, and `WorkspaceScope.put_credential` rely on the stored value to decide whether workspace-specific secrets can be read or changed.


##### `BillableEvent.usage`  (lines 47–49)

```
def usage(self, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: Adds one piece of billable model usage to the current billing event. Code uses this after a metered operation, such as an AI model call, so the cost can later be charged to the workspace.

**Data flow**: It receives a model name, a usage record, and pricing rules. It stores those together in the event’s private list. It does not write to the database immediately; it only remembers the usage for the surrounding billing block.

**Call relations**: This is used inside a `WorkspaceScope.billable_event` block. The billing block later reads the saved usage entries and hands them to the accounting layer if the block finishes without an error.


##### `WorkspaceScope.credential`  (lines 58–72)

```
async def credential(self, slot: str, env: str | None=None) -> str
```

**Purpose**: Returns the secret value for a named credential slot in this workspace. It protects the system from accidentally using a key without first knowing which workspace the key belongs to.

**Data flow**: It takes a slot name, such as a service key name, and optionally the name of an environment variable. First it asks the configured credential store for this workspace’s saved secret. If the store has no value for that slot, it reads a fallback value from the environment. If both are missing or empty, it raises `CredentialSlotUnset` instead of returning a bad value.

**Call relations**: Callers reach this through `ws_current().credential(...)`, meaning they must already be inside a `ws(...)` workspace block. It talks to the credential store when one is configured, and uses `CredentialSlotUnset` to signal that a needed secret is not available.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceScope.rotate_credential`  (lines 74–79)

```
async def rotate_credential(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing stored workspace credential only if the caller’s expected current value matches. This compare-before-change behavior helps avoid overwriting a secret that changed since the caller last checked.

**Data flow**: It receives a slot name, the expected existing secret, and the new plaintext secret. If no credential store is configured, it returns `False` because there is nowhere to rotate a stored workspace secret. Otherwise it asks the store to perform the safe replacement and returns whether that replacement succeeded.

**Call relations**: This is used by code that updates customer-owned credentials for the active workspace. It depends on the credential store installed by `init_workspace_credentials`; it does not affect platform fallback secrets stored in environment variables.


##### `WorkspaceScope.put_credential`  (lines 81–85)

```
async def put_credential(self, slot: str, plaintext: str) -> None
```

**Purpose**: Stores an initial credential value for this workspace. It is for authorized setup flows where a workspace owner provides a secret to save.

**Data flow**: It receives a slot name and the plaintext secret to store. If no credential store is configured, it raises an error because saving is impossible. Otherwise it passes the workspace id, slot, and secret to the credential store.

**Call relations**: This is called by credential setup code after a workspace has been bound. Like other credential operations, it relies on the credential store installed at startup by `init_workspace_credentials`.


##### `WorkspaceScope.billable_event`  (lines 88–98)

```
async def billable_event(self) -> AsyncIterator[BillableEvent]
```

**Purpose**: Creates a safe billing window for work done by this workspace. Usage can be collected inside the window, and it is only written to the workspace’s ledger if the work finishes successfully.

**Data flow**: It creates an empty `BillableEvent` and gives it to the caller’s block. The caller adds usage records to that event. When the block exits normally, it opens a workspace database transaction and records each usage entry for this workspace. If no usage was added, it writes nothing. If the block raises an error, the code after the yield is skipped, so the usage is not billed.

**Call relations**: Callers use this as `async with ws_current().billable_event() as bill`. Inside, they call `BillableEvent.usage`. On success, this function opens `workspace_tx`, then hands each usage item to `record_workspace_usage` in the accounting layer.

*Call graph*: 3 external calls (__init__, record_workspace_usage, workspace_tx).


##### `ws`  (lines 102–110)

```
def ws(workspace_id: UUID) -> Iterator[WorkspaceScope]
```

**Purpose**: Temporarily binds a workspace id as the active workspace for a block of code. This lets everything inside the block find the workspace without passing the id through every function call.

**Data flow**: It receives a workspace id. It saves that id in the current execution context, yields a `WorkspaceScope` object for the block to use, and then restores the previous context when the block ends. The cleanup happens even if the block exits because of an error.

**Call relations**: Request handlers and background jobs use this at their boundary, before doing workspace-specific work. While it is active, `ws_current()` can retrieve the workspace, and database helpers that read `current_workspace` can apply the same workspace scope.

*Call graph*: 3 external calls (__init__, reset, set).


##### `ws_current`  (lines 113–119)

```
def ws_current() -> WorkspaceScope
```

**Purpose**: Returns the currently bound workspace scope. If no workspace has been bound, it fails loudly so code cannot accidentally read secrets, query data, or bill usage without a workspace identity.

**Data flow**: It reads the workspace id from the current execution context. If an id is present, it wraps it in a `WorkspaceScope` and returns it. If no id is present, it raises `WorkspaceUnbound` with a clear message telling the caller to use `ws(workspace_id)` first.

**Call relations**: Workspace-aware code calls this when it needs credentials, billing, or the current workspace id. It depends on `ws` having already set the context; if that setup step was missed, this function stops the flow before unsafe work can continue.

*Call graph*: 3 external calls (__init__, __init__, get).


### `control/src/ufo_control/rls.py`

`domain_logic` · `startup / database bootstrap`

This file is about preventing one workspace from seeing or changing another workspace’s rows in the shared database. PostgreSQL row-level security, often called RLS, is a database feature that acts like a guard at each table row: even if a user can query a table, PostgreSQL only lets through rows that match a rule. Here, that rule says a row belongs to the current workspace, using a database setting named app.workspace_id.

The file has two main jobs. First, it prepares the database role used by the running service. That role is called ufo_serve. Its password is derived from a secret seed, so the password can be recreated consistently without storing it directly. The setup also grants the role table and sequence access, sets timeouts, and creates a companion database if needed.

Second, it scans every public table and makes sure the expected workspace policy exists. The migration bookkeeping table is skipped. For each real table, it first checks whether the table already has the exact correct policy. If not, it enables RLS, drops the old managed policy if present, and recreates it. If PostgreSQL cannot get a lock because another session is blocking the table, the code fails quickly and reports who is holding the lock. That makes startup safer: it does not silently run with missing isolation rules, and it does not hang forever.

#### Function details

##### `owner_dsn`  (lines 22–26)

```
def owner_dsn() -> str
```

**Purpose**: Reads the database connection string for the privileged PostgreSQL owner account. This is needed for setup work that ordinary application users should not be allowed to do.

**Data flow**: It reads the UFO_CONTROL_POSTGRES_OWNER_DSN environment variable. If the value is present, it returns that connection string. If it is missing, it raises an error so the program cannot continue without the credentials needed to enforce the database boundary.

**Call relations**: This is a small entry helper for code that needs the owner connection details. It does not call other project functions; it simply turns required environment configuration into a usable value or a clear failure.


##### `serve_password`  (lines 29–33)

```
def serve_password() -> str
```

**Purpose**: Creates the password for the service database role from a shared secret seed. This avoids hard-coding the service password while still making it predictable for setup and connection building.

**Data flow**: It reads the UFO_CONTROL_PG_ROLE_SEED environment variable. It combines that seed with the service role name, hashes the result with SHA-256, and returns the hash text as the password. If the seed is missing, it raises an error because the service role cannot be created or used safely.

**Call relations**: Both role setup and connection-string creation rely on this function. ensure_serve_role uses it before creating or updating the PostgreSQL role, and serve_dsn uses it when building the URL that the application will use to connect.

*Call graph*: called by 2 (ensure_serve_role, serve_dsn); 1 external calls (sha256).


##### `serve_dsn`  (lines 36–37)

```
def serve_dsn(postgres_host: str, app_database: str) -> str
```

**Purpose**: Builds the database connection string for the service role. Callers use it when they need the application to connect as the restricted ufo_serve PostgreSQL user rather than as the database owner.

**Data flow**: It receives a PostgreSQL host name and an application database name. It asks serve_password for the service role password, then puts the role name, password, host, and database into an async PostgreSQL connection URL. The result is a ready-to-use connection string.

**Call relations**: This function sits after serve_password in the setup flow. It does not open the database itself; it hands a correctly formatted connection string to whatever part of the system will create the actual connection.

*Call graph*: calls 1 internal fn (serve_password).


##### `ensure_serve_role`  (lines 40–62)

```
async def ensure_serve_role(admin_dsn: str) -> None
```

**Purpose**: Makes sure the restricted service database role exists and has the right password, privileges, and safety timeouts. This is the preparation step that lets the application use the database without giving it owner-level power.

**Data flow**: It receives an administrator database connection string. It connects to PostgreSQL, derives the service password, sets a short lock timeout, creates or updates the ufo_serve role, allows that role to set the workspace identifier, applies idle transaction timeouts, grants table and sequence access, and ensures a related DBOS database exists. It changes database roles, permissions, and possibly creates a database; it returns nothing when successful.

**Call relations**: This is one of the main setup routines in the file. It calls serve_password to get the role password, _grant_serve_role to apply permissions, and _ensure_database to create the companion database if missing. It opens its own asyncpg connection and always closes it afterward.

*Call graph*: calls 3 internal fn (_ensure_database, _grant_serve_role, serve_password); 1 external calls (connect).


##### `bootstrap_policies`  (lines 65–92)

```
async def bootstrap_policies(dsn: str) -> None
```

**Purpose**: Walks through the public database tables and ensures each one has the expected workspace row-level security rule. This is what turns the idea of workspace separation into enforced database policy.

**Data flow**: It receives a database connection string, connects to PostgreSQL, sets a lock timeout, and fetches the list of public tables. It skips the migration version table. For each other table, it checks whether the current RLS policy is already correct; if not, it opens a short transaction and recreates the policy. If a table is locked too long, it gathers information about the blocking sessions and raises a clear error.

**Call relations**: This is the other main setup routine in the file. It uses _conformant as a quick check, _policy_for when a table needs repair, and _lock_holders when PostgreSQL reports a lock timeout. Like ensure_serve_role, it owns its database connection and closes it when done.

*Call graph*: calls 3 internal fn (_conformant, _lock_holders, _policy_for); 1 external calls (connect).


##### `_conformant`  (lines 95–123)

```
async def _conformant(connection: asyncpg.Connection, table: str) -> bool
```

**Purpose**: Checks whether a table already has the exact row-level security policy this system expects. It is a fast path that avoids changing tables that are already correct.

**Data flow**: It receives an open database connection and a table name. It reads PostgreSQL’s system catalogs to see whether row-level security is enabled and whether the named policy has the expected rule, command type, and role coverage. It also asks _scope_column which column should identify the workspace for that table. It returns true only when everything matches exactly; otherwise it returns false.

**Call relations**: bootstrap_policies calls this before doing any table-changing work. If _conformant says the table is already right, bootstrap_policies moves on. If it says no, bootstrap_policies calls _policy_for to rebuild the policy.

*Call graph*: calls 1 internal fn (_scope_column); called by 1 (bootstrap_policies); 1 external calls (fetchrow).


##### `_lock_holders`  (lines 126–141)

```
async def _lock_holders(connection: asyncpg.Connection, table: str) -> str
```

**Purpose**: Explains who is currently holding locks on a table. This makes lock timeout errors useful instead of mysterious.

**Data flow**: It receives an open database connection and a table name. It queries PostgreSQL lock and activity views for other sessions that hold granted locks on that table. It returns a readable summary including process id, role name, session state, transaction age, and the start of the blocking query, or a note that no holder is visible.

**Call relations**: bootstrap_policies calls this only after a lock timeout while checking or changing a table. The returned text is included in the raised error so an operator can see what blocked the bootstrap.

*Call graph*: called by 1 (bootstrap_policies); 1 external calls (fetch).


##### `_grant_serve_role`  (lines 144–157)

```
async def _grant_serve_role(connection: asyncpg.Connection) -> None
```

**Purpose**: Applies the database permissions that the service role needs to work with public tables and sequences. It gives broad table access while relying on row-level security to limit which rows are visible or writable.

**Data flow**: It receives an open database connection. It updates default privileges, grants usage on the public schema, grants select, insert, update, and delete on existing public tables, and grants usage on existing public sequences. These are database permission changes; the function returns nothing.

**Call relations**: ensure_serve_role calls this after creating or updating the service role. It is the permissions portion of the role setup story, while ensure_serve_role handles connection setup, password setup, and surrounding role settings.

*Call graph*: called by 1 (ensure_serve_role); 1 external calls (execute).


##### `_ensure_database`  (lines 160–163)

```
async def _ensure_database(connection: asyncpg.Connection, name: str, owner: str) -> None
```

**Purpose**: Creates a named PostgreSQL database if it does not already exist. In this file it is used to make sure a companion database owned by the service role is available.

**Data flow**: It receives an open database connection, a database name, and an owner role name. It checks PostgreSQL’s database catalog for that name. If the database is absent, it creates it with the requested owner. If it already exists, it leaves it alone.

**Call relations**: ensure_serve_role calls this near the end of service role setup. The role and permissions are prepared first, then this helper ensures the related database exists under the right owner.

*Call graph*: called by 1 (ensure_serve_role); 2 external calls (execute, fetchval).


##### `_policy_for`  (lines 166–173)

```
async def _policy_for(connection: asyncpg.Connection, table: str) -> None
```

**Purpose**: Creates or refreshes the workspace row-level security policy for one table. It is the repair step used when a table is missing the expected rule or has drifted away from it.

**Data flow**: It receives an open database connection and a table name. It asks _scope_column which column should be compared to the current workspace setting. Then it enables row-level security on the table, drops the managed policy if it already exists, and creates a new policy that allows reads and writes only when the row’s workspace value matches app.workspace_id.

**Call relations**: bootstrap_policies calls this inside a short transaction when _conformant reports that a table is not already correct. It depends on _scope_column so the workspace table itself can use its id column while other tables use workspace_id.

*Call graph*: calls 1 internal fn (_scope_column); called by 1 (bootstrap_policies); 1 external calls (execute).


##### `_scope_column`  (lines 176–190)

```
async def _scope_column(connection: asyncpg.Connection, table: str) -> str
```

**Purpose**: Decides which column marks the workspace for a table. This is needed because the workspace table identifies itself with id, while other workspace-owned tables are expected to have a workspace_id column.

**Data flow**: It receives an open database connection and a table name. If the table is the workspace table, it returns id. Otherwise it checks the table’s columns for workspace_id. If that column exists, it returns workspace_id. If not, it raises an error because the table cannot be safely protected by the standard workspace policy.

**Call relations**: _conformant calls this when comparing the existing policy to the expected expression, and _policy_for calls it when building a new policy. It is the shared rule that keeps policy checking and policy creation in agreement.

*Call graph*: called by 2 (_conformant, _policy_for); 1 external calls (fetchval).


### Provider secret containment
These files keep external-provider credentials declared, stored, retrieved, and proxied without exposing raw secrets to unsafe contexts.

### `extensions/sources/ufo_ext_sources/direct.py`

`domain_logic` · `feed-sync authentication`

Some source providers may not have a separate broker service that can supply credentials. In that case, a workspace member can add an API key directly for that provider. This file is the small bridge that lets the host-side sync job use that key safely.

The central idea is simple: the key stays in the system’s credential store, under the provider’s name. When feed-sync needs to call the provider’s HTTP API, `DirectAuthProxy` asks `CredentialAccess` for that provider’s stored secret. It then wraps the secret as a bearer credential, which means “send this token as proof of access” when making the provider request.

A key safety point is that this happens host-side, inside the sync job. The secret is not passed into the sandbox and is not exposed through the agent-facing surface. In everyday terms, this file is like a locked cabinet clerk: it can fetch the one labeled key the job is allowed to use, hand it directly to the caller that needs to open the provider’s door, and avoid showing it anywhere else.

The `account` value is accepted because the auth-proxy interface expects it, but for this direct-key model it is not used. The API key itself is the account-like proof.

#### Function details

##### `DirectAuthProxy.credential`  (lines 28–29)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: This function retrieves the stored API key for a provider and turns it into a credential object that can be used as a bearer token. It is used when the direct auth-proxy backend needs to authenticate a provider request without using a brokered account.

**Data flow**: It receives a workspace ID, a provider name, and an account handle. The provider name is used to read the matching secret from `CredentialAccess`; the account handle is ignored because the direct API key is the authentication. The secret that comes back is wrapped into a `Credential` as its bearer value, and that credential is returned to the caller.

**Call relations**: When feed-sync asks this auth proxy for credentials, this method is the point where the stored provider key is fetched. After reading the key, it hands the value into `Credential` so the rest of the sync flow can use a standard credential shape rather than dealing with raw storage details.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/connectors.py`

`data_model` · `cross-cutting`

This file is the “border agreement” for connectors. A connector is the part of the system that talks to an outside provider, such as Gmail or GitHub. The difficult part is authentication: the system often needs to act on a member’s connected account, but the secret token must not leak into logs, the agent, or the sandbox.

The file solves that by defining a few small data shapes and interfaces. A Credential is the safe package a sync job receives when it needs to call a provider. It can be a brokered transport, where another service injects the secret, or a direct bearer token or headers read inside the trusted host process. Its printed form is deliberately redacted.

A ConnectorBroker is the trusted server-side face of a brokered provider. It can list tools, describe a tool’s input shape, execute a tool for a connected account, prepare file uploads, expose file outputs, search for tools, and provide credentials for feed sync. Files are passed as references and presigned URLs, not as raw bytes through the core process.

ConnectorRegistry ties everything together. It maps provider names to installed brokers and falls back to a deploy-selected authentication backend for providers that are not brokered. Without this file, different connector backends would not share one safe, predictable way to run tools, resolve credentials, and move files without exposing secrets.

#### Function details

##### `Credential.__repr__`  (lines 51–60)

```
def __repr__(self) -> str
```

**Purpose**: Returns a safe text version of a Credential for debugging. It shows which kind of credential exists, but never prints the actual token, headers, or transport details.

**Data flow**: It reads the Credential’s own fields: transport, bearer, and headers. It checks which one is present and turns that into a short redacted string. The result is only a description like “bearer: redacted”; no secret data comes out.

**Call relations**: This is used automatically by Python when a Credential is printed, logged, or shown in an exception context. It protects every caller that might accidentally display a Credential while the rest of the connector flow passes the object around.


##### `AuthProxy.credential`  (lines 71–71)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines the promise that an authentication provider must fulfill: given a workspace, provider name, and account handle, return the Credential needed to call that provider. It is an interface method, so concrete backends provide the real behavior.

**Data flow**: The inputs are the workspace ID, the provider name, and the connected account identifier. An implementation uses those to find or create the right safe authentication path. The output is a Credential that a feed-sync source can use without knowing where the secret actually lives.

**Call relations**: Feed-sync code depends on this method instead of depending on one specific credential store or broker. ConnectorRegistry.credential also behaves as an AuthProxy and routes the request either to a broker or to a fallback backend.


##### `stale_grant_guidance`  (lines 79–86)

```
def stale_grant_guidance(provider: str) -> str
```

**Purpose**: Builds a clear error message for the case where a broker is asked to use an account grant it no longer recognizes. The message tells the agent that retrying will not fix the problem and that the member must reconnect the account.

**Data flow**: It takes a provider name as input. It inserts that name into a human-readable explanation about an old or invalid broker grant. It returns the finished guidance string.

**Call relations**: Broker implementations can use this helper when execution or credential lookup fails because the grant belongs to a previous broker setup or organization. The helper keeps that important user-facing advice consistent across providers.


##### `ConnectorBroker.tools`  (lines 156–158)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Defines how a broker lists the tools it offers for a provider, optionally using a search query. This lets dynamic connector tools discover what actions are available.

**Data flow**: The inputs are the workspace ID, provider name, and query text. A real broker implementation looks up matching tools that the workspace can use. The output is a tuple of BrokerTool objects, each describing a tool at a high level.

**Call relations**: Dynamic connector discovery calls this through the ConnectorRegistry’s selected broker. The returned tools can later be described in more detail with ConnectorBroker.schema or executed with ConnectorBroker.execute.


##### `ConnectorBroker.schema`  (lines 160–160)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Defines how a broker returns the detailed input shape for one provider tool. The agent uses this to know what arguments a tool expects.

**Data flow**: The inputs are the workspace ID, provider name, and tool slug, which is the broker’s short tool identifier. A real broker checks that the tool exists and returns a BrokerTool containing its schema. If the tool is unknown, it raises UnknownBrokerTool.

**Call relations**: After a tool is discovered or requested by name, dynamic connector code asks the broker for its schema before building a call. Unknown tools can be reported as unresolved instead of treated like a system crash.


##### `ConnectorBroker.execute`  (lines 162–170)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Defines how a broker runs one provider tool for a connected account. The broker injects the account’s secret on its own side, so the core system and sandbox do not receive the provider token.

**Data flow**: The inputs are the workspace ID, provider name, tool slug, argument values, account ID, and an optional idempotency key, which helps avoid doing the same action twice after a retry. A real broker sends the request to its execution service under the granted account. The output is a dictionary containing the provider tool’s response.

**Call relations**: Dynamic connector tools call this after choosing a provider tool and preparing its arguments. If files are involved, stage_upload may be used before execution, and file_outputs may be used afterward to expose produced files safely.


##### `ConnectorBroker.file_outputs`  (lines 172–172)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Defines how a broker extracts produced files from a tool execution response. It turns broker-specific response data into a standard list of downloadable file references.

**Data flow**: The input is the raw response dictionary from a broker tool execution. A real implementation inspects it for file results and converts them into BrokerFile objects with names and short-lived URLs. The output is a tuple of those file references.

**Call relations**: This fits after ConnectorBroker.execute. The sandbox can then fetch file bytes itself from the broker’s file store through the allowed network path, instead of the core process carrying those bytes.


##### `ConnectorBroker.stage_upload`  (lines 174–182)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Defines how a broker prepares a workspace file so a provider tool can use it as input. Instead of sending file bytes through the core process, it returns a place where the sandbox can upload the file directly.

**Data flow**: The inputs describe the workspace, provider, tool, filename, MIME type, and MD5 checksum. A real broker either creates a presigned upload URL or recognizes that it already has the file. The output is a StagedUpload containing the upload URL when needed, the required content type, and the argument value to pass into the tool call.

**Call relations**: Dynamic connector execution uses this before ConnectorBroker.execute when a tool argument refers to a workspace file. If a broker does not support this upload style, its implementation can reject the request.


##### `ConnectorBroker.search`  (lines 184–184)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Defines a richer semantic search over a broker’s tools. Besides matching tools, it can return a suggested plan, guidance, and warnings.

**Data flow**: The inputs are the workspace ID, provider name, and natural-language query. A real broker searches its catalog and may consult its own router or planner. The output is a BrokerSearch containing matching BrokerTool entries plus optional plan, guidance, and pitfalls.

**Call relations**: This supports discovery when the agent or user is not starting from an exact tool slug. It complements tools and schema by helping choose the right tool before execution.


##### `ConnectorBroker.credential`  (lines 186–186)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines how a broker supplies the Credential needed by feed-sync code for one provider account. The broker can return a transport-based credential so the secret remains on the broker side.

**Data flow**: The inputs are the workspace ID, provider name, and account handle. A real broker verifies the account belongs to that workspace and prepares the appropriate Credential. The output is that Credential, usually without exposing the underlying token to the caller.

**Call relations**: ConnectorRegistry.credential calls this when the requested provider is registered with a broker. Feed-sync code receives the result through the AuthProxy interface and uses it to authenticate provider HTTP requests.


##### `RequestForwarder.forward`  (lines 205–207)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: Defines how an intercepted provider HTTP request is forwarded through a broker under a connected account. This lets command-line tools in the sandbox appear authenticated without receiving the real secret.

**Data flow**: The inputs are the account ID, HTTP method, URL, request headers, and request body. A real forwarder sends that request through the broker, where the broker adds the actual credential. The output is a ForwardedResponse containing the provider’s status, headers, and body.

**Call relations**: The egress proxy calls this when it sees a sandbox request carrying a special grant marker instead of a real token. The forwarder hands the request to the broker and returns the provider’s response so the proxy can write it back to the sandbox.


##### `ConnectorRegistry.entry`  (lines 244–248)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Looks up the installed connector entry for a provider name. It fails clearly if no extension registered that provider.

**Data flow**: It receives a provider string and checks the registry’s entries mapping. If it finds a ConnectorEntry, it returns it. If not, it raises a KeyError with a message naming the missing provider.

**Call relations**: Dynamic connector code uses this when it needs the broker for a specific provider, such as to describe or execute a tool. By failing loudly for unknown providers, it prevents calls from silently going to the wrong backend.


##### `ConnectorRegistry.credential`  (lines 250–258)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Routes credential requests to the right place. Brokered providers go to their registered broker, while non-brokered providers can use the configured fallback authentication backend.

**Data flow**: The inputs are the workspace ID, provider name, and account handle. It first checks whether the provider has a ConnectorEntry. If yes, it asks that entry’s broker for the Credential. If not, it asks the fallback AuthProxy when one exists. If neither path exists, it raises a RuntimeError explaining that no credential source is configured.

**Call relations**: This makes ConnectorRegistry act as the single AuthProxy for feed-sync runners. During a sync, callers do not need to know whether a provider is brokered or direct; the registry chooses the correct credential source and hands back the resulting Credential.


### `core/src/ufo/credential_kind.py`

`domain_logic` · `workspace object request handling`

Some extensions need secrets, such as API keys. This file represents those needs as “credential” objects in the workspace. The important idea is that the slot declaration and the secret value are kept separate: the extension manifest says a slot exists, while the database may or may not hold an encrypted value for it. Reads only show the slot’s public information and whether it is filled. They never show the secret itself, or even a digest of it.

Think of it like labeled lockboxes in an office. This file lists the lockboxes, says which department requested each one, and tells you whether something is inside. It never opens the box.

The main class, `CredentialObjects`, is the object-kind implementation. It can list all declared slots, get the public description for one slot, report whether it is currently filled, and delete the stored value. It refuses create and update, because filling or rotating a secret must happen through `request_credentials`, which is designed for private handoff and owner authorization. Deleting is also protected: only the workspace owner may clear a slot. If a slot is cleared, the slot still appears, because the declaration lives in the extension, not in the credential table.

#### Function details

##### `_slug`  (lines 62–63)

```
def _slug(raw: str) -> str
```

**Purpose**: Turns a raw credential slot name into a simple, URL-like object name. This gives each declared slot a stable plain name that is easier to search and use.

**Data flow**: It receives a raw string, lowercases it, replaces runs of non-letter-or-number characters with hyphens, and trims extra hyphens from the ends. It returns the cleaned-up name.

**Call relations**: `CredentialObjects._named` calls this when it builds the public names for credential slots. `_slug` does only the small name-cleaning step, while `_named` decides what to do if two slots clean up to the same name.

*Call graph*: called by 1 (_named); 1 external calls (sub).


##### `CredentialObjects.list`  (lines 74–90)

```
async def list(self, ctx: ToolContext, query: str, cursor: str) -> ObjectPage
```

**Purpose**: Builds the list view for credential objects. It shows every declared slot, includes a short human-readable summary, and marks each slot as filled or empty without revealing the value.

**Data flow**: It reads the declared slots through `_named`, reads the database-backed set of filled slots through `_filled_slots`, and combines those into object rows. It filters the rows by the search text, applies cursor-based paging, and returns an `ObjectPage` containing the visible rows and, if needed, a cursor for the next page.

**Call relations**: This is used when the object system needs to display credential objects in a workspace. It relies on `_named` for the names users see and `_filled_slots` for the fill state, then wraps the result in standard object-list response objects.

*Call graph*: calls 2 internal fn (_filled_slots, _named); 2 external calls (__init__, __init__).


##### `CredentialObjects.get`  (lines 92–101)

```
async def get(self, ctx: ToolContext, name: str) -> CredentialSpec | None
```

**Purpose**: Returns the public declaration for one credential slot. It tells the caller what the slot is for, which extension declared it, and where it may be injected, but not the secret value.

**Data flow**: It receives an object name, looks that name up in the mapping made by `_named`, and returns `None` if no slot matches. If it finds a slot, it turns the slot’s public fields into a `CredentialSpec` result.

**Call relations**: This is used when the object system needs the readable specification for a single credential object. It hands off name resolution to `_named` and uses `CredentialSpec` as the safe public shape of the answer.

*Call graph*: calls 1 internal fn (_named); 1 external calls (__init__).


##### `CredentialObjects.status`  (lines 103–119)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: Reports whether a credential slot currently has a stored value and when that stored value was last changed. It deliberately reports status only, not the secret.

**Data flow**: It receives an object name and first checks that the declared slot exists through `_named`. If it does not exist, it returns `None`. If it exists, it opens a workspace database transaction, looks for a credential row for the current workspace and slot, and returns a small dictionary with `filled` and `updated_at` fields.

**Call relations**: This is used when callers need operational state for a credential object, separate from the public declaration returned by `get`. It uses the current workspace from `ws_current` and the credential table inside `workspace_tx` so the answer is scoped to the active workspace.

*Call graph*: calls 1 internal fn (_named); 3 external calls (select, workspace_tx, ws_current).


##### `CredentialObjects.apply`  (lines 121–124)

```
async def apply(self, ctx: ToolContext, name: str, spec: CredentialSpec, old: CredentialSpec | None) -> None
```

**Purpose**: Rejects attempts to create, fill, or update a credential through the normal object apply path. This protects secrets by forcing callers to use the dedicated `request_credentials` flow instead.

**Data flow**: It receives the requested name, new specification, and any old specification, but does not use them to change anything. It immediately raises a `VerbNotSupported` error with a message explaining that secret filling and rotation must happen elsewhere.

**Call relations**: This function is called when the object system tries to apply a create or update operation to a credential object. Instead of handing off to database writes, it stops the flow and points users toward the private credential-request mechanism.

*Call graph*: 1 external calls (__init__).


##### `CredentialObjects.delete`  (lines 126–136)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Clears the stored value for a credential slot. It does not remove the slot itself, because the slot is declared by an installed extension.

**Data flow**: It first asks the tool context whether the current speaker is the workspace owner. If not, it raises an owner-required error. If allowed, it resolves the object name to a declared slot, opens a workspace database transaction, and deletes the credential row for the current workspace and that slot. The result is that the slot becomes empty while remaining listed.

**Call relations**: This is used when someone deletes a credential object through the object system. It calls `speaker_is_owner` to enforce the ownership rule, `_named` to connect the public object name back to the real slot, and then uses the current workspace and credential table to remove only that workspace’s stored value.

*Call graph*: calls 2 internal fn (_named, speaker_is_owner); 4 external calls (__init__, delete, workspace_tx, ws_current).


##### `CredentialObjects._named`  (lines 138–150)

```
def _named(self) -> dict[str, DeclaredSlot]
```

**Purpose**: Builds the mapping from public object names to declared credential slots. It also prevents name collisions when two slots would otherwise produce the same simple name.

**Data flow**: It starts with the tuple of declared slots. For each slot, it creates a cleaned-up base name using `_slug` and groups slots that share that name. If a group has only one slot, that base name is used directly. If several slots collide, it adds a short hash made from the extension and slot name so each public name stays unique.

**Call relations**: This is the shared name-resolution helper for listing, reading, checking status, and deleting credentials. Those public operations all need the same answer to the question: “Which declared slot does this object name mean?”

*Call graph*: calls 1 internal fn (_slug); called by 4 (delete, get, list, status); 1 external calls (sha256).


##### `CredentialObjects._filled_slots`  (lines 152–161)

```
async def _filled_slots(self) -> frozenset[str]
```

**Purpose**: Finds which declared credential slots currently have stored values in the active workspace. It returns only slot names, never values.

**Data flow**: It opens a workspace database transaction, selects credential slot names for the current workspace from the credential table, and turns the result into an immutable set. The output is a set of slot names that are filled.

**Call relations**: `CredentialObjects.list` calls this while building the list view. The list operation then compares declared slots against this filled-slot set so it can show each item as either filled or empty.

*Call graph*: called by 1 (list); 3 external calls (select, workspace_tx, ws_current).


### `core/src/ufo/credentials.py`

`domain_logic` · `request handling and credential use`

This file is the project’s safe deposit box for credentials. A credential is stored in a named “slot”, and the real secret is encrypted before it is written to the database. Encryption here uses Fernet, a standard tool that turns readable text into unreadable bytes and can later turn it back only with the right key. Without this file, secrets could leak into chat transcripts, logs, sandboxes, or be overwritten accidentally.

There are two main parts. The first part, `CredentialRequests`, creates sealed credential requests. Think of these like tamper-proof claim tickets: they say “this member in this workspace is allowed to fill these specific slots,” and they expire after a short time. When a private surface later sends back a value or provider authorization state, the seal is opened and checked before anything is trusted.

The second part, `CredentialStore`, writes and reads the actual secrets. `put` encrypts a new value and inserts or updates the database row. `get` reads the encrypted value and decrypts it for use. `rotate` replaces a stored value only if the caller proves they saw the current old value first, which prevents two refresh operations from accidentally overwriting each other. The file is careful to reject empty secrets and to raise clear errors when a slot is missing or a sealed request is invalid.

#### Function details

##### `seal_credential_request`  (lines 43–44)

```
def seal_credential_request(fernet: Fernet, state: CredentialRequestState) -> str
```

**Purpose**: Turns a credential request description into a sealed, unreadable token. This lets the system pass around proof of what should be filled without exposing or allowing easy changes to the request details.

**Data flow**: It receives a Fernet encryptor and a `CredentialRequestState`, which contains the workspace, member, slots, and optional provider state. It converts that state to JSON text, encrypts the text, and returns the encrypted token as a string.

**Call relations**: This is the shared sealing step used by `CredentialRequests.seal` for normal slot-filling requests and by `CredentialRequests.authorize` for provider authorization flows. Those higher-level methods first decide what claims are allowed, then hand the final state here to be locked.

*Call graph*: called by 2 (authorize, seal); 2 external calls (model_dump_json, encrypt).


##### `open_credential_request`  (lines 47–52)

```
def open_credential_request(fernet: Fernet, sealed: str) -> CredentialRequestState
```

**Purpose**: Opens a sealed credential request and checks that it is still valid. If the token was changed, made with the wrong key, or is too old, it reports that the request is invalid.

**Data flow**: It receives a Fernet decryptor and a sealed token string. It tries to decrypt the token within the allowed lifetime, then parses the recovered JSON back into a `CredentialRequestState`. If decryption fails, it raises `CredentialRequestInvalid` instead of returning unsafe data.

**Call relations**: `CredentialRequests.open_authorization` calls this when it needs to verify a returned authorization token. This function performs the basic unlock-and-expiry check, then the caller checks whether the contents match the expected workspace, member, and slot.

*Call graph*: called by 1 (open_authorization); 2 external calls (__init__, decrypt).


##### `CredentialRequests.seal`  (lines 64–71)

```
def seal(self, workspace_id: UUID, member_id: UUID, slots: tuple[str, ...]) -> str
```

**Purpose**: Creates a sealed request saying that a specific member may privately fill one or more declared credential slots in a workspace. It stops callers from requesting slots that no installed extension has declared.

**Data flow**: It receives a workspace ID, member ID, and tuple of slot names. It compares the requested slots with the known declared slot set. If any slot is unknown, it raises an error. Otherwise it builds a request state and returns the encrypted seal produced by `seal_credential_request`.

**Call relations**: This method is used at the start of a private credential-entry flow. It validates the slots locally, then delegates the actual encryption to `seal_credential_request` so the resulting token can later be verified before storing secrets.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.authorize`  (lines 73–86)

```
def authorize(self, workspace_id: UUID, member_id: UUID, slot: str, payload: str) -> str
```

**Purpose**: Creates a sealed token for a provider authorization flow, such as an OAuth-style handoff, where there is extra provider state that must come back unchanged. It ensures the slot exists and that the provider state is not blank.

**Data flow**: It receives a workspace ID, member ID, one slot name, and a payload string from the authorization provider. It rejects unknown slots and empty payloads. Then it builds a request state containing that single slot and payload, encrypts it through `seal_credential_request`, and returns the sealed string.

**Call relations**: This is the authorization-specific sibling of `CredentialRequests.seal`. After it checks the slot and payload, it hands the state to `seal_credential_request`; later, `CredentialRequests.open_authorization` can open the seal and confirm the returned authorization belongs to the same situation.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.open_authorization`  (lines 88–102)

```
def open_authorization(self, sealed: str, workspace_id: UUID, member_id: UUID, slot: str) -> str
```

**Purpose**: Verifies a sealed authorization token and returns the provider state inside it. It makes sure the token belongs to the exact workspace, member, and slot expected by the current request.

**Data flow**: It receives a sealed token plus the workspace ID, member ID, and slot that the caller expects. It opens the token with `open_credential_request`, compares the decoded claims against the expected values, checks that the slot is still declared, and confirms there is a payload. If everything matches, it returns the payload string; otherwise it raises an invalid-request error or a slot declaration error.

**Call relations**: This method is called after an authorization handoff comes back. It relies on `open_credential_request` for decryption and expiry checking, then adds stricter business checks so a token for one member, workspace, or slot cannot be reused somewhere else.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `CredentialStore.put`  (lines 109–131)

```
async def put(self, workspace_id: UUID, slot: str, plaintext: str) -> None
```

**Purpose**: Stores a credential value for a workspace and slot after encrypting it. It either updates the existing slot or creates a new database row if the slot has not been stored before.

**Data flow**: It receives a workspace ID, slot name, and plaintext secret. It rejects an empty secret, encrypts the value, opens a database transaction with `workspace_tx`, and tries to update the matching credential row. If no row was updated, it inserts a new row with timestamps. It returns nothing, but the database now contains the encrypted secret.

**Call relations**: Higher-level credential fulfillment code would call this after a private credential request has been verified. Inside the function, SQLAlchemy builds the update or insert statements, and `workspace_tx` provides the database connection and transaction boundary.

*Call graph*: 3 external calls (insert, update, workspace_tx).


##### `CredentialStore.get`  (lines 133–145)

```
async def get(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Retrieves a stored credential secret for a workspace and slot. If the slot has never been filled, it raises a clear missing-slot error.

**Data flow**: It receives a workspace ID and slot name. It opens a database transaction, selects the encrypted credential bytes for that workspace and slot, and closes the transaction. If there is no row, it raises `CredentialSlotUnset`. If a row exists, it decrypts the ciphertext and returns the readable secret string.

**Call relations**: This is called when the system needs the real secret, for example before the proxy substitutes it for a safe placeholder. It uses SQLAlchemy to read the database and relies on the store’s Fernet key to turn the saved encrypted value back into plaintext.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `CredentialStore.rotate`  (lines 147–178)

```
async def rotate(self, workspace_id: UUID, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Safely replaces an existing credential only if it still has the expected old value. This is useful for refreshed tokens, where two refreshes might happen close together and the older one must not overwrite the newer result.

**Data flow**: It receives a workspace ID, slot name, expected current plaintext, and replacement plaintext. It rejects an empty replacement. Then it reads the current encrypted row from the database. If the row is missing, or decrypting it does not match the expected value, it returns `false`. If it matches, it writes the encrypted replacement, but only while the database row still contains the same ciphertext it just checked. It returns `true` only if exactly one row was updated.

**Call relations**: OAuth or similar refresh logic would call this after receiving a new token from an outside provider. The function uses `workspace_tx` for the database transaction and SQLAlchemy for the select and update, combining a value check and a database condition to avoid clobbering a newer credential.

*Call graph*: 3 external calls (select, update, workspace_tx).


### `extensions/pipedream/ufo_ext_pipedream/proxy.py`

`io_transport` · `request handling and transport teardown`

Some connectors need to talk to outside services like Gmail or Slack, but Pipedream keeps the real provider credential hidden for safety. This file solves that gap. It creates a custom HTTP transport, which is the part of an HTTP client responsible for actually sending requests. Instead of sending a request straight to the provider, it wraps the request and sends it to Pipedream’s Connect Proxy.

The flow is like giving a sealed letter to a trusted courier. The connector writes an ordinary request to the provider. This transport reads that request, asks Pipedream for an access token for the proxy, encodes the original provider URL into the proxy path, adds the account and user identity to the query string, and forwards the request body and allowed headers. Pipedream then injects the hidden provider credential on its own server and calls the provider.

A key detail is that provider response details are preserved. The body, headers, and status code come back through the proxy unchanged. That matters because connectors often rely on status codes, such as 404 meaning a cursor expired or 401 meaning access is forbidden. The file also carefully filters request headers: transport-level headers like authorization or content length are not forwarded, while useful provider headers are renamed with Pipedream’s required prefix.

#### Function details

##### `PipedreamProxyTransport.handle_async_request`  (lines 54–73)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This function turns one normal provider HTTP request into a Pipedream Connect Proxy request. It is used when a connector wants to call a provider, but the real provider credential must stay inside Pipedream.

**Data flow**: It receives an HTTP request aimed at the original provider. It asks the Pipedream client for a proxy access token, reads the request body, keeps only safe and useful headers, prefixes those headers so Pipedream knows to forward them, and base64-url-encodes the original URL so it can be placed inside the proxy URL. It then builds a new request to Pipedream’s proxy endpoint with the account id and external user id attached, sends it through the inner transport, and returns the resulting response unchanged.

**Call relations**: This is the main work of the custom transport. The HTTP client calls it whenever it is about to send a request. Inside that moment, it uses the Pipedream client for authorization, uses standard HTTPX request and URL objects to build the proxy request, and hands the final network send to the wrapped inner transport.

*Call graph*: 4 external calls (urlsafe_b64encode, Request, aread, URL).


##### `PipedreamProxyTransport.aclose`  (lines 75–76)

```
async def aclose(self) -> None
```

**Purpose**: This function closes the wrapped HTTP transport when the proxy transport is no longer needed. It makes sure any underlying network resources are cleaned up properly.

**Data flow**: It receives no new request data. It simply passes the close instruction down to the inner transport, which can then release open connections or other resources. Nothing is returned.

**Call relations**: This is called during cleanup, usually when the HTTP client using this transport is being closed. It does not perform proxy rewriting itself; it delegates shutdown to the inner transport that did the actual sending.


### Signed access tokens
These files create and verify bearer, gateway, artifact, and operator-session tokens used to prove identity and authorize privileged access.

### `control/src/ufo_control/gateway_token.py`

`domain_logic` · `credential issuing`

This file is a small wrapper around the project’s shared bearer token code. A bearer token is like a temporary pass: whoever presents it can be treated as the named user, so it must be signed and expire after a set time. Here, the pass is for a hosted member of a workspace, and it is meant to be stored by the client in the user’s UFO credentials file and later checked by surfaces that need to trust the member’s identity.

The file sets two important policy choices in one place. First, tokens last 30 days. Second, the secret used to sign them is expected to come from the environment variable named `UFO_TOKEN_SECRET`. The actual cryptographic signing work is not written here. Instead, `mint_token` calls the shared `ufo.bearer.mint_token` function. That matters because token creation and token checking must stay perfectly matched. If two parts of the system invented their own token shapes, a valid token might fail verification, or worse, an invalid-looking token might be accepted by mistake.

In everyday terms, this file does not build the lock or the key. It decides how long the key is valid and asks the central locksmith to make it in the standard shape.

#### Function details

##### `mint_token`  (lines 13–14)

```
def mint_token(secret: str, workspace_id: str, email: str, now: datetime | None=None) -> str
```

**Purpose**: Creates a signed member token for a specific workspace and email address. Someone uses it when the gateway needs to give a client a time-limited credential that can later be verified.

**Data flow**: It receives a signing secret, a workspace ID, an email address, and optionally the current time. It adds this file’s fixed 30-day lifetime, then passes all of that to the shared bearer-token maker. The result is a token string; this function does not change any stored data itself.

**Call relations**: This function is the control-side convenience entry for making gateway member tokens. When it is asked to mint a token, it immediately hands the real signing work to `ufo.bearer.mint_token`, ensuring the token is produced by the same shared codec that the rest of the system expects.

*Call graph*: 1 external calls (mint_token).


### `core/src/ufo/artifact_token.py`

`domain_logic` · `share link creation and artifact download request handling`

This file is a small security gate for artifact downloads. An artifact is a stored file that may be shared, and the system does not want the download route to serve any file just because someone guesses a path. Instead, a caller must present a token: a compact piece of text that says which artifact may be downloaded, what filename to suggest, and when permission expires.

The token is protected with an HMAC, which is a cryptographic signature made from a secret known only to the deployment. A useful analogy is a tamper-evident wax seal: anyone can carry the message, but only someone with the secret can make a seal that verifies. If the blob key, filename, or expiry time is changed after the token is made, the signature check fails.

The file also limits what a valid token can point to. Even with a correct signature, the blob key must live under the `artifacts/` prefix and must not contain `..`, which could otherwise mean “walk up to a parent folder.” Finally, the token must not be expired. If anything is wrong, the code raises `ArtifactTokenError` instead of returning claims. If everything is right, it returns `ArtifactClaims`, the safe, verified facts the download route can use.

#### Function details

##### `_sign`  (lines 36–38)

```
def _sign(secret: str, body: str) -> str
```

**Purpose**: Creates the cryptographic signature for a token body. It is the shared helper that both token creation and token verification use, so both sides agree on exactly what a valid seal looks like.

**Data flow**: It receives a secret string and a token body string. It combines them with HMAC using SHA-256, turns the resulting bytes into URL-safe base64 text, removes padding characters, and returns that text as the signature. It does not change any outside state.

**Call relations**: When `mint_artifact_token` builds a new token, it calls `_sign` to attach the seal. When `verify_artifact_token` checks an incoming token, it calls `_sign` again on the received body and compares the result with the supplied signature.

*Call graph*: called by 2 (mint_artifact_token, verify_artifact_token); 2 external calls (urlsafe_b64encode, new).


##### `mint_artifact_token`  (lines 41–51)

```
def mint_artifact_token(secret: str, blob_key: str, filename: str, expires_at: int) -> str
```

**Purpose**: Builds a download token for one artifact file. It is used when the system wants to give someone temporary permission to download a specific stored artifact.

**Data flow**: It takes the deployment secret, the artifact blob key, the suggested filename, and an expiry time. If the secret is missing, it raises `ArtifactTokenError`. Otherwise it packages the key, filename, and expiry into JSON, encodes that package into URL-safe text, asks `_sign` to sign it, and returns one token string made from `body.signature`.

**Call relations**: This is the token-making side of the flow. It prepares the body with `json.dumps` and base64 encoding, then hands that body to `_sign` so the artifact route can later detect any tampering. The matching checker is `verify_artifact_token`, which expects the same body-and-signature format.

*Call graph*: calls 1 internal fn (_sign); 3 external calls (__init__, urlsafe_b64encode, dumps).


##### `verify_artifact_token`  (lines 54–80)

```
def verify_artifact_token(token: str, secret: str, now: datetime) -> ArtifactClaims
```

**Purpose**: Checks an incoming artifact download token and turns it into trusted download claims. It rejects tokens that are missing, altered, unreadable, expired, or pointing outside the artifact storage area.

**Data flow**: It receives the token text, the deployment secret, and the current time. It splits the token into body and signature, rebuilds the expected signature with `_sign`, and compares the two using a safe comparison function. Then it decodes the body from base64, reads the JSON payload, and builds `ArtifactClaims` from the blob key, filename, and expiry time. Before returning those claims, it checks that the key starts with `artifacts/`, does not contain parent-folder escapes like `..`, and has not passed its expiry time. Any failed check becomes an `ArtifactTokenError`.

**Call relations**: This is the token-checking side of the flow. It uses the same `_sign` helper as `mint_artifact_token`, which keeps creation and verification in sync. After signature and payload checks, it hands back `ArtifactClaims`, which the artifact download route can use to know exactly which blob to serve and what filename to suggest.

*Call graph*: calls 1 internal fn (_sign); 7 external calls (__init__, __init__, urlsafe_b64decode, timestamp, compare_digest, loads, PurePosixPath).


### `core/src/ufo/bearer.py`

`domain_logic` · `token minting and request authentication`

This file is the shared rulebook for UFO member tokens. A bearer token is like a stamped pass: whoever holds it can present it, and the system checks whether the stamp is real and not expired. The token contains three pieces of information: the workspace id, the member email, and an expiry time. That information is encoded into text, then signed with a secret key using HMAC, which is a way to prove the text was made by someone who knows the secret and was not changed afterward.

The important idea is that the server does not need to remember issued tokens. When a request arrives, the code recomputes the signature from the token body and compares it with the signature attached to the token. If they match, and the expiry time is still in the future, the claims are accepted. If anything looks wrong, the answer is simply “no claims.”

The signing secret comes from the `UFO_TOKEN_SECRET` environment variable when verifying. That keeps the key inside core code, so extensions can ask whether a token is valid without directly handling the secret. The file supports two main checks: “is this token valid for this exact workspace?” and “which workspace does this valid token claim?”

#### Function details

##### `mint_token`  (lines 28–45)

```
def mint_token(secret: str, workspace_id: str, email: str, ttl: timedelta, now: datetime | None=None) -> str
```

**Purpose**: Creates a new signed bearer token for a workspace and email address. It is used by token issuers so every token has the same shape and can later be checked by the matching verification code.

**Data flow**: It receives a secret key, a workspace id, an email address, a time-to-live, and optionally a fixed current time. It lowercases and trims the email, calculates the expiry time, turns the claims into compact JSON, base64-url encodes that JSON into a safe text body, signs the body with HMAC-SHA256, and returns `body.signature` as one token string. If the secret is empty, it raises an error instead of making an unverifiable token.

**Call relations**: This is the creation side of the token flow. It uses standard JSON, base64, time, and HMAC helpers to produce the exact format that `verified_claims` later expects to read and check.

*Call graph*: 4 external calls (urlsafe_b64encode, now, new, dumps).


##### `verified_claims`  (lines 48–71)

```
def verified_claims(token: str, now: int | None=None) -> tuple[str, str] | None
```

**Purpose**: Checks whether a bearer token is genuine and still valid, then returns the workspace and email it proves. If the token is missing, forged, expired, malformed, or signed with the wrong secret, it returns `None`.

**Data flow**: It receives a token string and optionally a current time. It reads the signing secret from the environment, splits the token into body and signature, recomputes the expected signature, and compares signatures using a constant-time comparison, which avoids leaking timing clues to attackers. If the signature is good, it decodes the base64 body, parses the JSON, checks that `ws`, `email`, and `exp` have the expected types, rejects expired tokens, and finally outputs the workspace string and email string.

**Call relations**: This is the central verification step. `verify_token` calls it before checking that the token belongs to one specific workspace, and `workspace_claim` calls it before turning the claimed workspace into a UUID. It relies on `_secret` to get the shared signing key and `_b64url_decode` to unpack the encoded token body.

*Call graph*: calls 2 internal fn (_b64url_decode, _secret); called by 2 (verify_token, workspace_claim); 4 external calls (now, compare_digest, new, loads).


##### `verify_token`  (lines 74–85)

```
def verify_token(token: str, workspace_id: UUID, now: int | None=None) -> str | None
```

**Purpose**: Checks whether a token is valid for one particular workspace and returns the member email if it is. This protects a workspace from accepting a token that was signed for a different workspace.

**Data flow**: It receives a token, a workspace UUID, and optionally a current time. It asks `verified_claims` to prove the token first. If that fails, it returns `None`; if the signed workspace claim does not match the given workspace id, it also returns `None`. When both checks pass, it returns the email in lowercase.

**Call relations**: This function builds on the general token checker for deployments that already know which workspace they serve. It delegates the hard security work to `verified_claims`, then adds the workspace match as the final local rule.

*Call graph*: calls 1 internal fn (verified_claims).


##### `workspace_claim`  (lines 88–99)

```
def workspace_claim(token: str, now: int | None=None) -> UUID | None
```

**Purpose**: Extracts the workspace UUID from a valid token. This is useful in a shared service where one running process may serve many workspaces and must decide the workspace from each request.

**Data flow**: It receives a token and optionally a current time. It first calls `verified_claims` to confirm the token is signed correctly and not expired. If that succeeds, it tries to turn the workspace claim string into a UUID object. It returns that UUID when valid, or `None` if verification fails or the workspace text is not a valid UUID.

**Call relations**: This is another small wrapper around `verified_claims`. Instead of comparing the workspace to a known value like `verify_token`, it hands the verified workspace claim to `uuid.UUID` so callers can use it as a proper workspace identifier.

*Call graph*: calls 1 internal fn (verified_claims); 1 external calls (UUID).


##### `_secret`  (lines 102–106)

```
def _secret() -> str
```

**Purpose**: Reads the token signing secret from the `UFO_TOKEN_SECRET` environment variable. Verification cannot be safe without this shared secret, so the function fails loudly if it is missing.

**Data flow**: It reads the process environment and looks for `UFO_TOKEN_SECRET`. If a non-empty value is present, it returns that string. If not, it raises a runtime error explaining that the secret must be set before member bearer tokens can be verified.

**Call relations**: `verified_claims` calls this before checking any token signature. This keeps secret lookup in one place, so the verification path always uses the same environment setting.

*Call graph*: called by 1 (verified_claims).


##### `_b64url_decode`  (lines 109–110)

```
def _b64url_decode(value: str) -> bytes
```

**Purpose**: Decodes the token body from base64-url text back into bytes. Base64-url is an encoding that makes binary or JSON data safe to place in URLs and headers.

**Data flow**: It receives the encoded token body string. Because the token format strips padding characters to keep the token shorter, this function adds the needed padding back, decodes the text with URL-safe base64 decoding, and returns the original bytes.

**Call relations**: `verified_claims` calls this after a token signature has matched, so it can recover and parse the JSON claims inside the token body.

*Call graph*: called by 1 (verified_claims); 1 external calls (urlsafe_b64decode).


### `core/src/ufo/ext/operator.py`

`domain_logic` · `request handling`

Operator tools need a way to know, “Who is using this page, and which workspace are they looking at?” This file answers that question without putting long-lived secret tokens into URLs, where they could end up in browser history, server logs, or copied links. Think of it like a front desk for internal tools: it checks the visitor’s badge, confirms they belong to the right company department, and then points them to the correct room.

The main flow starts by looking for a bearer token. A bearer token is a credential that proves the request is allowed to act as someone. The file accepts it from the Authorization header, from a shared operator cookie, or from the form body during the one POST request that opens a session. It deliberately does not accept tokens from query parameters.

Once it has a token, it asks the bearer-token verifier to check it. If the token is valid, the file checks the email address inside it. Only users from the configured operator email domain are allowed through. After that, the request is scoped to a workspace. By default, it uses the workspace named in the token. An operator can also pass a `ws` query value to inspect another workspace, either as a raw workspace UUID or as a customer domain that is converted into a stable UUID.

Finally, the file can turn a posted token into an HTTP-only session cookie, so one successful operator login works across multiple operator surfaces.

#### Function details

##### `operator_bearer`  (lines 24–37)

```
async def operator_bearer(request: Request) -> str
```

**Purpose**: This function finds the operator credential for an incoming web request. It checks safe places only: the Authorization header, the operator session cookie, or the form body during the session-opening POST request.

**Data flow**: It receives a web request. First it reads the Authorization header and returns the token if it is a proper Bearer token. If that is missing, it reads the shared operator cookie. If that is also missing and the request is a POST, it reads the submitted form and looks for the token field. It returns the cleaned token text, or an empty string if no usable token is found.

**Call relations**: This is the first step used by `resolve_operator_workspace`. That later function depends on `operator_bearer` to get the credential before it can verify the user and decide which workspace the request belongs to. When the token is submitted in a POST form, this function asks the request object to parse the form body.

*Call graph*: called by 1 (resolve_operator_workspace); 1 external calls (form).


##### `resolve_operator_workspace`  (lines 40–64)

```
async def resolve_operator_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: This function decides whether an operator request is allowed and, if so, which workspace it should operate on. It rejects the request unless the token is valid and the token’s email address belongs to the operator email domain.

**Data flow**: It receives the web request and a surface-auth object. It first gets the bearer token using `operator_bearer`. Then it verifies the token and reads the workspace claim and email address from it. If the token is missing, invalid, or from the wrong email domain, it returns `None`, meaning the request should be rejected. If there is no `ws` query value, it returns the workspace UUID from the token. If `ws` is present, it tries to treat it as a UUID; if that fails, it treats it as a domain name and converts it into a stable UUID using DNS-style UUID generation.

**Call relations**: This is the central authorization decision for operator surfaces. It calls `operator_bearer` to locate the credential, hands the token to `verified_claims` for checking, uses `_email_domain` to enforce the operator-domain gate, and then uses UUID parsing or UUID generation to choose the final workspace scope.

*Call graph*: calls 1 internal fn (operator_bearer); 4 external calls (verified_claims, _email_domain, UUID, uuid5).


##### `bind_operator_session`  (lines 67–79)

```
async def bind_operator_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function opens an operator web session by storing a posted token in a secure session cookie and redirecting the browser back to the page. It is used after the token has already been checked by the surrounding surface logic.

**Data flow**: It receives the current surface context and web request. It reads the form body and looks for the token field. If the token is missing or blank, it returns a JSON error response with a bad-request status. If the token is present, it creates a redirect response back to the current URL, attaches the token as the shared operator cookie, and returns that response to the browser.

**Call relations**: This function is the bridge from a one-time posted login token to a reusable browser session. It asks the request to parse the form, uses `JSONResponse` when the form is invalid, uses `RedirectResponse` when the session can be opened, and calls `set_session_cookie` so later operator requests can be authenticated through the cookie instead of reposting the token.

*Call graph*: 4 external calls (JSONResponse, RedirectResponse, form, set_session_cookie).


### Approval governance
This file enforces proposal-and-approval policy for sensitive agent prompt changes.

### `core/src/ufo/governance.py`

`domain_logic` · `request handling`

This file is a safety gate for changing an agent’s configuration, specifically its prompt. A prompt is the instruction text that shapes how an agent behaves, so changing it directly could be risky or could overwrite someone else’s newer work. Instead, this file uses a governed workflow: first a change is proposed, then later it can be approved.

The key idea is a “compare-and-swap” check. In everyday terms, it is like leaving a note that says, “Replace page 3 with this new page, but only if page 3 still contains the text I saw earlier.” The file records a fingerprint of the old prompt, called a digest. A digest is a short, fixed text made from the prompt using a hash function; if the prompt changes, the digest changes too.

When a proposal is opened, the code checks that the target agent exists in the current workspace, then stores the proposed new prompt and both the old and new digests in the proposal table. When approval happens, the code locks and rereads the current agent prompt from the database. If the current prompt no longer matches the proposal’s original digest, the proposal is rejected instead of overwriting newer changes. If it still matches, the new prompt is written to the agent and the proposal is marked approved. This matters because it prevents stale approvals from silently undoing recent edits.

#### Function details

##### `prompt_digest`  (lines 16–17)

```
def prompt_digest(prompt: str) -> str
```

**Purpose**: This function makes a stable fingerprint for a prompt. It is used to tell whether two prompt texts are exactly the same without storing or comparing the full text every time.

**Data flow**: It takes one prompt string as input. It turns that text into bytes, runs it through SHA-256, which is a standard hashing method that produces a fixed-length fingerprint, and returns the fingerprint as readable text.

**Call relations**: When a change is proposed, Governance.propose_change calls this to record what the proposed new prompt would look like as a digest. When a proposal is approved, Governance.approve_proposal calls it again on the current stored prompt to check whether the proposal is still safe to apply.

*Call graph*: called by 2 (approve_proposal, propose_change); 1 external calls (sha256).


##### `Governance.propose_change`  (lines 28–55)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: This function opens a new proposal to change an agent’s prompt. It does not change the agent yet; it records the requested change so it can be reviewed and approved later.

**Data flow**: It receives an AgentChange, which includes the target agent, the prompt digest the proposer thinks is current, and the proposed new prompt. It creates a new proposal ID, opens a database transaction for the workspace, checks that the agent exists in that workspace, and inserts a pending proposal row containing the old digest, the new prompt, and the digest of the new prompt. It returns a ProposalRef containing the new proposal ID. If the agent is not found, it raises an error instead of creating a proposal.

**Call relations**: This is the first step in the governed-change flow. Some caller asks Governance to propose a change; this function uses prompt_digest to fingerprint the new prompt, stores the proposal through the database transaction supplied by workspace_tx, and hands back a ProposalRef so the proposal can be referred to later, for example during approval.

*Call graph*: calls 1 internal fn (prompt_digest); 5 external calls (__init__, insert, select, workspace_tx, uuid4).


##### `Governance.approve_proposal`  (lines 57–114)

```
async def approve_proposal(self, proposal_id: UUID) -> None
```

**Purpose**: This function tries to approve and apply a pending prompt-change proposal. It only writes the new prompt if the agent has not changed since the proposal was made.

**Data flow**: It receives a proposal ID. It opens a workspace database transaction, loads the proposal, checks that it exists and is still pending, then reads the current prompt for the target agent while locking that row so another update cannot race with it. It compares the proposal’s original digest with a fresh digest of the current prompt. If they do not match, it marks the proposal rejected and logs that rejection. If they do match, it updates the agent prompt, marks the proposal approved, and then logs the approval after the transaction completes.

**Call relations**: This is the second step in the governed-change flow. A caller asks to approve a proposal; this function reads the stored proposal, calls prompt_digest to verify that the agent is still in the expected state, then either updates the agent and proposal records or rejects the proposal. It uses the logging helper to record the final outcome for observability, meaning humans or tools can later see what happened.

*Call graph*: calls 1 internal fn (prompt_digest); 4 external calls (select, update, workspace_tx, log).

## 📊 State Registers Touched

- `reg-effective-config` — The combined settings that tell the service which features, adapters, limits, and deployment options to use.
- `reg-workspace-context` — The current workspace and member context that keeps every request acting inside the right tenant boundary.
- `reg-workspaces-members-agents` — The durable records for workspaces, their members, and the agents that can act for them.
- `reg-identity-and-session-tokens` — The identities, bearer tokens, gateway tokens, operator sessions, and other passes that prove who is allowed in.
- `reg-onboarding-claims-invites` — The temporary signup codes, email claims, and invite records used before or during workspace creation.
- `reg-connected-credentials` — The encrypted store of outside account connections and secrets that tools and sync jobs may use safely.
- `reg-permission-grants` — The permission ledger saying which agent may use which connected account or provider access.
- `reg-connector-catalog` — The shared directory of external service connectors and broker-backed provider access.
- `reg-sandbox-session-policy` — The safe execution room state: sandbox handles, mounted workspace files, storage access, and restrictions.
- `reg-egress-proxy-state` — The controlled network gateway state that decides which outside sites sandboxed work may contact and how usage is counted.
- `reg-conversation-transcript` — The saved conversation thread, messages, transcript edits, and compacted summaries.
- `reg-turn-queue-run-state` — The durable state of each agent turn, including whether it is queued, claimed, running, parked, finished, failed, or cancelled.
- `reg-inbound-surface-state` — The stored inbound messages and surface delivery keys that connect Slack, web, terminal, and other fronts to conversations.
- `reg-artifact-blob-store` — The shared file and blob storage used for uploaded content, generated artifacts, and token-protected downloads.
- `reg-source-sync-state` — The remembered external sources, imported pages, raw bodies, change records, cursors, errors, and deletion markers.
- `reg-memory-store` — The workspace memory facts, episodes, ownership labels, confidence, and consolidation indexes used for recall.
- `reg-scheduled-task-calendar` — The durable calendar of one-time and repeating tasks that workers can safely claim and run later.
- `reg-accounting-ledger-spend` — The usage ledger, spend caps, exports, and cost totals for models, egress, and other billable work.
- `reg-seat-billing-state` — The workspace seat limits, granted seats, included seats, and external billing integration state.
- `reg-observability-trace-state` — The logs, metrics, traces, traceparent links, and redaction rules used to monitor work safely.
- `reg-workspace-object-catalog` — The named workspace objects and object-type registry used to list, inspect, validate, change, or delete stored things.
- `reg-prompt-governance` — The saved prompt digests, prompt-change proposals, approvals, and experiment evidence that control instruction changes.
- `reg-subagent-work-tree` — The parent-child turn links, delegated work state, and message flow between main agents and subagents.
- `reg-oauth-consent-flow-state` — Short-lived OAuth/provider consent attempt state linking redirects, callbacks, workspace/member identity, and provider account checks until a credential is finalized.
- `reg-secret-keyring` — Loaded signing and encryption key material used to mint/verify tokens and seal/unseal protected secrets across trusted paths.
- `reg-hosted-domain-workspace-map` — Durable hosted onboarding mapping from verified email domains to the shared workspace used for automatic member provisioning.
