# Adapter manifests and provider registries  `stage-19.1`

This stage is shared startup support. It does not move data itself. Instead, it tells UFO what outside systems it can connect to and which piece of code should be used for each choice. It is like a directory at the front desk: when the configuration says “use this provider,” UFO looks here to find the right worker.

The Redis hub manifest announces that a Redis-backed frame hub is available. A frame hub is the place UFO uses to pass units of work or data frames between parts of the system. If the user selects Redis in the configuration, this manifest lets UFO create the correct Redis hub object instead of some other hub.

The source connector registry is the address book for input sources. It links short backend names, such as Slack or Stripe, to the Python classes that know how to read from those services. Together, these files make external adapters discoverable, so the rest of UFO can stay generic and choose the right connector or hub at startup.

## Files in this stage

### Provider Registries
Registration modules that expose external frame hub and source connector backends to the UFO system.

### `extensions/redis_hub/ufo_ext_redis_hub/manifest.py`

`config` · `startup`

A hub is the part of the system that spreads live frames to whoever needs them. The default hub works inside one running server process. This extension offers a Redis Streams version, which can share frames across multiple server instances. Redis Streams is a Redis feature for storing ordered messages, a bit like a shared conveyor belt that several workers can add to and read from.

This file is the extension’s sign-up sheet. It gives the extension a name and version, declares that it provides a hub backend called "redis", and tells the core system how to create that backend. The important safety rule is that Redis needs a connection address, called `hub.url` in configuration. If someone selects the Redis backend but forgets the URL, the file raises a clear error immediately. That is better than letting the system start and then fail later when the first frame is published.

The manifest itself does not publish frames or talk to Redis directly. Instead, it points the core system to `RedisStreamHub`, the class that does the real Redis work. In short, this file is the bridge between configuration choice and the concrete Redis hub implementation.

#### Function details

##### `_build_hub`  (lines 17–22)

```
def _build_hub(url: str | None) -> Hub
```

**Purpose**: This function creates the Redis-backed hub after the system has chosen the Redis backend. It also checks that the Redis connection URL was supplied, so configuration mistakes fail early with a useful message.

**Data flow**: It receives a Redis URL, or no URL at all. If the URL is missing, it stops and raises an error explaining that `hub.url` is required. If the URL is present, it passes that address into `RedisStreamHub` and returns the new hub object for the rest of the system to use.

**Call relations**: This function is handed to the hub registration created by `manifest`, so the core system can call it later when it needs to build the selected hub. Its main handoff is to `RedisStreamHub`, which takes over the actual work of using Redis Streams.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 25–30)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the extension to the core system. It says: this extension is named `redis_hub`, has a version, and provides a hub backend named `redis` that can be built with `_build_hub`.

**Data flow**: It starts from the constants in this file: the extension name, version, and backend name. It wraps the Redis hub builder in a `HubSpec`, then places that specification inside a `Manifest`. The returned manifest is the package the core system reads to discover what this extension offers.

**Call relations**: This is the public entry point for extension discovery. When the core system scans installed extensions, it calls `manifest` to learn about available pieces. `manifest` creates the hub specification and connects the backend name `redis` to `_build_hub`, so a later configuration choice can become a real Redis hub.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/sources/ufo_ext_sources/registry.py`

`config` · `startup`

This registry solves a simple but important problem: when the system sees a source that says its backend is something like "github" or "airtable", it needs to know which connector code to use. Rather than searching the whole codebase at startup, this file imports every supported connector explicitly and puts them into one dictionary called `CONNECTORS`.

Think of it like a hotel front desk directory. Each service has a room name, and the registry tells the rest of the system which door to open. The key is the connector's `name`, which also matches the backend name stored on a source row and the credential slot used to find the right secret or API key.

The file also protects against a subtle but serious mistake: two connectors cannot claim the same name. If that happened, the system might send a Salesforce source to the wrong connector, or one connector could silently replace another. The helper function builds the dictionary and raises an error immediately if it finds a duplicate. Without this file, the sync driver would not have a clear, reliable list of source backends it can run.

#### Function details

##### `_connector_registry`  (lines 59–67)

```
def _connector_registry(connector_types: tuple[type[Connector], ...]) -> dict[str, type[Connector]]
```

**Purpose**: Builds the lookup table from connector name to connector class. It also checks that no two connectors use the same name, because duplicate names would make it unclear which connector should be used.

**Data flow**: It receives a tuple of connector classes. It starts with an empty dictionary, reads the `name` from each connector class, and stores that name as the key with the class as the value. If a name is already present, it stops and raises an error; otherwise it returns the completed dictionary.

**Call relations**: This function is used in this file when `CONNECTORS` is created. The file hands it the full list of imported connector classes, and the function hands back the registry that the rest of the source-sync system can use to choose the right connector by backend name.
