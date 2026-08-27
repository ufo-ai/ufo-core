# External Connectors, Credentials, and Egress Requests  `stage-13`

This stage is shared support for the moments when UFO must reach outside its own walls. Sync jobs use it to read data from services, and agents use it to call tools or send messages, while secrets stay controlled.

The source connector framework gives sync jobs a common way to fetch records from different services. Direct and keyed connectors cover the simpler case where a member supplies an API key. The main connector access file defines the rules all providers must follow, and checks that a credential still belongs to the right workspace, member, provider, and account.

Composio and Pipedream integrations act as safe middlemen. They manage connected accounts, expose available actions, proxy web requests, run tools, and handle files without handing raw tokens to the sandbox. Generic connector objects and agent tools make these accounts visible and usable inside UFO, while evaluation fakes provide predictable test versions.

GitHub App credentials support coding work with verified, short-lived access. Slack and iMessage flows guide messaging setup. Finally, egress rules decide which network calls a sandboxed agent may make, and where secrets are safely added outside the sandbox.

## Sub-stages

- [Source Connector Framework](stage-13.1.md) `stage-13.1` — 2 files
- [Direct and Keyed API-Credential Connectors](stage-13.2.md) `stage-13.2` — 2 files
- [Composio Brokered Connector Integration](stage-13.3.md) `stage-13.3` — 6 files
- [Pipedream Brokered Connector Integration](stage-13.4.md) `stage-13.4` — 4 files
- [Generic Connector Objects, Agent Tools, and Evaluation Fakes](stage-13.5.md) `stage-13.5` — 3 files
- [Coding GitHub App Connector Credentials](stage-13.6.md) `stage-13.6` — 2 files
- [Slack and iMessage Connector Flows](stage-13.7.md) `stage-13.7` — 3 files

## Files in this stage

### Connector Access and Egress Policy
Defines how external connector credentials are validated and translated into sandbox-safe outbound network rules.

### `core/src/ufo/access/connectors.py`

`domain_logic` · `cross-cutting: connector discovery, tool execution, feed sync credential resolution, and proxied request handling`

This file is the connector doorway for the system. A connector is code that lets UFO talk to outside services, like Gmail or GitHub. The file separates two very different ways of authenticating: a broker can keep the secret token on its own server and proxy requests, or UFO can read a workspace-owned key directly from its credential store. The `Credential` object represents exactly one of those paths, and it is designed not to reveal secrets if accidentally printed.

The file also defines the broker interface: how UFO asks a broker what tools exist, what inputs a tool needs, how to run a tool, how to stage files, and how to resolve credentials for feed sync. Think of the broker as a concierge: UFO asks for a service, but the concierge keeps the keys behind the desk.

`ConnectorRegistry` is the routing table. It knows which provider belongs to which broker, can ask an open resolver about providers that were not registered one by one, and can fall back to direct credentials.

The most important safety behavior is source binding. Feed-sync sources may be tied to a specific member-owned connection. Before using that connection, and again before each proxied HTTP request, the code checks the database to make sure the connection is still active and still matches the original owner, provider, and account. Without this, an old or reassigned source could keep using access it should no longer have.

#### Function details

##### `Credential.__repr__`  (lines 57–66)

```
def __repr__(self) -> str
```

**Purpose**: Returns a safe text version of a credential without exposing the actual secret. This matters because credentials can accidentally appear in logs or error messages.

**Data flow**: It looks at the credential fields to see whether authentication is via a transport, bearer token, headers, or nothing. It returns a short label that says which kind is present, but replaces the sensitive value with “redacted”. It does not change the credential.

**Call relations**: This is used automatically by Python when a `Credential` is printed or shown in debugging output. It acts as a last line of defense if surrounding code accidentally includes a credential in a message.


##### `AuthProxy.credential`  (lines 85–85)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines the promise that an authentication backend can turn a workspace, provider, and account name into a usable `Credential`. Implementations may return a brokered transport, a bearer token, or provider-specific headers.

**Data flow**: The caller supplies the workspace ID, provider slug, and account handle. An implementation checks its own storage or broker and returns a `Credential` object that the connector can use to make provider requests.

**Call relations**: This is a protocol method, meaning this file defines the shape but not the body. `_credential` and bound source credential resolution call objects through this contract so the rest of the system does not need to know which authentication backend is installed.


##### `GrantUnusable.__init__`  (lines 113–115)

```
def __init__(self, reason: str, *, awaits_grant: bool=False) -> None
```

**Purpose**: Creates an error that says a brokered account grant cannot currently be used. It also records whether the only fix is for the member to reconnect the account.

**Data flow**: It receives a human-readable reason and an optional `awaits_grant` flag. It stores the reason in the normal exception machinery and saves the flag on the exception object for callers to inspect later.

**Call relations**: Broker integrations such as Composio and Pipedream create this error when they discover a revoked, expired, unhealthy, or unknown account grant. Downstream sync code can treat this differently from a temporary provider outage, often parking the feed instead of raising an operator alert.

*Call graph*: called by 4 (credential, _account, credential, _account).


##### `stale_grant_guidance`  (lines 118–125)

```
def stale_grant_guidance(provider: str) -> str
```

**Purpose**: Builds a clear error message for a grant that points to an account the current broker does not recognize. The message tells the reader that retrying will not help and that the member should reconnect.

**Data flow**: It takes a provider name and formats it into a standard guidance sentence. The output is just text; no state is read or changed.

**Call relations**: Broker implementations can use this helper when turning a stale or unknown account reference into a `GrantUnusable` error. It keeps the user-facing explanation consistent across broker backends.


##### `ConnectorBroker.tools`  (lines 196–198)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Defines how UFO asks a broker for the tools available for a provider, optionally filtered by a search query. A tool here means an action the agent can ask the outside service to perform.

**Data flow**: The caller gives a workspace ID, provider name, and query text. An implementation returns a tuple of `BrokerTool` descriptions that match what the broker offers.

**Call relations**: This is a protocol method. Dynamic connector discovery code calls broker implementations through this shape when it needs to list possible actions for an agent or user.


##### `ConnectorBroker.schema`  (lines 200–200)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Defines how UFO asks for the detailed input shape of one broker tool. This lets the agent know what arguments it must provide before trying to run the tool.

**Data flow**: The caller supplies the workspace ID, provider name, and tool slug. The implementation returns a `BrokerTool` with its input schema filled in, or raises `UnknownBrokerTool` if the slug is not known for that provider.

**Call relations**: This is part of the broker contract used by dynamic connector tools when describing a specific tool. It sits between high-level tool selection and actual execution.


##### `ConnectorBroker.execute`  (lines 202–210)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Defines how UFO asks a broker to run one provider tool under a connected account. The broker injects the real account secret on its side, so UFO does not receive the token.

**Data flow**: The caller provides the workspace, provider, tool slug, tool arguments, account ID, and an optional idempotency key, which is a repeat-safe request identifier. The implementation sends the request to the broker and returns the broker’s response as a dictionary.

**Call relations**: Dynamic connector execution code calls broker implementations through this method after a tool has been chosen and arguments prepared. File staging and output extraction happen through separate broker methods around this execution step.


##### `ConnectorBroker.file_outputs`  (lines 212–212)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Defines how a broker turns a tool response into downloadable file references. The actual file bytes stay outside the serve process.

**Data flow**: It receives the dictionary returned by a broker execution. The implementation extracts any produced files and returns them as `BrokerFile` objects containing names and short-lived download URLs.

**Call relations**: This protocol method is used after tool execution when a broker may have produced files. It keeps file transfer as references rather than moving bytes through the core service.


##### `ConnectorBroker.stage_upload`  (lines 214–222)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Defines how UFO asks a broker where a workspace file should be uploaded before a tool consumes it. This supports tools that need file inputs without routing file contents through the main service.

**Data flow**: The caller gives the workspace, provider, tool slug, filename, MIME type, and MD5 hash. The implementation returns a `StagedUpload` with a PUT URL when bytes must be uploaded, or no PUT URL when the broker already has the same content.

**Call relations**: Dynamic connector tools use this before execution when an argument points to a workspace file. The sandbox performs the upload directly to the broker’s storage, then execution receives only the staged reference.


##### `ConnectorBroker.search`  (lines 224–224)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Defines semantic search over a broker’s tools. Instead of only matching names, the broker may return tools plus planning advice, guidance, or warnings.

**Data flow**: The caller supplies workspace ID, provider, and a query. The implementation returns a `BrokerSearch` containing matching tools and optional notes about how to use them.

**Call relations**: This protocol method supports richer tool discovery. Broker implementations can provide smarter routing while callers still use one common interface.


##### `ConnectorBroker.credential`  (lines 226–226)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines how a broker resolves feed-sync credentials for a connected account. For brokered accounts, this normally returns a transport that forwards requests through the broker instead of exposing the secret.

**Data flow**: The caller gives a workspace ID, provider, and account handle. The implementation verifies the account and returns a `Credential` that the feed-sync connector can use.

**Call relations**: _credential calls this method after `_broker` finds the broker for a non-direct account. `_BoundSourceCredentials.credential` relies on the result and requires brokered source credentials to include a proxy transport.


##### `RequestForwarder.forward`  (lines 245–247)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: Defines how a captured provider HTTP request is sent through a broker using a granted account. This lets command-line tools in the sandbox authenticate without ever seeing the true provider token.

**Data flow**: The caller provides the account ID, HTTP method, URL, headers, and body bytes. The implementation forwards the request through the broker and returns a `ForwardedResponse` with status, headers, and body from the provider side.

**Call relations**: This is used through `CliCredential`, where a connector declares which environment variable and header carry a harmless sentinel value. The egress proxy can then hand matching requests to the forwarder.


##### `ConnectorResolver.transfer_hosts`  (lines 302–302)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: Defines which broker file-storage hosts are allowed for file transfers in an open connector namespace. These hosts are extra destinations the egress proxy may permit for grants handled by that resolver.

**Data flow**: The implementation exposes a tuple of host names. Callers read it; nothing is changed.

**Call relations**: This property belongs to the open-namespace resolver contract. It supports brokers that can serve many provider slugs without each one being explicitly registered.


##### `ConnectorResolver.claims`  (lines 304–304)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Defines how UFO asks whether an open resolver really serves a provider slug. This avoids assuming that an open namespace owns every unknown provider name.

**Data flow**: The caller supplies a provider slug. The implementation may consult the broker’s live catalog and returns true if that provider is available, otherwise false.

**Call relations**: This is part of the resolver protocol. Code choosing between brokered connectors and workspace-owned direct credentials can use it to avoid routing a provider to the wrong backend.


##### `ConnectorResolver.entry`  (lines 306–306)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Defines how an open resolver builds a `ConnectorEntry` for a provider it serves. The entry tells the registry which broker should receive requests for that provider.

**Data flow**: The caller provides a provider slug. The implementation returns a `ConnectorEntry` with that provider, a label, and the shared broker.

**Call relations**: ConnectorRegistry.entry and the private `_broker` helper call this when a provider is not in the explicit registry but a resolver is installed. It is the bridge from an unknown slug to a concrete broker.


##### `ConnectorResolver.catalog`  (lines 308–308)

```
async def catalog(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Defines how UFO pages through the broker’s live list of connectable services. This lets the discovery tool show services that were not hard-coded into the registry.

**Data flow**: The caller gives search text, a maximum count, and an optional cursor saying where to continue. The implementation returns a `CatalogPage` with entries and possibly another cursor.

**Call relations**: ConnectorRegistry.search_catalog and ConnectorRegistry.catalog call this when a resolver is present. The resolver’s answers are combined with explicitly registered connectors.


##### `ConnectorRegistry.entry`  (lines 325–331)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Finds the connector entry for a provider so later code knows which broker owns it. If the provider is not explicitly registered, it asks the open resolver if one exists.

**Data flow**: It receives a provider slug. It first looks in the registry’s `entries` mapping, then asks the resolver to build an entry if a resolver is installed, and otherwise raises a clear `KeyError`.

**Call relations**: This is the registry’s main routing lookup for dynamic connector code. It hands callers the `ConnectorEntry` that contains the broker they should use.


##### `ConnectorRegistry.search_catalog`  (lines 333–338)

```
async def search_catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]
```

**Purpose**: Returns search results from the open connector catalog only. If no open resolver is installed, it returns an empty result.

**Data flow**: It receives query text and a limit. It asks the resolver for the first catalog page when available, then returns just that page’s entries as a tuple.

**Call relations**: Discovery flows can use this to append live resolver results to known registered providers. It delegates the real search to `ConnectorResolver.catalog`.


##### `ConnectorRegistry.catalog`  (lines 340–360)

```
async def catalog(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Builds one combined page of connectable services from explicitly registered connectors and the open resolver catalog. It also removes duplicates by provider name.

**Data flow**: It receives search text, a limit, and an optional cursor. On the first page, it filters the explicit registry by provider or label text. It then asks the resolver for a page if one exists, appends those entries, keeps the first entry for each provider, and returns a `CatalogPage` with the resolver’s next cursor.

**Call relations**: This is used by connector discovery when a caller wants both fixed registered providers and live broker-catalog providers in one answer. It creates `CatalogEntry` and `CatalogPage` objects while merging the two sources.

*Call graph*: 2 external calls (__init__, __init__).


##### `_broker`  (lines 363–369)

```
def _broker(registry: ConnectorRegistry, provider: str) -> ConnectorBroker | None
```

**Purpose**: Finds the broker responsible for a provider, or returns nothing if no broker is available. It is a small internal routing helper.

**Data flow**: It receives a registry and provider slug. It looks for an explicit entry first, then asks the resolver for an entry if one exists, and returns that entry’s broker; if neither path works, it returns `None`.

**Call relations**: _credential calls this when it needs brokered credentials for a non-direct account. `_broker` keeps that credential logic from duplicating registry lookup rules.

*Call graph*: called by 1 (_credential).


##### `_credential`  (lines 372–385)

```
async def _credential(registry: ConnectorRegistry, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Chooses the right authentication path for a feed-sync source. Brokered accounts go to their connector broker; direct accounts go to the fallback authentication backend.

**Data flow**: It receives the registry, workspace ID, provider, and account handle. If the account is not the special direct-account handle, it finds a broker and asks it for a credential. If the account is direct, it asks the fallback auth proxy. If no suitable path exists, it raises a runtime error.

**Call relations**: _BoundSourceCredentials.credential calls this after enforcing source rules. `_credential` calls `_broker` to locate the broker for connected accounts.

*Call graph*: calls 1 internal fn (_broker); called by 1 (credential).


##### `_require_source_connection`  (lines 388–412)

```
async def _require_source_connection(workspace_id: UUID, connection_id: UUID, owner_member_id: UUID, provider: str, account: str) -> None
```

**Purpose**: Checks that a source’s saved connection is still valid for the same workspace, owner member, provider, and account. This prevents stale or mismatched feed sources from continuing to use access they no longer own.

**Data flow**: It receives the workspace ID, connection ID, owner member ID, provider, and account. It enters the workspace context, opens a database transaction, and searches the connection table for an exact match. If the match exists, it returns nothing; if not, it raises `ValueError`.

**Call relations**: _BoundSourceCredentials.credential calls this before issuing a brokered credential. `_ConnectionTransport.handle_async_request` calls it again before every proxied HTTP request, so revocation is noticed even after a credential transport has been created.

*Call graph*: called by 2 (credential, handle_async_request); 3 external calls (select, workspace_tx, ws).


##### `_ConnectionTransport.handle_async_request`  (lines 424–432)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: Wraps a brokered HTTP transport with a fresh connection-validity check before each request. This keeps a feed-sync job from continuing to send provider requests after its connection is removed or changed.

**Data flow**: It receives an HTTP request from the connector. Before forwarding it, it calls `_require_source_connection` using the stored workspace, connection, owner, provider, and account. If the check passes, it passes the request to the inner transport and returns the inner transport’s HTTP response.

**Call relations**: _BoundSourceCredentials.credential creates this wrapper around broker-provided transports. It hands actual network forwarding to the inner transport only after the database check succeeds.

*Call graph*: calls 1 internal fn (_require_source_connection).


##### `_ConnectionTransport.aclose`  (lines 434–435)

```
async def aclose(self) -> None
```

**Purpose**: Closes the wrapped HTTP transport when the client is done with it. This releases whatever network resources the inner transport owns.

**Data flow**: It takes no new data beyond the stored inner transport. It calls the inner transport’s close method and returns when that cleanup is complete.

**Call relations**: HTTP client cleanup code calls this as part of normal transport shutdown. The wrapper does not own separate resources; it simply forwards the close operation to the transport it protects.


##### `_BoundSourceCredentials.credential`  (lines 444–474)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Resolves credentials for one feed-sync source while enforcing whether that source is direct-key based or bound to a specific member-owned connection. It is the main guardrail around source credential use.

**Data flow**: It receives a workspace ID, provider, and account. For the direct account handle, it rejects sources that are connection-bound, then delegates to `_credential`. For brokered accounts, it requires stored connection and owner IDs, verifies the database connection, gets a credential through `_credential`, requires that it contains a transport, and returns a new `Credential` whose transport is wrapped in `_ConnectionTransport`.

**Call relations**: SourceCredentialResolver.bind creates this object for a particular source. This method then calls `_require_source_connection` and `_credential`, and wraps broker transports so `_ConnectionTransport.handle_async_request` can re-check access on every request.

*Call graph*: calls 2 internal fn (_credential, _require_source_connection); 2 external calls (__init__, __init__).


##### `SourceCredentialResolver.bind`  (lines 481–486)

```
def bind(self, connection_id: UUID | None, owner_member_id: UUID | None) -> AuthProxy
```

**Purpose**: Creates an authentication proxy tied to a particular feed-sync source’s connection information. This lets the sync runner ask for credentials later without forgetting which source they belong to.

**Data flow**: It receives an optional connection ID and optional owner member ID. It packages those values with the registry into a `_BoundSourceCredentials` object and returns it as an `AuthProxy`.

**Call relations**: The sync runner uses this binding step before resolving source credentials. The returned object’s `credential` method performs the actual checks and routing when the source starts authenticating provider requests.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/access/egress_rules.py`

`domain_logic` · `per-turn rule derivation before egress proxy enforcement`

A sandboxed agent should not be able to call any internet host it wants, and it should not directly see real API keys. This file builds the rulebook for the egress proxy, which is the gatekeeper for outgoing network traffic. Think of it like a security desk: it knows which doors are open, which visitors must be counted, and which badge placeholder should be replaced with a real badge only at the door.

The rules are small value objects. A scope rule says which exact hosts are allowed. An internet rule allows broader public internet access for a live turn. An injection rule replaces a fake secret value, called a sentinel, with the real secret as the request leaves the sandbox. A meter rule says requests to a host should be counted for billing or usage tracking. A forward rule sends certain authenticated requests through the broker instead of using a local secret. A service rule allows special local services to be reached through synthetic hosts.

The derivation functions build these rules from different sources: the chosen model provider, extension manifests, artifact storage, workspace credentials, connector grants, and CLI-style connector credentials. A key design point is safety by omission: if a credential cannot be resolved, this file logs a warning and simply does not open that route. That keeps one bad secret from accidentally widening network access.

#### Function details

##### `provider_host`  (lines 112–116)

```
def provider_host(model: str) -> str
```

**Purpose**: Finds which model provider host should be used for a model name. For example, model names starting with OpenAI-style prefixes map to OpenAI's API host, while Claude-style names map to Anthropic's host.

**Data flow**: It receives a model name as text. It checks the known model-name prefixes in order and returns the matching API host. If no prefix matches, it raises an error because the system would not know where that model is served.

**Call relations**: This is a helper used by derive_model_rules. Before the model rules can allow network access or inject the model API key, they need this function to identify the correct provider host.

*Call graph*: called by 1 (derive_model_rules).


##### `derive_model_rules`  (lines 119–134)

```
def derive_model_rules(model: str, real_key: str) -> tuple[Rule, ...]
```

**Purpose**: Builds the network rules needed for the sandbox to call the selected language model provider. It allows only the provider's host, arranges for the real model API key to be injected at the proxy, and marks model traffic for token metering.

**Data flow**: It receives a model name and the real API key for that model provider. It first turns the model name into a provider host, then chooses the right authentication header shape for that provider. It returns a small set of rules: allow the host, replace the sandbox's fake key with the real key, and meter the traffic as token usage.

**Call relations**: This function calls provider_host to identify the destination service. It then creates scope, injection, and meter rules that the egress proxy will later read when model requests leave the sandbox.

*Call graph*: calls 1 internal fn (provider_host); 3 external calls (__init__, __init__, __init__).


##### `derive_manifest_rules`  (lines 137–139)

```
def derive_manifest_rules(manifests: tuple[Manifest, ...]) -> tuple[InternetRule, ...]
```

**Purpose**: Checks whether any installed extension asks for sandbox internet access, and if so grants live turns access to the public internet. Without this, extensions that genuinely need internet access would be blocked by default.

**Data flow**: It receives the extension manifests. It looks for any manifest marked as needing sandbox internet. If at least one asks for it, it returns an internet rule; otherwise it returns no rules.

**Call relations**: This is one input into the overall egress rule set. It does not call other project logic; it simply turns manifest declarations into the broad internet permission the proxy understands.

*Call graph*: 1 external calls (__init__).


##### `derive_artifact_store_rules`  (lines 142–158)

```
async def derive_artifact_store_rules(blob: FilesystemBlobStore | S3BlobStore) -> tuple[Rule, ...]
```

**Purpose**: Allows the sandbox to upload shared files to the configured artifact store when that store is backed by S3. This matters because sharing a produced file may require the sandbox to PUT data to a presigned S3 URL, even if the agent otherwise has no public internet access.

**Data flow**: It receives the blob store configuration. If the store is S3, it asks the store for the upload host, then returns rules that allow exactly that host and meter requests to it. If the store is local filesystem storage, it returns no network rules because no external host is needed.

**Call relations**: This function is used when composing the sandbox's allowed outbound routes. It hands the proxy exact host permission for artifact uploads without granting full public internet access.

*Call graph*: 3 external calls (__init__, __init__, put_host).


##### `derive_credential_rules`  (lines 161–224)

```
async def derive_credential_rules(slots: tuple[CredentialSlot, ...], workspace_id: UUID, store: CredentialStore) -> tuple[Rule, ...]
```

**Purpose**: Builds rules for workspace credentials that should be safely injected into outgoing requests. It lets a sandbox use stored credentials without ever placing the raw secret inside the sandbox itself.

**Data flow**: It receives credential slot declarations, a workspace ID, and the credential store. For each slot that has an injection target, it tries to read the stored secret and resolve the host that credential is allowed to reach. If either step fails or produces no usable result, it logs a warning or skips the slot. For usable slots, it creates injection rules, groups them by host, adds one host allow rule per host, and adds metering when the slot declares a metering dimension. For Git basic authentication, it converts the username and secret into the Basic authorization format before building the injection rule.

**Call relations**: This function is a major part of per-workspace egress setup. It calls the credential store helpers to fetch secrets and resolve hosts, uses warning logs when a slot cannot be used, and returns the rules the proxy needs to allow and authenticate only those credential-backed destinations.

*Call graph*: 7 external calls (__init__, __init__, __init__, b64encode, credential_host, slot_secret, warn).


##### `derive_grant_rules`  (lines 227–244)

```
def derive_grant_rules(grants: tuple[Grant, ...], transfer_hosts: 'ConnectorTransferHosts | None'=None) -> tuple[Rule, ...]
```

**Purpose**: Builds network allow and metering rules for active connector grants. A grant admits the connector provider's host, plus any broker file-transfer hosts needed to move tool input and output files.

**Data flow**: It receives active grants and, optionally, a lookup object for connector transfer hosts. For each grant, it collects the provider host and any extra transfer hosts, removes duplicates while preserving order, and ignores empty host names. If any hosts remain, it returns a scope rule for them and a request-metering rule for each host.

**Call relations**: This function is used when connector access has already been granted. It does not inject credentials; connector secrets live with the broker. It may ask ConnectorTransferHosts.of for extra file-store hosts, then hands the proxy only admission and metering rules.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_cli_rules`  (lines 247–267)

```
def derive_cli_rules(grants: tuple[Grant, ...], acting_member_id: UUID | None, clis: Mapping[str, CliCredential]) -> tuple[Rule, ...]
```

**Purpose**: Creates forwarding rules for connector grants that expose a command-line-style credential. These rules say that requests carrying the grant's sentinel should be executed through the broker, where the real account credential lives.

**Data flow**: It receives grants, the acting member ID if there is one, and a mapping of connector providers to CLI credential declarations. It keeps only grants whose provider has a CLI credential and whose account the acting member is allowed to use: either a shared connection or the member's own grant. For each allowed grant, it creates a forward rule with the host, header, sentinel, account ID, and broker forwarding behavior.

**Call relations**: This sits beside connector tool authorization. It uses grant_sentinel to recognize the placeholder credential value, then returns forward rules that tell the egress proxy to send matching requests through the broker instead of trying to inject a local secret.

*Call graph*: 2 external calls (__init__, grant_sentinel).


##### `ConnectorTransferHosts.of`  (lines 281–282)

```
def of(self, provider: str) -> tuple[str, ...]
```

**Purpose**: Looks up which file-transfer hosts should be allowed for a connector provider. If the provider was explicitly listed, it uses that provider's declared hosts; otherwise it falls back to the default open-namespace hosts.

**Data flow**: It receives a provider name. It checks the explicit provider-to-hosts mapping stored on the object. If the provider is present, it returns that tuple of hosts; if not, it returns the default tuple.

**Call relations**: derive_grant_rules calls this when it needs to add broker file-store hosts for a grant. This small lookup keeps the grant-rule code from needing to know how manifest defaults and explicit connector declarations were built.


##### `connector_transfer_hosts`  (lines 285–296)

```
def connector_transfer_hosts(manifests: tuple[Manifest, ...]) -> ConnectorTransferHosts
```

**Purpose**: Builds the lookup table that maps connector providers to the broker file-store hosts they may need. It also records default transfer hosts for providers covered by the open connector namespace.

**Data flow**: It receives all manifests. It walks through every declared connector and records that connector's provider name with its declared transfer hosts. Then it checks for an open connector namespace and, if present, uses that namespace's transfer hosts as the default. It returns a ConnectorTransferHosts object containing both the explicit map and the default hosts.

**Call relations**: This function prepares data later used by derive_grant_rules. It calls open_connector_namespace to find the default namespace, then packages the result into ConnectorTransferHosts so grant rule derivation can make a simple provider lookup.

*Call graph*: 2 external calls (__init__, open_connector_namespace).

## 📊 State Registers Touched

- `reg-effective-config` — The current trusted settings for how the service should run, including database, provider, deployment, and safety options.
- `reg-extension-capability-registry` — The live catalog of everything enabled extensions add, such as tools, routes, jobs, credentials, hooks, and backends.
- `reg-database-session-workspace-scope` — The shared database access layer that keeps reads and writes inside the right workspace and transaction.
- `reg-member-auth-principals` — The shared answer to who the current person or service is and what member identity they are acting as.
- `reg-surface-routing` — The mapping from outside places like web, Slack, terminal, and iMessage to the right workspace, conversation, member, and agent.
- `reg-tool-catalog` — The current list of tools the agent may call, with their names, inputs, permissions, and implementations.
- `reg-billing-ledger` — The shared money and usage record for tokens, images, videos, sandbox use, egress, balances, caps, and exports.
- `reg-credential-vault` — The encrypted store of API keys, connected accounts, grants, and approvals that lets tools use outside services without exposing secrets.
- `reg-egress-policy` — The shared network exit rules that decide which outside addresses sandboxes may contact and when secrets may be added.
- `reg-sandbox-handles` — The remembered execution workspaces, browser workbenches, terminal sessions, and sandbox IDs used across a conversation or turn.
- `reg-source-sync-state` — The saved state of connected content sources, including pages, checkpoints, errors, ownership, and read grants.
- `reg-observability-context` — The shared tracing, logging, metrics, health, and redaction context used to understand what happened safely.
- `reg-extension-data-store` — Durable extension-scoped key/value or JSON state used by installed extensions beyond their manifest capabilities and lockfile selection.
- `reg-auth-and-oauth-flow-state` — Short-lived login and OAuth handoff state such as nonces, return targets, code-verifier data, pending claims, and callback correlation before it becomes an authenticated principal or stored credential.
- `reg-runtime-connection-pools` — Live pooled connections and reusable clients for shared services such as the database, Redis/live hub, blob storage, model providers, connector APIs, and sandbox/browser providers.
- `reg-egress-policy-generation` — Per-workspace egress-rule version or invalidation counter used to rebuild cached sandbox proxy rules after credential, grant, or network-policy changes.
