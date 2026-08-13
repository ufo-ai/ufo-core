# Deployment Configuration and Extension Catalog State  `stage-24.1`

This stage is part of startup and setup. It decides what UFO is allowed to do before the server begins real work. Think of it as the system’s checklist and parts shelf: one part reads the local rules, and the other decides which optional add-ons are available.

The configuration file code in core/src/ufo/config.py defines what a valid UFO deployment setup looks like. It reads a single ufo.toml file, checks required settings, and rejects broken or incomplete values early. This helps the system fail fast with a clear problem instead of starting in a confused state.

The extension store in core/src/ufo/ext/store.py connects two things: a catalog of extensions that could be used, and a lockfile that records the exact extensions UFO should load. It supports command-line actions such as searching, installing by pinning an extension, and removing it. Together, these files make startup predictable: UFO knows its settings and exactly which extensions are selected.

## Files in this stage

### Startup Configuration and Extension State
Defines the deployment configuration and the extension catalog/lockfile state that determine UFO startup behavior.

### `core/src/ufo/config.py`

`config` · `config load and startup`

This file is the project's rulebook for configuration. A deployment needs many settings: where the database lives, where blobs such as transcripts are stored, which model to use, how sandboxes are reached, and which optional extension backends are selected. Instead of letting each part of the system guess for itself, this file gathers those settings into small Pydantic models. Pydantic is a validation library: it turns raw data into typed Python objects and rejects values that do not match the rules.

The main `Config` object is like a checklist for booting the system. Some sections are required, such as `database` and `blob`. Many others have safe defaults. Several sections also include extra checks. For example, a filesystem blob store must name a root folder, an S3 blob store must name a bucket, and a public sandbox ingress URL must be a clean HTTPS base address with no path or password in it.

The file also decides where the config file is found. By default it reads `ufo.toml`, but the `UFO_CONFIG` environment variable can point somewhere else. If the file is missing or a setting is wrong, loading stops loudly. That matters because configuration mistakes here could otherwise turn into confusing failures later, such as unreachable sandboxes, unusable cookies, or a database support store pointing at the wrong driver.

#### Function details

##### `DatabaseConfig._derive_system_url`  (lines 37–50)

```
def _derive_system_url(self) -> 'DatabaseConfig'
```

**Purpose**: This validator fills in the DBOS system database URL when the user did not provide one. DBOS is the system store used alongside the application database, so this gives it a predictable sibling location by default.

**Data flow**: It starts with the configured application database URL and checks whether `system_url` is already set. If it is set, nothing changes. If it is empty, the function splits the database URL, builds a second database name by adding `_dbos`, and also swaps async database drivers for the synchronous driver forms that the system store expects. The same `DatabaseConfig` object comes out, now with `system_url` filled in.

**Call relations**: This runs automatically while Pydantic is building a `DatabaseConfig` as part of loading the whole `Config`. Nothing in this file calls it directly; it is part of the validation step that happens when `load_config` turns TOML text into configuration objects.


##### `BlobConfig._backend_complete`  (lines 68–73)

```
def _backend_complete(self) -> 'BlobConfig'
```

**Purpose**: This validator checks that the selected blob storage backend has the information it needs. It prevents the system from starting with, for example, a filesystem store that has no folder or an S3 store that has no bucket.

**Data flow**: It receives a `BlobConfig` after the raw settings have been parsed. If the backend is `filesystem`, it requires `root` to be present. If the backend is `s3`, it requires `bucket` to be present. If the needed field is missing, it raises an error; otherwise it returns the unchanged configuration object.

**Call relations**: This is invoked automatically during configuration validation. It supports `load_config` indirectly by making sure the blob section is complete before any later code tries to save transcripts, artifacts, or other stored objects.


##### `ModelsConfig._auto_model_concrete`  (lines 86–89)

```
def _auto_model_concrete(self) -> 'ModelsConfig'
```

**Purpose**: This validator makes sure the deployment chooses a real model for agents that say they want `auto`. It stops `auto` from pointing back to itself, which would leave the system without a concrete model to call.

**Data flow**: It reads the `auto_model` setting from `ModelsConfig`. If the value is empty or is the special placeholder name `auto`, it raises an error. Otherwise it returns the same configuration object, confirming that `auto_model` is a specific model identifier.

**Call relations**: This runs as part of model configuration validation when `load_config` builds the overall `Config`. Later agent turns can rely on this setting already being resolved to a real model choice instead of doing their own safety check.


##### `SandboxConfig._ingress_base_is_addressable`  (lines 165–189)

```
def _ingress_base_is_addressable(self) -> 'SandboxConfig'
```

**Purpose**: This validator checks that the public sandbox ingress URL is usable for serving sandbox sites. It requires a clean HTTPS base URL because sandbox session cookies are marked secure and because the system creates per-site addresses by adding a subdomain in front of the host.

**Data flow**: It looks at `ingress_public_url`. If it is not set, it accepts that and returns the configuration unchanged. If it is set, it breaks the URL into pieces with `urlsplit`, then checks that the scheme is `https`, that a host exists, and that there is no path, query string, fragment, username, or password. A bad value raises a clear error; a good value returns the same `SandboxConfig` object.

**Call relations**: This is called automatically during sandbox configuration validation. It uses Python's URL-splitting helper to inspect the address. Its result protects later ingress code and link-building code, which both assume the configured value is only a secure scheme plus host, optionally with a port.

*Call graph*: 1 external calls (urlsplit).


##### `config_path`  (lines 301–302)

```
def config_path() -> Path
```

**Purpose**: This small helper decides which configuration file path to use. It lets operators override the default `ufo.toml` location with the `UFO_CONFIG` environment variable.

**Data flow**: It reads the process environment for `UFO_CONFIG`. If that variable exists, its value becomes the path. If it does not exist, the default path `ufo.toml` is used. The function returns a `Path` object, which is Python's filesystem path type.

**Call relations**: `load_config` calls this when the caller did not pass an explicit path. This keeps the path-selection rule in one place, so loading code does not need to know the environment variable name or the default filename.

*Call graph*: called by 1 (load_config); 1 external calls (Path).


##### `load_config`  (lines 305–311)

```
def load_config(path: Path | None=None) -> Config
```

**Purpose**: This is the main entry point for reading the deployment configuration. It finds the TOML file, reads it, parses it, and returns a validated `Config` object.

**Data flow**: It takes an optional path. If no path is given, it asks `config_path` where to look. It then checks that the file exists; if not, it raises a `FileNotFoundError` with a message telling the operator to create `ufo.toml` or set `UFO_CONFIG`. If the file exists, it reads the text, parses the TOML into ordinary data with `tomllib.loads`, and asks Pydantic to validate that data as a `Config`. The output is a ready-to-use configuration object, or a clear exception if something is wrong.

**Call relations**: This function ties the file's pieces together. It calls `config_path` for the default location and Python's TOML parser for the file format. During `Config.model_validate`, all the section validators in this file, such as the database, blob, model, and sandbox checks, run before the configuration is handed to the rest of the system.

*Call graph*: calls 1 internal fn (config_path); 1 external calls (loads).


### `core/src/ufo/ext/store.py`

`domain_logic` · `extension management commands`

This file solves a practical safety problem: UFO should only load extensions that are deliberately chosen and pinned. The catalog is like a shop shelf: it lists extensions that may be available. The lockfile is like a receipt or packing list: it records exactly which extensions UFO should use, including a digest, which is a fingerprint of the installed code.

The file defines simple data shapes for catalog entries, full catalogs, and search results. A catalog entry says the extension name, its version, and whether it is disabled. A disabled extension is not installable through the normal store command; it is meant only for bundling.

The main worker is ExtensionStore. It searches the catalog, checks which catalog entries are already pinned in the lockfile, installs an extension by adding or replacing its pin, and removes an extension by deleting its pin. Installing is intentionally strict: the extension must be in the catalog, must not be disabled, and must actually be installed in the current Python environment. That last check matters because the lockfile records a digest of real installed code; UFO cannot safely pin something it cannot inspect.

When writing the lockfile, the file preserves the existing UFO version anchor if one already exists. If this is the first lockfile, it records the currently installed UFO package version.

#### Function details

##### `read_catalog`  (lines 48–49)

```
def read_catalog(path: Path) -> Catalog
```

**Purpose**: Reads a catalog file from disk and turns it into a checked Catalog object. This is how the extension store gets its list of available extensions.

**Data flow**: It takes a file path, reads the text from that file, parses the text as TOML, and validates the result against the Catalog shape. The output is a Catalog whose entries can be searched or installed from.

**Call relations**: This is the doorway from a catalog file into the store logic. It relies on the file system to provide the text and on the TOML parser to turn that text into structured data before the rest of this file uses it.

*Call graph*: 2 external calls (read_text, loads).


##### `ufo_version`  (lines 52–53)

```
def ufo_version() -> str
```

**Purpose**: Returns the installed version of the UFO package. The store uses this when it needs to create a new lockfile and record which UFO version the lockfile belongs to.

**Data flow**: It asks Python package metadata for the version of the package named "ufo" and returns that version string. It does not change anything.

**Call relations**: ExtensionStore._write calls this only when there is no existing lockfile version to preserve. In that moment, this function supplies the version anchor for the new lockfile.

*Call graph*: called by 1 (_write); 1 external calls (version).


##### `pin_for`  (lines 56–63)

```
def pin_for(name: str) -> ExtensionPin
```

**Purpose**: Builds the lockfile pin for one installed extension. A pin records the extension name, the version from its manifest, and a digest, meaning a fingerprint of the installed extension code.

**Data flow**: It receives an extension name and looks for that name among extensions discovered in the current Python environment. If the name is missing, it raises an error instead of writing an unsafe lockfile entry. If found, it reads the extension manifest version, calculates a digest from the discovered entry, and returns an ExtensionPin.

**Call relations**: ExtensionStore.install calls this after confirming the catalog allows the extension. This function is the bridge from “the catalog says this extension exists” to “this exact installed code can be pinned safely.”

*Call graph*: called by 1 (install); 3 external calls (__init__, discovered, extension_digest).


##### `ExtensionStore.search`  (lines 73–84)

```
def search(self, query: str) -> tuple[StoreListing, ...]
```

**Purpose**: Searches the catalog for extension names containing the requested text and marks which results are already installed in the lockfile. It is what powers a user-facing “find extensions” view.

**Data flow**: It takes a query string, reads the current pins from the lockfile, and compares those pinned names with catalog entries. It returns StoreListing objects containing each matching extension’s name, version, disabled flag, and whether it is already pinned.

**Call relations**: This method calls ExtensionStore._pins to learn the current installed state, then builds search results from the catalog. It does not modify the lockfile; it only reports what is available and what is already selected.

*Call graph*: calls 1 internal fn (_pins); 1 external calls (__init__).


##### `ExtensionStore.install`  (lines 86–96)

```
def install(self, name: str) -> ExtensionPin
```

**Purpose**: Pins an extension into the lockfile so UFO will load it later. It refuses unknown catalog names, disabled bundle-only entries, and extensions that are not actually installed in the current environment.

**Data flow**: It receives an extension name, finds the matching catalog entry, and checks that the entry exists and is not disabled. It then asks pin_for to create a verified pin from the installed extension. Finally, it reads existing pins, replaces any old pin for the same name with the new one, writes the updated lockfile, and returns the new pin.

**Call relations**: This is the main install path. It uses pin_for to verify and fingerprint the installed extension, ExtensionStore._pins to keep other existing pins, and ExtensionStore._write to save the updated lockfile.

*Call graph*: calls 3 internal fn (_pins, _write, pin_for).


##### `ExtensionStore.remove`  (lines 98–102)

```
def remove(self, name: str) -> None
```

**Purpose**: Removes an extension pin from the lockfile so UFO will no longer load that extension. It reports an error if the extension was not pinned in the first place.

**Data flow**: It receives an extension name, reads the current pins, and checks whether any pin has that name. If none do, it raises an error. If one exists, it writes a new lockfile containing all the other pins except that extension.

**Call relations**: This is the uninstall path for extensions. It uses ExtensionStore._pins to inspect the current lockfile and ExtensionStore._write to save the lockfile after the chosen pin has been removed.

*Call graph*: calls 2 internal fn (_pins, _write).


##### `ExtensionStore._pins`  (lines 104–105)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: Reads the current list of pinned extensions from the lockfile. If there is no lockfile yet, it treats that as an empty list rather than an error.

**Data flow**: It looks at the store’s lockfile path. If the file exists, it reads and returns the lockfile’s extension pins. If the file does not exist, it returns an empty tuple.

**Call relations**: Search, install, and remove all call this helper before deciding what to show or change. It centralizes the rule that “no lockfile” means “no extensions are pinned yet.”

*Call graph*: called by 3 (install, remove, search); 1 external calls (read_lockfile).


##### `ExtensionStore._write`  (lines 107–111)

```
def _write(self, pins: tuple[ExtensionPin, ...]) -> None
```

**Purpose**: Writes a complete lockfile containing the given extension pins. It preserves the existing UFO version recorded in the lockfile, or uses the current installed UFO version when creating a new one.

**Data flow**: It receives the full tuple of pins that should be in the lockfile after the change. It checks whether a lockfile already exists: if so, it reads the existing UFO version from it; if not, it gets the current UFO package version. It then builds a Lockfile object and writes it to disk.

**Call relations**: Install and remove both call this after they have decided the new set of pins. This helper is the final step that turns an in-memory change into the lockfile that UFO’s loader will later read.

*Call graph*: calls 1 internal fn (ufo_version); called by 2 (install, remove); 3 external calls (__init__, read_lockfile, write_lockfile).
