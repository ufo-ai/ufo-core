# Source connector registration and account bootstrap  `stage-14.1.2`

This stage is behind-the-scenes setup for bringing outside services into the system. It makes sure the app knows which external source connectors exist, and it prepares the basic “feed” records needed when a member links an account. A connector is the adapter that knows how to talk to a provider such as Slack or Salesforce.

The registry file is like the system’s address book for connectors. It lists every supported provider and stores them under their public names, so other parts of the app can ask for “Slack” or “Salesforce” and get the right connector code.

The connected file runs when a member connects one of those provider accounts. It creates the first feed rows for that account’s main streams, so the rest of the system has something to read from and sync. If that first setup fails or leaves gaps, it also offers a retry path that can create the missing feed rows later. Together, these files turn “we support this provider” into “this member’s account is ready to use.”

## Files in this stage

### Connector Registration and Feed Bootstrap
Defines the available external source connectors, then creates or repairs the initial feed rows for newly connected provider accounts.

### `extensions/sources/ufo_ext_sources/registry.py`

`config` · `startup / config load`

This file solves a simple but important problem: when a stored source says its backend is “slack” or “github”, the system needs to know which connector class should be used to talk to that service. Instead of searching the codebase at startup, this file imports every supported connector and lists them explicitly. That is like keeping a printed phone directory rather than wandering through every office to ask who works there.

The central result is `CONNECTORS`, a dictionary that maps each connector’s `name` to the connector class itself. Other parts of the source-sync system can use that map to turn a saved backend name into working code for the right provider.

The helper `_connector_registry` builds this dictionary and checks for duplicate names. That check matters because two connectors with the same name would make the lookup ambiguous: the system would not know which provider to use for a source row. The file also defines `SOURCE_KIND = "source"`, a shared label used by the surrounding source framework.

The important design choice here is that adding a provider requires editing this file and adding it to the list. That is deliberate: it avoids hidden import-time discovery and makes supported connectors easy to audit.

#### Function details

##### `_connector_registry`  (lines 66–74)

```
def _connector_registry(connector_types: tuple[type[Connector], ...]) -> dict[str, type[Connector]]
```

**Purpose**: Builds the connector lookup table from a fixed list of connector classes. It also protects the system from two connectors accidentally using the same name.

**Data flow**: It receives a tuple of connector classes. For each class, it reads the class’s `name`, checks whether that name has already been used, and then stores the class under that name in a dictionary. It returns the finished dictionary, or raises an error before returning if it finds a duplicate name.

**Call relations**: This helper is used when the module is loaded to create the global `CONNECTORS` registry. The top-level list of imported provider classes is handed into it, and its returned dictionary becomes the map that the rest of the source system can consult when it needs the connector for a backend name.


### `extensions/sources/ufo_ext_sources/connected.py`

`domain_logic` · `connection callback and scheduled retry`

When a member connects an outside account, the system has proof that the account is allowed, but it still needs actual source rows for the account's useful streams. A source row is the thing the rest of the system can later sync and read from. Without this file, a connected account could exist but not produce any content until the member took another action.

The file has two entry paths. One runs immediately after a connection is recorded, so the member gets feeds before the connection flow finishes. The other runs later as a safety net, like a clerk checking for paperwork that should have been filed but was missed.

The central class, ConnectedSources, looks at all connections held by the main agent, finds the connector for each provider, and creates sources only for that connector's canonical streams. Canonical streams are the provider's core streams, not every possible list the provider's API exposes.

It is careful not to overwrite member choices. If a source row already exists, it leaves it alone. If the member removed that source before, it does not recreate it. If the existing rows for a connection have been shared beyond the member's private subject, it also backs away, because this auto-registration should only create private feeds for the member who owns the connection. Providers that require a tenant-specific base URL are skipped, because only the member can supply that missing address.

#### Function details

##### `on_connection_recorded`  (lines 51–58)

```
async def on_connection_recorded(ctx: HookContext) -> HookOutcome
```

**Purpose**: This is the hook that runs when a new account connection has just been saved. It starts source creation right away so the connected account has its main feeds without the member needing to click or request anything else.

**Data flow**: It receives a hook context containing an event payload. If the payload says a connection was recorded, it takes that connection's ID, builds a ConnectedSources helper around the extension context, and asks it to register feeds for that one connection. If the payload is not the expected kind, it raises an error because this hook was called for the wrong event. It returns no special outcome.

**Call relations**: The extension system calls this during the connection-recorded hook. This function is the immediate path: it turns the event into a call to ConnectedSources, which does the detailed work of finding streams and registering missing source rows.

*Call graph*: 1 external calls (__init__).


##### `retry_connected_sources`  (lines 61–63)

```
async def retry_connected_sources(ctx: ExtensionContext) -> None
```

**Purpose**: This is the repair job for connected-account feeds. It creates any source rows that should exist but were missed because an earlier hook failed, a process stopped halfway through, or a grant arrived by another path.

**Data flow**: It receives the extension context for the current run. It builds a ConnectedSources helper and asks it to register feeds without naming a single connection, which means it checks all relevant main-agent connections. It does not return data; its effect is any missing source rows being added.

**Call relations**: A scheduled or background job calls this later, outside the connection callback. It uses the same ConnectedSources path as the hook, so retry behavior follows the same rules about existing rows, removed rows, private ownership, and canonical streams.

*Call graph*: 1 external calls (__init__).


##### `ConnectedSources.register`  (lines 74–82)

```
async def register(self, connection_id: UUID | None=None) -> None
```

**Purpose**: This method finds connected accounts that should have automatic source rows and sends each suitable one to the detailed registration step. It can work on one connection, for the live hook, or on all connections, for the retry job.

**Data flow**: It first reads the currently live source rows from the extension context. Then it reads the main-agent connections and, if a specific connection ID was provided, ignores all others. For each remaining connection, it looks up the connector class for that provider. If the provider is unknown, or if the connector lacks a base URL and therefore needs member-specific setup, it skips it. For each usable connector, it creates a connector instance and passes the connection, connector, and current source rows to _register.

**Call relations**: Both on_connection_recorded and retry_connected_sources rely on this method after creating a ConnectedSources object. It is the broad filter stage: it decides which connections are eligible, then hands each eligible connection to ConnectedSources._register for the stream-by-stream work.

*Call graph*: calls 1 internal fn (_register); 2 external calls (main_agent_connections, get).


##### `ConnectedSources._register`  (lines 84–129)

```
async def _register(self, connection: MainAgentConnection, connector: Connector, live: tuple[SourceRecord, ...]) -> None
```

**Purpose**: This method creates the missing source rows for one connected account's canonical streams. It protects existing member choices by not replacing existing rows, not reviving removed rows, and not adding private rows into a connection that already has non-private source rows.

**Data flow**: It receives one main-agent connection, the connector for that provider, and the already-live source records. It turns the connection owner into that member's private subject, then finds source rows already bound to this connection. If any existing bound row belongs to a different subject, it stops, because auto-created rows must stay private to the owner. If there is an existing bound row, it reads its backfill request, meaning how far back the first sync should look. It then walks through the connector's streams and keeps only canonical ones. For each stream, it calculates the effective backfill date, builds a source configuration for that account and stream, and computes the stable source ID the row would have. If that ID is not already live, it remembers it as a possible new row. Before creating anything, it asks which of those possible IDs were previously removed. Finally, it registers only the fresh source rows that were not removed, granting them to the owner's private subject and tying them to the connection.

**Call relations**: ConnectedSources.register calls this after it has found an eligible connection and connector. This method calls into the connector to learn its streams, uses the source configuration model to read and build configuration, uses effective_days to calculate the allowed backfill window, and uses the extension context to check removed IDs and create new source rows.

*Call graph*: calls 1 internal fn (streams); called by 1 (register); 6 external calls (__init__, model_validate, now, timedelta, member_subject, effective_days).
