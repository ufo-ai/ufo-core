# Direct and Keyed API-Credential Connectors  `stage-13.2`

This stage is shared support for connecting to outside services when the member provides an API key directly. An API key is a secret string that proves the user is allowed to call a service, like a password made for software. This path is used instead of an OAuth login, where the system would normally send the user through a brokered sign-in flow.

The keyed connector file defines which outside services work this way. It builds a manifest, which is a structured description the system can read. That manifest tells UFO what credential to ask for, how to store it safely, and how to attach it to outgoing web requests. The goal is to let the sandboxed code use the service without ever seeing the raw secret.

The direct source connector file is the runtime side of the same idea. When a sync job needs data from a direct source, it reads the stored API key from the credential store and turns it into a bearer credential, which is the form used in web request headers. Together, these files define the connector and then safely use its key.

## Files in this stage

### API-Key Connector Credentials
Defines keyed connector manifests and the direct credential retrieval path for member-supplied API keys.

### `extensions/keyed_connectors/ufo_ext_keyed_connectors.py`

`config` · `startup / manifest load`

Some services cannot be connected through a broker such as Composio or Pipedream because the user already owns the API key and no broker creates it for them. This file covers that case. It is like a locked mail slot: the user privately places a key into the system, the sandbox only sees a harmless placeholder, and the outgoing request gateway swaps the real key in only when the request is going to the right service host.

The file defines two small declaration types. `KeyedSecret` describes one secret value a provider needs, such as an API key header and the environment variable name the sandbox should see. `KeyedProvider` describes a service such as Datadog or PostHog: its name, human label, required secrets, and either one fixed API host or a closed list of allowed hosts the user can choose from.

That closed host list is important. For providers with regional API sites, such as Datadog, the user chooses from known safe hostnames rather than typing an arbitrary server name. This prevents a key meant for one region or service from being sent somewhere unexpected.

At the bottom, the file lists the supported keyed providers and builds a prompt section explaining how agents should use them. The exported `manifest()` function packages all credential slots and instructions so the larger system can discover them.

#### Function details

##### `KeyedSecret.__post_init__`  (lines 56–61)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that a declared secret uses only an authentication scheme the egress proxy knows how to safely replace. It prevents someone from adding a provider declaration that looks valid but cannot actually be handled on the wire.

**Data flow**: A newly created `KeyedSecret` comes in with fields such as its header name, environment variable name, and optional scheme like `Bearer`. The function compares the scheme against the small approved set. If it is allowed or absent, nothing changes; if it is unsupported, object creation stops with a clear error.

**Call relations**: This runs automatically when a `KeyedSecret` is created, including the secrets declared for Datadog and PostHog in this file. Later code assumes these declarations are safe to convert into injection rules, so this early check protects `KeyedProvider.slots` and the final manifest from bad entries.


##### `KeyedProvider.__post_init__`  (lines 79–88)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that each provider declaration names its API destination in exactly one safe way: either one fixed host, or a fixed menu of allowed regional hosts. It also makes sure regional providers include enough information to ask the user for their chosen host and pass that choice into the sandbox.

**Data flow**: A newly created `KeyedProvider` comes in with provider details, secrets, and either `host` or `sites`. The function validates that the declaration is not missing both and does not provide both at once. If `sites` is used, it also requires a host environment variable and a user-facing description. Valid declarations pass through unchanged; invalid declarations raise an error immediately.

**Call relations**: This runs automatically when each provider row is built in `KEYED_PROVIDERS`. It keeps later steps, especially `KeyedProvider.target_host`, `KeyedProvider.slots`, and `manifest`, from having to guess what kind of host setup they are dealing with.


##### `KeyedProvider.target_host`  (lines 91–100)

```
def target_host(self) -> str | HostChoice
```

**Purpose**: This property gives the rest of the file the provider’s API host in the right form. For simple providers it returns the fixed hostname; for regional providers it creates a controlled host choice that the user can select from.

**Data flow**: The function reads the provider’s `host`, `sites`, provider name, host environment variable, and site description. If there is one fixed host, it returns that hostname as plain text. If there are several allowed sites, it builds and returns a `HostChoice`, which records the credential slot name, allowed hostnames, default host, description, and environment variable used to expose the choice.

**Call relations**: This is used by `KeyedProvider.slots` when building credential slots and by `KeyedProvider.usage` when writing human-readable instructions. When a provider has selectable sites, it hands off to `HostChoice` so the manifest can represent the host choice in a structured way.

*Call graph*: 1 external calls (__init__).


##### `KeyedProvider.slots`  (lines 102–120)

```
def slots(self) -> tuple[CredentialSlot, ...]
```

**Purpose**: This turns a provider declaration into the credential slots the rest of the system understands. Each slot tells the system what secret to ask the user for and exactly where that secret may be injected into outgoing requests.

**Data flow**: The function starts with a provider and its secrets. For each secret, it creates a credential slot with a name, description, and injection rule: the allowed host, HTTP header, sandbox sentinel value, environment variable, and request dimension. If the provider also needs the user to choose a host, it adds one extra credential slot for that host choice. The result is a tuple of credential slot objects ready for the manifest.

**Call relations**: This is called by `manifest` while collecting all credential slots from every provider in `KEYED_PROVIDERS`. It relies on `KeyedProvider.target_host` for the safe destination and hands the final rules to `CredentialSlot` and `InjectionTarget`, which are SDK types used by the broader UFO manifest system.

*Call graph*: 2 external calls (__init__, __init__).


##### `KeyedProvider.usage`  (lines 122–135)

```
def usage(self) -> str
```

**Purpose**: This creates a short instruction line showing an agent how to call the provider’s REST API from the sandbox. It names the required slots and gives a sample `curl` command that uses the sandbox environment variables rather than raw secrets.

**Data flow**: The function reads the provider name, label, secrets, host information, and environment variable names. It formats the needed HTTP headers, chooses either the fixed host or the host environment variable, and lists the credential slot names. It returns one human-readable bullet point for inclusion in the prompt text.

**Call relations**: This is used while building `SECTION_BODY`, the prompt section that explains keyed providers to the agent. It depends on `KeyedProvider.target_host` to describe fixed-host and host-choice providers correctly.


##### `manifest`  (lines 220–226)

```
def manifest() -> Manifest
```

**Purpose**: This is the file’s public entry point for the UFO extension system. It packages the keyed provider credential declarations and user-facing instructions into a manifest that the larger system can load.

**Data flow**: The function reads the extension name, version, provider table, and prepared prompt text. It asks each provider for its credential slots, flattens those into one tuple, wraps the instructions in a prompt section, and returns a `Manifest` object containing all of it.

**Call relations**: The extension loader calls this when it needs to discover what this extension contributes. Inside, it calls each provider’s `slots` method to gather credential requirements, then hands the assembled data to the SDK’s `Manifest` and `PromptSection` types so the rest of UFO can request credentials and teach agents how to use them.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/sources/ufo_ext_sources/direct.py`

`domain_logic` · `source sync authentication`

Some data sources need an API key that the system cannot get through its normal account-broker flow. In those cases, a member brings their own key. This file is the small bridge that lets a feed-sync job use that key without exposing it to the wrong place.

The key is stored encrypted under the connector provider’s name. When a sync run is routed through the special direct account path, `DirectAuthProxy` looks up the provider’s credential using `CredentialAccess`, which is the controlled doorway to the workspace’s stored secrets. It then wraps the secret as a bearer credential, meaning it will be used like an HTTP “Bearer” token when the connector talks to the outside provider.

The important safety rule is that the key is read only on the host side, inside the sync job. It is not passed into the sandbox or shown to the agent. A useful analogy is a locked office mailroom: the sync worker can briefly open the right locked box to send a package, but it does not hand the key to visitors or write it on the envelope.

#### Function details

##### `DirectAuthProxy.credential`  (lines 29–30)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: This method retrieves the stored API key for a provider and returns it in the standard credential shape expected by the rest of the sync system. It is used when a connector authenticates directly with a member-provided key.

**Data flow**: It receives a workspace ID, a provider name, and an account handle. The provider name is used to read the matching secret from `CredentialAccess`; the account handle only indicates that this direct route was chosen. The method then creates and returns a `Credential` whose bearer token is the retrieved secret.

**Call relations**: When the source sync needs credentials for a direct-account connector, it calls this method. The method reads the secret through the credential access layer, then hands the value into `Credential.__init__` so the rest of the sync code can use it in the normal credential format.

*Call graph*: 1 external calls (__init__).
