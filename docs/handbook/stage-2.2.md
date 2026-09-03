# Feature flag reads and operator flag writes  `stage-2.2`

This stage is shared behind-the-scenes support for feature flags, which are small switches that turn behavior on or off without changing the code. Other parts of the system use these switches during normal work, for example to try a new feature only for certain workspaces.

The core file, `core/src/ufo/flags.py`, is the safe front door for reading those switches. Callers ask it for a flag value and also provide a default. If the flag service is not configured, is too slow, fails, or returns something the code cannot use, this layer returns the default instead. That keeps a user’s request from failing just because the flag system has a problem.

The extension file, `extensions/flagship/ufo_ext_flagship.py`, connects that safe front door to Cloudflare Flagship, the outside service used to store and serve flag choices. It also gives operators a way to change which variation a flag returns without redeploying the product. Together, these files act like a safe control panel: app code reads switches through a guardrail, while operators can adjust those switches in the backing service.

## Files in this stage

### Safe flag reads
Provides a resilient core interface for reading feature flags with caller defaults when the flag service is unavailable or returns unusable data.

### `core/src/ufo/flags.py`

`util` · `startup and feature checks during normal work`

Feature flags are like remote light switches for product behavior. They let a deployment turn a feature on or off without changing code, often for one workspace at a time. This file is the single doorway through which the core code talks to OpenFeature, a standard library for reading flags from different flag providers.

At startup, `init_flags` may attach the deployment’s chosen flag provider to the process-wide OpenFeature API. If there is no provider, it deliberately leaves OpenFeature’s no-op provider in place, so every flag read simply returns the code’s default value.

During normal work, other code asks `flag_enabled` whether a named flag is on. The function builds an evaluation context using the current workspace ID, so a backend can answer differently for different workspaces. It asks the flag service for a string value, not a real boolean, because the deployment stores flag variations as the strings `"true"` and `"false"`. Any other value is treated as unreadable.

The most important behavior is that this file “fails closed.” A bad token, provider error, timeout, strange value, or exception does not crash the flow or leave it waiting on the network. It logs a warning and returns the default chosen by the call site.

#### Function details

##### `init_flags`  (lines 37–42)

```
def init_flags(provider: FeatureProvider | None) -> None
```

**Purpose**: Connects the deployment’s chosen feature flag provider to OpenFeature, the shared feature-flag interface used by the process. If no provider is supplied, it does nothing, which means all future flag reads will fall back to their code defaults.

**Data flow**: It receives either a feature flag provider object or `None`. If it gets `None`, nothing changes. If it gets a provider, it gives that provider to OpenFeature as the process-wide source of flag answers; the function returns nothing.

**Call relations**: Startup or bootstrapping code calls this once after deciding which flag backend, if any, the deployment selected. When a provider is present, this function hands it to `openfeature.api.set_provider`, so later calls to `flag_enabled` ask that backend through OpenFeature.

*Call graph*: 1 external calls (set_provider).


##### `flag_enabled`  (lines 45–75)

```
async def flag_enabled(flag: str, *, default: bool) -> bool
```

**Purpose**: Answers the question: “Is this feature flag on for the current workspace?” It is designed to be safe: if the flag system cannot give a clear answer quickly, it returns the caller’s default value and records a warning.

**Data flow**: It takes a flag name and a required default boolean. It reads the current workspace ID, uses it as the targeting key for the flag lookup, and converts the default into the string form expected by the flag service. It then asks OpenFeature for the flag value, with a short timeout. If the lookup raises an error, times out, reports an error code, or returns something other than `"true"` or `"false"`, it warns and returns the default. If the value is valid, it returns `true` for `"true"` and `false` for `"false"`.

**Call relations**: Feature code calls this whenever it needs to decide whether to offer gated behavior. Inside, it uses `ws_current` to find the workspace, builds an OpenFeature `EvaluationContext` so the provider can make a workspace-specific decision, asks `openfeature.api.get_client` for the active client, wraps the lookup in `asyncio.timeout` so it cannot hang, and uses `warn` to report unresolved or unreadable flag answers.

*Call graph*: 5 external calls (timeout, get_client, EvaluationContext, warn, ws_current).


### Flagship integration
Connects feature flag reads and operator-controlled variation updates to Cloudflare Flagship without requiring product redeploys.

### `extensions/flagship/ufo_ext_flagship.py`

`io_transport` · `startup for flag reads; operator flag commands for writes`

Feature flags are switches that let the product turn behavior on or off without changing code. This file makes Cloudflare Flagship the outside service that stores those switches. At startup, it looks for the deploy-level Cloudflare app ID, account ID, and read token. If any are missing, it does not install a Flagship provider, so the rest of the app falls back to each flag’s built-in default. That means a badly configured or unreachable flag service does not crash the product; it simply leaves features in their safe default state.

The file has two sides. The read side is `build`, which creates the OpenFeature provider. OpenFeature is a common interface for asking “is this flag on?” so the rest of the code does not need to know the vendor-specific details. Reads use short timeouts and no retries, so a slow Flagship request cannot hold up the app for long.

The write side is `FlagshipAdmin`, used by an operator command such as `ufoctl flags set`. It reads a flag from Cloudflare, changes only its `default_variation` to `on` or `off`, and writes the full flag record back. This is careful because Cloudflare’s update endpoint expects the whole flag, not just the one changed field. Like editing a form where every field must be resubmitted, it carries forward the existing rollout rules and metadata so they are not accidentally erased.

#### Function details

##### `build`  (lines 52–73)

```
def build(cache_ttl_seconds: float) -> FeatureProvider | None
```

**Purpose**: Creates the Cloudflare Flagship provider that the app uses to read feature flags through OpenFeature. If the needed deploy settings are missing, it returns nothing so flags use their code defaults instead of failing the app.

**Data flow**: It reads three deploy environment values: the Flagship app ID, the Cloudflare account ID, and the read-only Flagship token. If any value is absent, it logs a warning saying which pieces are present and returns `None`. If all are present, it builds a `FlagshipServerProvider` with the app details, a short timeout, no retries, and the requested cache time, then returns that provider.

**Call relations**: The extension manifest points the flag system to this function as the builder for the `flagship` backend. During startup, core code can call it to attach the Flagship provider. It relies on `deploy_env` to read deploy secrets, `warn` to report missing setup, and Cloudflare’s `FlagshipServerProvider` to do the actual flag reads.

*Call graph*: 3 external calls (FlagshipServerProvider, deploy_env, warn).


##### `FlagshipAdmin.serve`  (lines 95–103)

```
def serve(self, key: str, *, on: bool) -> None
```

**Purpose**: Changes which variation of one Flagship flag is served by default: either `on` or `off`. It is used for deliberate operator writes, not for normal per-request flag checks.

**Data flow**: It receives a flag key and an `on` choice. First it asks Cloudflare for the current full flag record. It checks that the record is readable and that the requested variation exists. Then it copies all returned fields except read-only answer fields such as `updated_at` and `updated_by`, replaces `default_variation` with `on` or `off`, and sends the updated full record back. It returns no value, but it changes the flag stored in Cloudflare.

**Call relations**: This method is part of the admin path used by flag-setting commands. It depends on `FlagshipAdmin._call` for both the read request and the write request, so all HTTP details and Cloudflare error handling stay in one helper.

*Call graph*: calls 1 internal fn (_call).


##### `FlagshipAdmin.list`  (lines 105–117)

```
def list(self) -> tuple[str, ...]
```

**Purpose**: Fetches the names of all flags currently present in the configured Flagship app. This lets an operator or tool see what the service itself contains, which may reveal drift from what infrastructure code expects.

**Data flow**: It sends a request for the app’s flag collection. It expects Cloudflare to return a list of flag records. From each record it takes the `key`, converts it to text, sorts all keys, and returns them as an immutable tuple. If the response is not a readable list, it raises an error.

**Call relations**: This is another admin operation on `FlagshipAdmin`. Like `serve`, it hands the actual HTTP work to `FlagshipAdmin._call`, then turns the returned Cloudflare data into a simple list of flag keys for callers.

*Call graph*: calls 1 internal fn (_call).


##### `FlagshipAdmin._call`  (lines 119–134)

```
def _call(self, method: str, path: str, body: dict[str, object] | None=None) -> dict[str, object]
```

**Purpose**: Sends one authenticated HTTP request to Cloudflare’s Flagship API and turns Cloudflare’s response into either usable data or a clear error. It is the shared doorway used by the admin methods.

**Data flow**: It receives an HTTP method such as `GET` or `PUT`, an API path, and optionally a JSON body. It builds the full Cloudflare URL from the stored account ID and app ID, adds the bearer token for authorization, and sends the request with the configured HTTP client. It parses the JSON response when present. If Cloudflare reports an HTTP error or a failed API result, it raises a `RuntimeError` with the status and error details. Otherwise it returns the parsed response dictionary.

**Call relations**: Both `FlagshipAdmin.serve` and `FlagshipAdmin.list` call this helper whenever they need to talk to Cloudflare. Keeping request formatting and error interpretation here means the higher-level methods can focus on flag behavior rather than network plumbing.

*Call graph*: called by 2 (list, serve).


##### `build_admin`  (lines 137–154)

```
def build_admin() -> FlagshipAdmin
```

**Purpose**: Creates a `FlagshipAdmin` client for commands that write flag values. Unlike the read provider, it fails loudly if required write credentials are missing, because an operator asked for a change and needs to know why it cannot happen.

**Data flow**: It reads the Cloudflare account ID, Flagship app ID, and the separate write token from deploy environment values. It gathers the names of any missing values. If anything is missing, it raises an error listing the required keys. If all values are present, it returns a `FlagshipAdmin` configured with those credentials.

**Call relations**: This is the setup function for the admin write path, such as `ufoctl flags set`. It uses `deploy_env` to load credentials and then constructs `FlagshipAdmin`, whose methods perform the actual list or serve operations.

*Call graph*: 2 external calls (__init__, deploy_env).


##### `manifest`  (lines 157–163)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the rest of the system. It tells the host what the extension is called, which deploy environment keys it needs, and how to build the Flagship flag provider.

**Data flow**: It takes no input. It creates a `Manifest` containing the extension name and version, the three deploy keys needed for read-time flag evaluation, and a `FlagProviderSpec` that links the `flagship` backend name to the `build` function. The finished manifest is returned to the extension loader.

**Call relations**: The extension system calls this to discover what this file offers. Through the returned `FlagProviderSpec`, the core flag machinery later knows to call `build` when it wants a Flagship-backed OpenFeature provider.

*Call graph*: 2 external calls (__init__, __init__).
