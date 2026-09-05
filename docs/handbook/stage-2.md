# Process startup and service bootstrap  `stage-2`

This stage is the system’s “turn the key” moment. It happens before normal web requests, workers, or sandboxes start running. First, ufoctl in cli.py gives operators and developers a safe command-line front door for setup, running, inspection, packaging, and maintenance. Instead of editing internals by hand, they use this tool.

config.py reads the main ufo.toml deployment file and checks that required settings are present and shaped correctly, so mistakes are caught early. proxy_serve.py then prepares a few shared inputs that sandboxes and ingress need, such as which outside model-provider hosts are allowed and which database connection string to use.

serve.py is the main service launcher. It connects the web server, database job runner, sandboxes, extensions, connectors, storage, and background workers into one running service. ufo_ext_flagship.py plugs feature flags into Cloudflare Flagship, so behavior can be changed safely by configuration. product.py builds a product “census” from workspace activity, showing onboarding, tool connections, and payment funnel status for dashboards.

## Files in this stage

### Operator command entrypoint
Defines the CLI surface used to create, run, inspect, package, and maintain a UFO workspace.

### `core/src/ufo/cli.py`

`entrypoint` · `startup and operator commands`

This file is the project’s control panel. It uses Click, a Python library for building command-line commands, to expose practical actions such as `init`, `serve`, `portal`, `migrate`, `spend`, `credential`, `ext`, `turn`, and `seed`. A newcomer can think of it like the front desk of a building: it does not contain every room’s machinery, but it knows which door to open for each task.

At startup, the CLI loads a nearby `.env` file so local secrets are available. The `init` command creates a default config if needed, mints development secrets, prepares the database, onboards the first workspace owner, and writes a local CLI login token. `serve` starts the UFO service, while `portal` opens the browser and safely hands it the local token through a temporary one-use loopback web page.

The rest of the file gives operators safe shortcuts for common maintenance: applying database migrations, creating new migration files, setting spend caps, reading billing balances and spend reports, listing transcript-read disclosures, storing encrypted bring-your-own-key credentials, installing extensions, bundling a deploy artifact, cancelling stuck turns, printing a turn’s recorded steps, and seeding demo content. Most commands follow the same pattern: load config, open the right database scope, call the subsystem that owns the real work, print a human-readable result, and always close database connections afterward.

#### Function details

##### `_ufoctl_dir`  (lines 127–129)

```
def _ufoctl_dir() -> Path
```

**Purpose**: Chooses where this machine stores local `ufoctl` state, such as the CLI token. It uses an override environment variable when present, otherwise it falls back to a hidden folder in the user’s home directory.

**Data flow**: It reads the `UFOCTL_DIR` environment variable. If that variable is set, it turns that value into a path; if not, it builds `~/.ufoctl`. The path is returned without creating it.

**Call relations**: The `init` command calls this when saving a freshly minted CLI token. The `portal` command calls it later to find that saved token before opening the browser.

*Call graph*: called by 2 (init, portal); 2 external calls (Path, home).


##### `_dotenv_path`  (lines 132–133)

```
def _dotenv_path() -> Path
```

**Purpose**: Finds the `.env` file that belongs beside the project’s config file. This gives the CLI one consistent place to read and write local secrets.

**Data flow**: It asks the config system for the config file path, takes that file’s parent folder, and appends `.env`. It returns that path.

**Call relations**: Startup loading, secret creation, missing-key checks, and init status messages all use this helper so they agree on exactly which `.env` file is meant.

*Call graph*: called by 4 (_load_dotenv, _missing_deploy_keys, _write_dev_secrets, init); 1 external calls (config_path).


##### `_dotenv_pairs`  (lines 136–170)

```
def _dotenv_pairs(text: str) -> list[tuple[str, str]]
```

**Purpose**: Parses simple `.env` text into key-value pairs. It supports comments, blank lines, optional `export`, quoted values, and multi-line quoted secrets such as private keys.

**Data flow**: It receives raw text, reads it line by line, skips comments and blank lines, splits `KEY=VALUE`, removes matching quotes when needed, and returns a list of `(name, value)` pairs. If a quoted value never closes, it raises an error instead of silently guessing.

**Call relations**: The environment loader uses it to import secrets. The init helpers use it to avoid overwriting existing secrets and to decide which extension-required keys are still missing.

*Call graph*: called by 3 (_load_dotenv, _missing_deploy_keys, _write_dev_secrets).


##### `_load_dotenv`  (lines 173–189)

```
def _load_dotenv() -> None
```

**Purpose**: Loads local `.env` variables into the current process before any command runs. It refuses plain provider names like `OPENAI_API_KEY` because those could leak into unrelated tools that also read `.env`.

**Data flow**: It finds the `.env` path, exits quietly if the file does not exist, parses its key-value pairs, rejects reserved bare names, then writes each approved value into `os.environ`. On unsafe names, it raises a Click-friendly command error.

**Call relations**: The top-level `main` command group calls this first, so all subcommands see the same local environment and unsafe provider-key names are caught early.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (main); 1 external calls (ClickException).


##### `main`  (lines 193–195)

```
def main() -> None
```

**Purpose**: Defines the root `ufoctl` command. It is the entry point that all subcommands hang from.

**Data flow**: When invoked, it loads `.env` into the process. It does not return meaningful data; it prepares the environment for whichever subcommand Click dispatches next.

**Call relations**: Every command in this file is registered under this Click group, so this function is the doorway into the whole CLI.

*Call graph*: calls 1 internal fn (_load_dotenv).


##### `_one_address`  (lines 198–203)

```
def _one_address(_ctx: click.Context, _param: click.Parameter, value: str) -> str
```

**Purpose**: Validates that a command-line email option looks like one local address with a domain. It gives the user a clear error before database setup starts.

**Data flow**: It receives the raw command-line value, asks the seat/email helper whether it has a domain, and either returns the original string or raises a Click validation error.

**Call relations**: Click uses this as the callback for `init --email`, so bad owner addresses are rejected while parsing command options.

*Call graph*: 2 external calls (BadParameter, email_domain).


##### `init`  (lines 220–260)

```
def init(email: str, model: str, reasoning: ReasoningEffort, member_model_provider: str | None) -> None
```

**Purpose**: Bootstraps a usable UFO workspace on the current machine. It writes default config, prepares secrets and database schema, creates the first owner and agent, and stores a local CLI token.

**Data flow**: It reads command options, creates `ufo.toml` if missing, loads config, writes needed development secrets, optionally creates a Postgres database, applies migrations, runs onboarding, mints a long-lived local token, saves it with private file permissions, and prints next-step information. Errors from setup are turned into readable CLI failures.

**Call relations**: This is usually the first command a developer runs. It coordinates helpers for secrets, database creation, onboarding, token storage, and missing extension key reporting.

*Call graph*: calls 6 internal fn (_create_postgres_system_database, _dotenv_path, _missing_deploy_keys, _onboard, _ufoctl_dir, _write_dev_secrets); 7 external calls (run, ClickException, echo, config_path, load_config, apply_migrations, mint_token).


##### `_missing_deploy_keys`  (lines 263–279)

```
def _missing_deploy_keys(config: Config) -> tuple[str, ...]
```

**Purpose**: Finds extension-required deployment keys that are not currently available. It warns users about missing secrets without blocking local startup.

**Data flow**: It loads active extension manifests, gathers the key names they declare, reads non-empty names from `.env` and the process environment, then returns the missing ones in their `UFO_`-prefixed form.

**Call relations**: `init` calls this after setup so the user learns which optional features still need real provider keys before they are used.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 1 external calls (load_manifests).


##### `_write_dev_secrets`  (lines 282–304)

```
def _write_dev_secrets(config: Config) -> tuple[str, ...]
```

**Purpose**: Creates local development secrets that UFO needs to run with no manual setup. It avoids overwriting anything already present.

**Data flow**: It generates an encryption key and token-signing secrets, reads existing `.env` entries and environment variables, writes only missing names to `.env`, also adds them to the current process, and returns the names it newly wrote.

**Call relations**: `init` calls this before onboarding so token minting, artifact signing, and credential encryption have the secrets they need.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 2 external calls (generate_key, token_urlsafe).


##### `_onboard`  (lines 307–361)

```
async def _onboard(config: Config, email: str, model: str, reasoning: ReasoningEffort, member_model_provider: str | None) -> Onboarded
```

**Purpose**: Creates the initial workspace, owner member, default agent, and extension onboarding data. It can also store an initial member model API key securely.

**Data flow**: It opens the database, builds an optional encrypted credential store, creates an onboarding object from config and extension manifests, optionally reads a provider key from the environment, creates the core records, stores the member-specific key if requested, runs extension onboarding steps, returns the onboarding result, and always closes database resources.

**Call relations**: `init` calls this after schema setup. It hands most creation work to the onboarding subsystem and uses the credential store only when a provider key should be saved.

*Call graph*: called by 1 (init); 9 external calls (__init__, __init__, Fernet, dispose_db, init_db, load_manifests, deploy_env, member_slot, items).


##### `_create_postgres_system_database`  (lines 364–375)

```
async def _create_postgres_system_database(config: Config) -> None
```

**Purpose**: Ensures the separate system database exists when using Postgres. This saves a local operator from creating that database by hand.

**Data flow**: It derives a plain Postgres connection string, connects to the application database, checks whether the configured system database name exists, creates it if missing, and closes the connection.

**Call relations**: `init` calls this only for Postgres-style database URLs, before migrations and onboarding need the system database.

*Call graph*: called by 1 (init); 1 external calls (connect).


##### `migrate`  (lines 379–396)

```
def migrate() -> None
```

**Purpose**: Applies database migrations so the schema matches the current code and active extensions. This is what keeps tables and columns up to date after upgrades or extension installs.

**Data flow**: It loads config, chooses an owner database URL from the environment when provided or the normal config URL otherwise, runs migrations, and prints confirmation.

**Call relations**: Operators run this directly, and `init` runs migration separately during first setup. It delegates actual schema changes to the database migration layer.

*Call graph*: 3 external calls (echo, load_config, apply_migrations).


##### `_one_slug`  (lines 399–402)

```
def _one_slug(_ctx: click.Context, _param: click.Parameter, value: str) -> str
```

**Purpose**: Validates the short name used for a new migration file. It enforces lowercase snake_case names so migration filenames stay predictable.

**Data flow**: It receives a command-line slug, checks it against the allowed pattern, returns it if valid, or raises a Click validation error if not.

**Call relations**: Click uses this when parsing `new-migration`, preventing invalid filenames before the file-writing logic runs.

*Call graph*: 1 external calls (BadParameter).


##### `new_migration`  (lines 407–424)

```
def new_migration(slug: str) -> None
```

**Purpose**: Creates a new empty core database migration file. It gives developers a safe starting point for schema changes.

**Data flow**: It reads the current migration head, creates a timestamp revision, writes a Python migration template under the core versions directory, prints the new revision information, and updates the `HEAD` file to force merge conflicts if two branches create competing heads.

**Call relations**: Developers call this while changing the schema. It relies on the migration head helper and contained-file writing to keep the new file inside the expected directory.

*Call graph*: 4 external calls (echo, now, core_migration_head, contained_file).


##### `serve`  (lines 428–437)

```
def serve() -> None
```

**Purpose**: Starts the UFO server process for the current config. It also tells the user where the browser portal will be if the active pack provides one.

**Data flow**: It loads config, loads active extension manifests, finds a home browser surface, prints the portal URL when available, then calls the main server runner. It does not return until the server runner exits.

**Call relations**: This command is the normal local runtime entry. It uses `_serve_base` for the public URL and then hands control to `ufo.serve.run`.

*Call graph*: calls 1 internal fn (_serve_base); 5 external calls (echo, load_config, load_manifests, home_surface, run).


##### `portal`  (lines 441–459)

```
def portal() -> None
```

**Purpose**: Opens the UFO browser portal and signs it in with this machine’s saved CLI token. It avoids making users copy and paste tokens.

**Data flow**: It loads config and manifests, finds the portal surface, reads the saved token, checks that the server answers, starts a temporary browser handoff, and prints the opened URL. Missing portal, missing token, or unreachable server become clear CLI errors.

**Call relations**: Users run this after `init` and `serve`. It depends on `_ufoctl_dir` for the token path, `_serve_base` for the portal host, and `BrowserHandoff` for safe browser sign-in.

*Call graph*: calls 2 internal fn (_serve_base, _ufoctl_dir); 7 external calls (__init__, ClickException, echo, get, load_config, load_manifests, home_surface).


##### `_serve_base`  (lines 462–469)

```
def _serve_base(config: Config) -> str
```

**Purpose**: Chooses the base URL users and browser sessions should use to reach the server. It prefers the configured public URL because cookies and absolute links depend on the host name matching.

**Data flow**: It reads config. If `connect.public_base_url` is set, it returns that; otherwise it builds a local URL from the server host and port.

**Call relations**: `serve` uses it when printing the portal location. `portal` uses it when checking server reachability and building the target portal URL.

*Call graph*: called by 2 (portal, serve).


##### `BrowserHandoff.open`  (lines 484–492)

```
def open(self) -> None
```

**Purpose**: Temporarily serves a one-use local web page that transfers the CLI token into the browser by form post. This keeps the token out of the URL and closes the listener after delivery.

**Data flow**: It creates a random path, starts a local HTTP server on `127.0.0.1` with an available port, opens the browser to that private URL, and serves requests until the expected page has been delivered. If the browser cannot open automatically, it prints the URL to visit.

**Call relations**: `portal` calls this after it has found the portal URL and token. It builds its request handler through `BrowserHandoff._responder`.

*Call graph*: calls 1 internal fn (_responder); 5 external calls (echo, HTTPServer, token_urlsafe, Event, open).


##### `BrowserHandoff._responder`  (lines 494–511)

```
def _responder(self, path: str, delivered: threading.Event) -> type[BaseHTTPRequestHandler]
```

**Purpose**: Builds the tiny HTTP request handler used by the browser handoff server. The handler only serves the secret handoff path.

**Data flow**: It creates the HTML page once, closes over the expected path and delivery event, and returns a `BaseHTTPRequestHandler` subclass that can serve that page.

**Call relations**: `BrowserHandoff.open` passes this generated handler class to the local HTTP server. The generated handler uses `BrowserHandoff._page` for the actual HTML.

*Call graph*: calls 1 internal fn (_page); called by 1 (open).


##### `BrowserHandoff._responder.do_GET`  (lines 498–507)

```
def do_GET(self) -> None
```

**Purpose**: Responds to the browser’s one expected GET request during token handoff. It serves the sign-in form only on the random path.

**Data flow**: It checks the incoming request path. A wrong path receives 404; the right path receives the prepared HTML page, then marks the handoff as delivered so the local server can stop.

**Call relations**: The temporary HTTP server calls this when the browser loads the handoff URL created by `BrowserHandoff.open`.


##### `BrowserHandoff._responder.log_message`  (lines 509–509)

```
def log_message(self, *args: object) -> None
```

**Purpose**: Suppresses the default HTTP server access logs for the handoff server. This keeps the one-time local sign-in flow quiet.

**Data flow**: It receives log arguments from the base HTTP handler and intentionally does nothing. Nothing is returned or changed.

**Call relations**: The generated responder class uses this override whenever the standard library server would normally print a request log line.


##### `BrowserHandoff._page`  (lines 513–520)

```
def _page(self) -> str
```

**Purpose**: Creates the HTML page that posts the saved CLI token to the portal. It includes a button as a fallback and JavaScript to submit automatically.

**Data flow**: It reads the handoff object’s portal URL and token, escapes both for safe HTML, and returns a complete small HTML document containing a POST form.

**Call relations**: `BrowserHandoff._responder` calls this while preparing the page that `BrowserHandoff.open` will serve to the browser.

*Call graph*: called by 1 (_responder); 1 external calls (escape).


##### `ingress`  (lines 524–526)

```
def ingress() -> None
```

**Purpose**: Starts the sandbox ingress service, a token-protected reverse proxy into sandbox ports. This lets approved traffic reach sandboxed conversation tools.

**Data flow**: It takes no command options and simply calls the sandbox ingress runner. The runner owns the long-running network work.

**Call relations**: This is a direct CLI doorway into the sandbox ingress subsystem.

*Call graph*: 1 external calls (run).


##### `spend_cap`  (lines 530–531)

```
def spend_cap() -> None
```

**Purpose**: Defines the `spend-cap` command group for reading and changing spending limits. Spend caps are guardrails that can stop or park work when cost limits are reached.

**Data flow**: It performs no work itself; Click uses it as a parent for subcommands. The actual read and write behavior lives in `spend_cap_set` and `spend_cap_list`.

**Call relations**: Click dispatches subcommands under this group when the user runs `ufoctl spend-cap ...`.


##### `spend_cap_set`  (lines 542–560)

```
def spend_cap_set(scope: str, subject_id: str, window_seconds: int, limit_micro_usd: int, on_breach: str) -> None
```

**Purpose**: Creates or updates a spending limit for a workspace, member, or agent. It prevents confusing combinations such as a workspace cap with a subject ID.

**Data flow**: It validates the scope and subject arguments, converts the subject ID to a UUID when present, loads config, calls the async database writer, converts micro-dollars to dollars for display, and prints the cap ID and rule.

**Call relations**: Users call this under `spend-cap set`. It delegates the actual insert-or-update work to `_write_spend_cap`.

*Call graph*: calls 1 internal fn (_write_spend_cap); 5 external calls (run, ClickException, echo, load_config, UUID).


##### `spend_cap_list`  (lines 564–574)

```
def spend_cap_list() -> None
```

**Purpose**: Prints the configured spend caps for the current workspace. It gives operators a quick view of active cost guardrails.

**Data flow**: It loads config, reads cap rows asynchronously, prints `no spend caps set` if empty, otherwise formats each cap with scope, optional subject, dollar limit, window, and breach behavior.

**Call relations**: Users call this under `spend-cap list`. It relies on `_read_spend_caps` to fetch the database rows.

*Call graph*: calls 1 internal fn (_read_spend_caps); 3 external calls (run, echo, load_config).


##### `_write_spend_cap`  (lines 577–631)

```
async def _write_spend_cap(config: Config, scope: str, subject: UUID | None, window_seconds: int, limit_micro_usd: int, on_breach: str) -> UUID
```

**Purpose**: Writes a spend cap record, updating an existing matching cap when one already exists. This keeps repeated commands from creating duplicate rules.

**Data flow**: It opens the database, finds the workspace, looks for an existing cap with the same workspace, scope, subject, and time window, updates its limit and breach action if found, or inserts a new UUID-backed row if not. It returns the cap ID and closes the database.

**Call relations**: `spend_cap_set` calls this after validating user input. It does the direct database work inside a workspace transaction.

*Call graph*: called by 1 (spend_cap_set); 7 external calls (insert, select, update, dispose_db, init_db, workspace_tx, uuid4).


##### `_read_spend_caps`  (lines 634–660)

```
async def _read_spend_caps(config: Config) -> list[tuple[UUID, str, UUID | None, int, int, str]]
```

**Purpose**: Reads all spend caps for the current workspace. It returns simple tuples that the CLI can print.

**Data flow**: It opens the database, finds the workspace ID, selects cap fields ordered by scope, converts database rows into plain tuples, returns them, and closes the database.

**Call relations**: `spend_cap_list` calls this to get the data it displays.

*Call graph*: called by 1 (spend_cap_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `balance`  (lines 664–665)

```
def balance() -> None
```

**Purpose**: Defines the `balance` command group for prepaid balance operations. These commands let operators inspect, credit, and set reserve requirements.

**Data flow**: It does not read or write data itself. Click uses it as the parent for `show`, `credit`, and `reserve`.

**Call relations**: Click dispatches to the balance subcommands when a user runs `ufoctl balance ...`.


##### `balance_show`  (lines 670–685)

```
def balance_show(workspace_id: str) -> None
```

**Purpose**: Shows the workspace’s current prepaid balance, reserve, granted total, charged total, and last purchase time. This tells an operator whether work has enough funds to start.

**Data flow**: It loads config, reads the balance for an optional workspace ID, prints `no balance` if absent, or formats the balance values from micro-dollars into dollars.

**Call relations**: This is the user-facing `balance show` command. It calls `_read_balance`, which opens the correct workspace scope.

*Call graph*: calls 1 internal fn (_read_balance); 3 external calls (run, echo, load_config).


##### `balance_credit`  (lines 693–706)

```
def balance_credit(granted_micro_usd: int, charged_micro_usd: int, reference: str, workspace_id: str) -> None
```

**Purpose**: Adds or reverses credit using an idempotency reference, meaning the same reference is applied only once. This prevents accidental double-crediting.

**Data flow**: It checks the granted amount is not zero, loads config, calls the credit helper with granted amount, charged amount, reference, and workspace ID, then prints whether a new credit was applied or the reference was already used.

**Call relations**: This is the user-facing `balance credit` command. It delegates money-record writing to `_credit_balance`.

*Call graph*: calls 1 internal fn (_credit_balance); 4 external calls (run, ClickException, echo, load_config).


##### `balance_reserve`  (lines 712–720)

```
def balance_reserve(micro_usd: int, workspace_id: str) -> None
```

**Purpose**: Sets the reserve amount required before a turn may begin. This acts like minimum account headroom.

**Data flow**: It rejects negative reserves, loads config, calls the reserve helper, prints the new reserve if a balance exists, or raises an error if there is no balance to update.

**Call relations**: This is the user-facing `balance reserve` command. It relies on `_set_reserve` for the database change.

*Call graph*: calls 1 internal fn (_set_reserve); 4 external calls (run, ClickException, echo, load_config).


##### `_target_workspace`  (lines 723–745)

```
async def _target_workspace(named: str) -> UUID
```

**Purpose**: Decides which workspace an operator command should affect. If no workspace is named, it safely uses the only workspace, but refuses to guess in a multi-workspace deploy.

**Data flow**: Inside an owner-level database transaction, it either validates the named UUID exists or lists all workspaces. It returns the chosen workspace ID or raises a clear error for none, missing, or ambiguous choices.

**Call relations**: `_balance_scope` and `_seed_target` call this before doing workspace-specific work. It is the shared safety check that prevents commands from touching the wrong tenant.

*Call graph*: called by 2 (_balance_scope, _seed_target); 4 external calls (ClickException, select, owner_tx, UUID).


##### `_balance_scope`  (lines 749–766)

```
async def _balance_scope(config: Config, named: str) -> AsyncIterator[tuple[AsyncConnection, UUID]]
```

**Purpose**: Opens the database context needed by balance commands for exactly one workspace. It handles both local single-workspace setups and hosted multi-workspace deployments.

**Data flow**: It initializes the app database, optionally initializes the owner database, resolves the target workspace, pins that workspace in context, opens a workspace transaction, yields the connection and workspace ID to the caller, and disposes database resources afterward.

**Call relations**: `_read_balance`, `_credit_balance`, and `_set_reserve` all use this so they share the same workspace resolution and cleanup behavior.

*Call graph*: calls 1 internal fn (_target_workspace); called by 3 (_credit_balance, _read_balance, _set_reserve); 5 external calls (dispose_db, init_db, init_owner_db, workspace_tx, ws).


##### `_read_balance`  (lines 769–771)

```
async def _read_balance(config: Config, named: str) -> Balance | None
```

**Purpose**: Reads the prepaid balance for a selected workspace. It is a small bridge between the CLI and the billing balance subsystem.

**Data flow**: It opens `_balance_scope`, passes the connection and workspace ID to `read_balance`, and returns either a balance object or `None`.

**Call relations**: `balance_show` calls this when it needs data to print.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_show); 1 external calls (read_balance).


##### `_credit_balance`  (lines 774–780)

```
async def _credit_balance(config: Config, named: str, granted_micro_usd: int, charged_micro_usd: int, reference: str) -> bool
```

**Purpose**: Applies a balance credit or adjustment for a selected workspace. It returns whether the reference was new.

**Data flow**: It opens `_balance_scope`, passes the amounts and reference to the billing `credit` function, and returns that function’s boolean result.

**Call relations**: `balance_credit` calls this after validating command-line input.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_credit); 1 external calls (credit).


##### `_set_reserve`  (lines 783–785)

```
async def _set_reserve(config: Config, named: str, reserve_micro_usd: int) -> bool
```

**Purpose**: Updates the reserve amount for a selected workspace balance. The reserve is the required spending headroom before turns can start.

**Data flow**: It opens `_balance_scope`, passes the new reserve amount to `set_reserve`, and returns whether an existing balance was updated.

**Call relations**: `balance_reserve` calls this and turns the boolean result into either a success message or a user-facing error.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_reserve); 1 external calls (set_reserve).


##### `flags`  (lines 792–797)

```
def flags() -> None
```

**Purpose**: Defines the `flags` command group for changing feature flag values without deploying new code. Feature flags let operators turn behavior on or off at runtime.

**Data flow**: It does not process data directly. Click uses it as the parent for flag subcommands.

**Call relations**: Click dispatches to `flags_set` when the user runs `ufoctl flags set ...`.


##### `flags_set`  (lines 803–825)

```
def flags_set(key: str, on: bool) -> None
```

**Purpose**: Sets one feature flag to on or off through the configured flag backend. It refuses unknown flags so dashboards do not show settings that no active code reads.

**Data flow**: It loads config, checks a flag backend is configured, loads active manifests, verifies the requested key is declared, imports the backend admin module, asks it to serve the new value, and prints the result. Backend import or runtime errors become Click errors.

**Call relations**: This command connects the CLI to whichever feature-flag service is selected in config. It discovers the backend module by name and calls its admin API.

*Call graph*: 5 external calls (ClickException, echo, import_module, load_config, load_manifests).


##### `spend`  (lines 833–856)

```
def spend(window_seconds: int) -> None
```

**Purpose**: Prints a spending report for a recent time window. It breaks cost down by dimension, member, agent, origin, and pricing version.

**Data flow**: It loads config, reads a spend report for the requested window, converts micro-dollars into dollars, and prints the total plus each breakdown section.

**Call relations**: Users call this directly for cost inspection. It delegates report calculation to `_read_spend` and the billing rollup subsystem.

*Call graph*: calls 1 internal fn (_read_spend); 3 external calls (run, echo, load_config).


##### `_read_spend`  (lines 859–866)

```
async def _read_spend(config: Config, window_seconds: int) -> SpendReport
```

**Purpose**: Reads the spend rollup for the current workspace and time window. It keeps the CLI away from the details of ledger aggregation.

**Data flow**: It opens the database, finds the workspace ID, constructs a `SpendRollup`, asks it to read the report, returns that report, and disposes the database.

**Call relations**: `spend` calls this before printing the report.

*Call graph*: called by 1 (spend); 5 external calls (__init__, select, dispose_db, init_db, workspace_tx).


##### `transcript_reads`  (lines 874–887)

```
def transcript_reads(limit: int) -> None
```

**Purpose**: Lists recorded administrative disclosures for reading another member’s private transcript. This gives operators an audit view of sensitive access.

**Data flow**: It validates the limit is positive, loads config, reads access records, prints a no-records message if empty, or prints each reader, subject, conversation, and time.

**Call relations**: The command calls `_read_transcript_accesses` for database access and then formats the audit rows for humans.

*Call graph*: calls 1 internal fn (_read_transcript_accesses); 4 external calls (run, ClickException, echo, load_config).


##### `_read_transcript_accesses`  (lines 890–924)

```
async def _read_transcript_accesses(config: Config, limit: int) -> list[tuple[str, str, UUID, datetime]]
```

**Purpose**: Fetches the newest transcript access disclosure records for the current workspace. It joins member records so the CLI can show email addresses instead of only IDs.

**Data flow**: It opens the database, aliases the member table as reader and subject, selects disclosure rows for the workspace ordered newest first, limits the result count, converts rows into tuples, and closes the database.

**Call relations**: `transcript_reads` calls this to get the audit data it prints.

*Call graph*: called by 1 (transcript_reads); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `grants`  (lines 928–940)

```
def grants() -> None
```

**Purpose**: Lists OAuth account grants available to agents. OAuth is the common web sign-in permission system used to let an app access an account without seeing the password.

**Data flow**: It loads config, reads grant summaries, prints `no grants` if empty, or prints each agent, provider, account ID, shared/private scope, and grant date.

**Call relations**: This command calls `_read_grants`, which asks the access-grants subsystem for summarized data.

*Call graph*: calls 1 internal fn (_read_grants); 3 external calls (run, echo, load_config).


##### `_read_grants`  (lines 943–950)

```
async def _read_grants(config: Config) -> tuple[GrantSummary, ...]
```

**Purpose**: Reads OAuth grant summaries for the current workspace. It first discovers the workspace ID from the database.

**Data flow**: It opens the database, selects the workspace ID, calls `workspace_grant_summaries`, returns the tuple of summaries, and closes database resources.

**Call relations**: `grants` calls this and then handles all display formatting.

*Call graph*: called by 1 (grants); 5 external calls (select, dispose_db, init_db, workspace_tx, workspace_grant_summaries).


##### `credential`  (lines 954–956)

```
def credential() -> None
```

**Purpose**: Defines the `credential` command group for encrypted bring-your-own-key secrets declared by extensions. These commands let users fill and inspect secret slots without printing secret values.

**Data flow**: It does no work itself. Click uses it as a parent for `credential set` and `credential list`.

**Call relations**: Click dispatches to credential subcommands when the user runs `ufoctl credential ...`.


##### `credential_set`  (lines 961–979)

```
def credential_set(slot: str) -> None
```

**Purpose**: Stores one declared credential secret securely. It reads the value from a hidden prompt or standard input, never from command-line arguments.

**Data flow**: It loads config, checks the slot is declared by an active extension, checks the encryption key exists in the environment, reads and trims the secret value, rejects empty input, writes the encrypted credential, and prints which extension owns the slot.

**Call relations**: This command uses `_declared_slots` for validation and `_write_credential` for encrypted storage.

*Call graph*: calls 2 internal fn (_declared_slots, _write_credential); 5 external calls (run, ClickException, echo, prompt, load_config).


##### `credential_list`  (lines 983–993)

```
def credential_list() -> None
```

**Purpose**: Shows which declared credential slots are set or unset without revealing their values. This helps operators confirm setup safely.

**Data flow**: It loads config, gathers declared slots, prints a no-slots message if none exist, reads stored slot names, and prints each slot with its owning extension and status.

**Call relations**: This command combines `_declared_slots` with `_read_stored_slots` to compare what extensions ask for against what the workspace has stored.

*Call graph*: calls 2 internal fn (_declared_slots, _read_stored_slots); 3 external calls (run, echo, load_config).


##### `_declared_slots`  (lines 996–1001)

```
def _declared_slots(config: Config) -> dict[str, str]
```

**Purpose**: Collects credential slots declared by active extensions. It maps each slot name to the extension that owns it.

**Data flow**: It loads manifests for the configured pack, converts manifest credential declarations into a dictionary, and turns manifest loading errors into Click errors.

**Call relations**: Both credential subcommands call this so they only operate on slots the installed extensions actually declare.

*Call graph*: called by 2 (credential_list, credential_set); 2 external calls (ClickException, load_manifests).


##### `_write_credential`  (lines 1004–1011)

```
async def _write_credential(config: Config, key: str, slot: str, value: str) -> None
```

**Purpose**: Encrypts and stores a credential value for the current workspace. This keeps secret values out of plain database storage.

**Data flow**: It opens the database, finds the workspace ID, builds a Fernet encryption helper from the provided key, stores the slot value through `CredentialStore`, and closes the database.

**Call relations**: `credential_set` calls this after reading and validating the secret value.

*Call graph*: called by 1 (credential_set); 6 external calls (__init__, Fernet, select, dispose_db, init_db, workspace_tx).


##### `_read_stored_slots`  (lines 1014–1028)

```
async def _read_stored_slots(config: Config) -> frozenset[str]
```

**Purpose**: Reads the names of credential slots that already have stored values. It never reads or returns the secret contents.

**Data flow**: It opens the database, finds the workspace ID, selects credential slot names for that workspace, returns them as a frozen set, and closes the database.

**Call relations**: `credential_list` calls this to mark declared slots as set or unset.

*Call graph*: called by 1 (credential_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `ext`  (lines 1032–1033)

```
def ext() -> None
```

**Purpose**: Defines the `ext` command group for extension store operations. Extensions add capabilities, and this group lets operators search, install, or remove them.

**Data flow**: It performs no direct work. Click uses it as a parent for extension subcommands.

**Call relations**: Click dispatches to `ext_search`, `ext_install`, or `ext_remove` under this group.


##### `_store`  (lines 1036–1039)

```
def _store(config: Config) -> ExtensionStore
```

**Purpose**: Builds an extension store object from config. It refuses extension commands when no store is configured.

**Data flow**: It checks `config.ext.store`, reads the catalog, finds the lockfile path, creates an `ExtensionStore`, and returns it. If no store is configured, it raises a Click error.

**Call relations**: All extension subcommands call this before searching or changing installed extension pins.

*Call graph*: called by 3 (ext_install, ext_remove, ext_search); 4 external calls (__init__, ClickException, lockfile_path, read_catalog).


##### `ext_search`  (lines 1044–1057)

```
def ext_search(query: str) -> None
```

**Purpose**: Searches the configured extension catalog and shows whether matching extensions are installed, available, or bundle-only. This helps users discover optional features.

**Data flow**: It loads config, builds the store, searches using the query string, prints a no-match message if needed, or prints each listing with name, version, and state.

**Call relations**: This command relies on `_store` for catalog and lockfile access, then only formats the store’s search results.

*Call graph*: calls 1 internal fn (_store); 2 external calls (echo, load_config).


##### `ext_install`  (lines 1062–1068)

```
def ext_install(name: str) -> None
```

**Purpose**: Pins an extension from the store into the deploy lockfile. The next server run will load that extension.

**Data flow**: It loads config, builds the store, asks it to install the named extension, converts store errors into Click errors, and prints the installed name, version, and digest.

**Call relations**: This command uses `_store` to reach the extension store and delegates pinning rules to that subsystem.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `ext_remove`  (lines 1073–1079)

```
def ext_remove(name: str) -> None
```

**Purpose**: Removes an extension pin from the lockfile. The next server run will stop loading it.

**Data flow**: It loads config, builds the store, asks it to remove the named extension, converts store errors into Click errors, and prints confirmation.

**Call relations**: This command uses `_store` for lockfile access and lets the extension store subsystem perform the removal.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `_ufo_project_dir`  (lines 1085–1096)

```
def _ufo_project_dir() -> Path
```

**Purpose**: Finds the source checkout that contains the `ufo` Python project. This lets `ufoctl bundle` build the correct wheel from any current working directory.

**Data flow**: It walks upward from this file, looks for `pyproject.toml`, parses it, and returns the first ancestor whose project name is `ufo`. If none is found, it raises a Click error.

**Call relations**: `bundle` calls this before invoking the build tool. A wheel-only install will fail here because it lacks the source project needed for bundling.

*Call graph*: called by 1 (bundle); 3 external calls (ClickException, Path, loads).


##### `bundle`  (lines 1108–1138)

```
def bundle(out: Path, client_binary: Path) -> None
```

**Purpose**: Builds a runnable deployment bundle containing the UFO wheel, client binary, config, and extension lock information. This freezes a deploy into an artifact.

**Data flow**: It loads config, optionally reads the extension catalog, runs `uv build` to create a wheel, verifies the expected wheel exists, constructs a `Bundle`, builds it, and prints the output path plus pinned extensions.

**Call relations**: This command coordinates external building through `subprocess`, source lookup through `_ufo_project_dir`, and final artifact assembly through the bundle subsystem.

*Call graph*: calls 1 internal fn (_ufo_project_dir); 8 external calls (__init__, ClickException, echo, run, wheel_name, config_path, load_config, read_catalog).


##### `turn`  (lines 1142–1143)

```
def turn() -> None
```

**Purpose**: Defines the `turn` command group for operations on a single agent turn. A turn is one unit of conversation work.

**Data flow**: It does no direct work. Click uses it as a parent for turn subcommands.

**Call relations**: Click dispatches to `turn_cancel` or `turn_steps` when the user runs `ufoctl turn ...`.


##### `turn_cancel`  (lines 1149–1159)

```
def turn_cancel(turn_id: str, workspace_id: str) -> None
```

**Purpose**: Cancels one turn that is stuck or no longer wanted. It gives operators a way to end work that normal member controls cannot finish.

**Data flow**: It loads config, converts the turn ID to a UUID, calls the async cancellation helper with an optional workspace ID, and prints whether the turn was cancelled or already terminal.

**Call relations**: This is the user-facing command. It delegates workspace lookup, durable workflow cancellation, and database cleanup to `_cancel_turn`.

*Call graph*: calls 1 internal fn (_cancel_turn); 4 external calls (run, echo, load_config, UUID).


##### `_cancel_turn`  (lines 1162–1196)

```
async def _cancel_turn(config: Config, turn_id: UUID, named_workspace: str) -> bool
```

**Purpose**: Cancels a turn in its correct workspace, resolving that workspace when the user did not name it. It checks existence carefully so hosted deployments do not cancel the wrong thing.

**Data flow**: It initializes the database, resolves the workspace either from the provided ID or by reading the owner database, creates a replay-safe durable client, pins the workspace context, optionally verifies the turn exists there, calls `cancel_one_turn`, returns whether cancellation happened, and disposes database resources.

**Call relations**: `turn_cancel` calls this. It bridges the CLI, owner/workspace database scopes, the durable workflow client, and the turn cancellation subsystem.

*Call graph*: called by 1 (turn_cancel); 11 external calls (ClickException, select, dispose_db, init_db, init_owner_db, owner_tx, workspace_tx, replay_safe_client, cancel_one_turn, ws (+1 more)).


##### `turn_steps`  (lines 1202–1210)

```
def turn_steps(turn_id: str, workspace_id: str) -> None
```

**Purpose**: Prints the recorded durable step log for one turn. This helps operators see what a turn actually did, even if the final conversation transcript is compacted or missing.

**Data flow**: It loads config, converts the turn ID to a UUID, and calls the async printer with an optional workspace ID. It does not format steps itself.

**Call relations**: This is the user-facing command. It delegates lookup and step reading to `_print_turn_steps`.

*Call graph*: calls 1 internal fn (_print_turn_steps); 3 external calls (run, load_config, UUID).


##### `_print_turn_steps`  (lines 1213–1245)

```
async def _print_turn_steps(config: Config, turn_id: UUID, named_workspace: str) -> None
```

**Purpose**: Finds a turn’s workspace and reads its durable execution steps. Durable here means the steps are recorded so they survive crashes or retries.

**Data flow**: It initializes databases, resolves the workspace from an argument or owner lookup, creates a replay-safe client, pins workspace context, reads the turn’s running attempt ID, reads durable steps using that attempt or the turn ID, prints them, and disposes resources.

**Call relations**: `turn_steps` calls this. It hands the final display work to `_echo_turn_steps` after fetching data from `DurableTurnSteps`.

*Call graph*: calls 1 internal fn (_echo_turn_steps); called by 1 (turn_steps); 11 external calls (__init__, ClickException, select, dispose_db, init_db, init_owner_db, owner_tx, workspace_tx, replay_safe_client, ws (+1 more)).


##### `_echo_turn_steps`  (lines 1248–1261)

```
def _echo_turn_steps(steps: tuple[TurnStep, ...]) -> None
```

**Purpose**: Formats and prints a turn’s recorded steps and messages. It makes low-level step records readable in the terminal.

**Data flow**: It receives a tuple of step objects, prints `no recorded steps` if empty, otherwise prints each step number, kind, name, function, optional duration, and then each message. Structured message blocks are converted by `_echo_block`.

**Call relations**: `_print_turn_steps` calls this after reading steps from durable storage.

*Call graph*: calls 1 internal fn (_echo_block); called by 1 (_print_turn_steps); 1 external calls (echo).


##### `_echo_block`  (lines 1264–1274)

```
def _echo_block(block: object) -> str
```

**Purpose**: Turns one structured message block into a short terminal string. It understands text, tool calls, and tool results.

**Data flow**: It receives a block object, pattern-matches its type, returns plain text for text blocks, JSON-formatted arguments for tool calls, result text for tool results, or the type name for unknown blocks.

**Call relations**: `_echo_turn_steps` calls this for non-plain message content while printing turn step details.

*Call graph*: called by 1 (_echo_turn_steps); 1 external calls (dumps).


##### `seed`  (lines 1278–1279)

```
def seed() -> None
```

**Purpose**: Defines the `seed` command group for writing demonstration content. Seed data helps designers and developers see known UI cases.

**Data flow**: It does no direct work. Click uses it as a parent for seed subcommands.

**Call relations**: Click dispatches to `seed_kitchen_sink` under this group.


##### `seed_kitchen_sink`  (lines 1284–1293)

```
def seed_kitchen_sink(workspace_id: str) -> None
```

**Purpose**: Writes a demonstration conversation containing many shapes the portal UI must render. It prints the browser route where the seeded conversation can be viewed.

**Data flow**: It loads config, calls the async seed helper with an optional workspace ID, receives the conversation ID, and prints a portal path for it.

**Call relations**: This is the user-facing seed command. It delegates database, workspace, blob, and content-writing work to `_seed_kitchen_sink`.

*Call graph*: calls 1 internal fn (_seed_kitchen_sink); 3 external calls (run, echo, load_config).


##### `_seed_kitchen_sink`  (lines 1296–1307)

```
async def _seed_kitchen_sink(config: Config, named: str) -> UUID
```

**Purpose**: Sets up database and blob storage resources for writing the kitchen-sink demo conversation. It ensures hosted workspace lookup has the owner database available when possible.

**Data flow**: It initializes the app database, optionally initializes the owner database, creates a workspace-aware blob store from config, calls `_seed_target`, returns the new conversation ID, and closes database resources.

**Call relations**: `seed_kitchen_sink` calls this. It prepares the environment, then hands the actual content creation to `_seed_target`.

*Call graph*: calls 1 internal fn (_seed_target); called by 1 (seed_kitchen_sink); 5 external calls (__init__, blob_store_for, dispose_db, init_db, init_owner_db).


##### `_seed_target`  (lines 1310–1341)

```
async def _seed_target(blob: WorkspaceBlobStore, named: str) -> UUID
```

**Purpose**: Writes the kitchen-sink demo into a specific workspace. It finds the main agent and first member needed to own the seeded conversation.

**Data flow**: It resolves the target workspace, pins workspace context, reads the main agent ID and first member from the database, raises an error if there is no member, constructs a `KitchenSink` writer with blob storage and IDs, writes the content, and returns the conversation ID.

**Call relations**: `_seed_kitchen_sink` calls this after resource setup. It uses `_target_workspace` for safe workspace selection and the onboarding seed subsystem for the actual demo content.

*Call graph*: calls 1 internal fn (_target_workspace); called by 1 (_seed_kitchen_sink); 5 external calls (__init__, ClickException, select, workspace_tx, ws).


### Deployment configuration
Loads and validates the main deployment configuration so bad settings fail before startup continues.

### `core/src/ufo/config.py`

`config` · `config load and startup`

This file is the project's configuration gatekeeper. It describes, in one place, all the settings a UFO deployment needs: database addresses, blob storage, model choices, sandbox behavior, extension settings, feature flags, browser support, connectors, and more. Without this file, the rest of the system would have to guess where services live and which backends to use, which could lead to quiet misconfiguration or unsafe defaults.

The main idea is simple: read one TOML file, turn it into structured Python objects, and reject anything surprising. TOML is a human-friendly config format, like a stricter version of an INI file. The structured objects are Pydantic models, which means each section gets type checking, defaults, and custom validation.

Several settings are deliberately checked loudly. For example, filesystem blob storage must name a root folder, S3 blob storage must name a bucket, and model names cannot be the placeholder value `auto`. The database system URL can be derived automatically from the main database URL, saving the operator from repeating a predictable setting. The sandbox ingress public URL is checked carefully because it becomes the base for member-facing web links and cookies; allowing paths, credentials, or insecure public HTTP there could create broken links or security problems.

At the bottom, `load_config` finds the config file, reads it, parses it, and validates the whole thing before handing a `Config` object to the rest of the application.

#### Function details

##### `DatabaseConfig._derive_system_url`  (lines 41–54)

```
def _derive_system_url(self) -> 'DatabaseConfig'
```

**Purpose**: This validator fills in the database system-store URL when the operator did not write one explicitly. It keeps the common case simple while still allowing a custom system database location when needed.

**Data flow**: It starts with a `DatabaseConfig` object that has the main application database URL and may or may not have `system_url`. If `system_url` is already present, it leaves everything alone. If it is missing, it splits the main URL, creates a sibling database name ending in `_dbos`, adjusts the driver name for SQLite or PostgreSQL where needed, stores that derived URL back on the object, and returns the updated object.

**Call relations**: Pydantic calls this after building a `DatabaseConfig` from the config file. It does not call other project functions; it prepares the database settings so later startup code can rely on both the application database URL and the DBOS system-store URL being present.


##### `BlobConfig._backend_complete`  (lines 76–81)

```
def _backend_complete(self) -> 'BlobConfig'
```

**Purpose**: This validator checks that the chosen blob storage backend has the minimum information it needs. Filesystem storage needs a local root directory, while S3 storage needs a bucket name.

**Data flow**: It receives a `BlobConfig` object after basic parsing. If the backend is `filesystem`, it checks that `root` was provided. If the backend is `s3`, it checks that `bucket` was provided. When a required value is missing, it raises an error; otherwise it returns the unchanged config object.

**Call relations**: Pydantic runs this while validating the blob section of the configuration. Its job is to stop startup before any blob-store code tries to write transcripts, artifacts, or other shared data to a location that was never fully configured.


##### `ModelsConfig._models_concrete`  (lines 104–115)

```
def _models_concrete(self) -> 'ModelsConfig'
```

**Purpose**: This validator makes sure deployment-level model settings name real model IDs, not the placeholder `auto` and not an empty string. This matters because these values decide which external AI models are actually billed and used.

**Data flow**: It receives a `ModelsConfig` object containing the selected model names. It checks `auto_model`, `ambient_reply_model`, and `background_jobs_model` one by one. If any field is blank or equals the special placeholder model name, it raises an error explaining which setting is wrong. If all three are concrete names, it returns the config object.

**Call relations**: Pydantic calls this during config validation. It protects later agent turns and background jobs from reaching runtime with an unresolved model choice.


##### `SandboxConfig._ingress_base_is_addressable`  (lines 236–272)

```
def _ingress_base_is_addressable(self) -> 'SandboxConfig'
```

**Purpose**: This validator checks that the public base URL for sandbox-hosted web pages is safe and usable. It prevents confusing or insecure addresses from being used for member-facing links and session cookies.

**Data flow**: It receives a `SandboxConfig` object. If `ingress_public_url` is not set, it returns the object unchanged. If it is set, it breaks the URL into parts using `urlsplit`, then checks that it has a host, uses HTTPS unless it is a local-only address accepted by `plain_local`, and contains no path, query string, fragment, username, or password. If any rule is broken, it raises an error; otherwise it returns the config object.

**Call relations**: Pydantic runs this when validating the sandbox section. It calls `urllib.parse.urlsplit` to inspect the URL and `ufo.sdk.http.plain_local` to allow special local-development HTTP URLs. The result is used later by sandbox ingress and link-building code, which both need this URL to be just a clean scheme-and-host base.

*Call graph*: 2 external calls (plain_local, urlsplit).


##### `config_path`  (lines 419–420)

```
def config_path() -> Path
```

**Purpose**: This small helper decides which configuration file path to use. It lets an operator override the default `ufo.toml` location with the `UFO_CONFIG` environment variable.

**Data flow**: It reads the process environment for `UFO_CONFIG`. If that variable is set, it turns its value into a `Path` object. If it is not set, it uses the default path `ufo.toml`. The output is the path that config loading should try to read.

**Call relations**: `load_config` calls this when no path was supplied directly. It is the bridge between the outside process environment and the file-loading step.

*Call graph*: called by 1 (load_config); 1 external calls (Path).


##### `load_config`  (lines 423–429)

```
def load_config(path: Path | None=None) -> Config
```

**Purpose**: This function reads the deployment configuration file and turns it into a validated `Config` object. It is the normal entry point for code that needs the application's settings.

**Data flow**: It receives an optional path. If a path is given, it uses that; otherwise it asks `config_path` for the default or environment-selected path. It checks that the file exists, reads its text, parses the TOML text with `tomllib.loads`, and then asks the top-level `Config` model to validate the parsed data. The output is a fully checked `Config` object, or an early error if the file is missing or invalid.

**Call relations**: Startup code calls this when it needs configuration. Inside, it calls `config_path` only when the caller did not provide a path, and it calls Python's `tomllib.loads` to parse the file before Pydantic validates all the section models and their custom checks.

*Call graph*: calls 1 internal fn (config_path); 1 external calls (loads).


### Product census
Computes workspace funnel, integration, and onboarding metrics used for product reporting during service operation.

### `core/src/ufo/product.py`

`domain_logic` · `scheduled census ticks and occasional onboarding event recording`

This file is like a census taker for each workspace. Instead of recording every product event when it happens, it periodically looks at the database and asks: has this workspace seated a member, connected a tool, invited someone, built an app, chatted, stayed active, or paid? It then sends those yes-or-no answers as metrics, so the fleet-wide dashboard can add them up.

The file also counts onboarding steps. Some steps can be rediscovered from database rows, such as the first chat, first connector, or first teammate invite. For these, `onboarding_census` looks for the earliest row and only counts it if it happened during the most recent census window. Other steps leave no durable database row, such as a skipped first-run screen or a failed setup attempt, so `record_onboarding_step` records those immediately.

A key idea is that the database remains the source of truth. If the team changes what “active” or “onboarded” means later, the next census can recompute the answer from existing rows. The code is careful to always filter by `workspace_id`, because counting another workspace’s rows under the current workspace would quietly inflate the dashboard.

#### Function details

##### `_member_turn`  (lines 76–84)

```
def _member_turn(workspace_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the shared database condition for “a real member started a top-level chat turn in this workspace.” This keeps the meaning of member chat activity identical in the funnel metrics and the onboarding metrics.

**Data flow**: It receives a workspace ID. It combines that ID with checks on the turn table: the turn must belong to the workspace, must come from a member admission source, and must not be a reply nested under another turn. It returns a database expression that other queries can plug into their own searches.

**Call relations**: Both `product_census` and `onboarding_census` call this before querying chat activity. By sharing this helper, the file avoids two slightly different definitions of “member chatted” drifting apart.

*Call graph*: called by 2 (onboarding_census, product_census); 1 external calls (and_).


##### `product_census`  (lines 87–185)

```
async def product_census() -> None
```

**Purpose**: Counts the current workspace’s product funnel stages and attached tools, then emits them as observability metrics. It is the periodic snapshot that feeds product dashboards.

**Data flow**: It starts from the workspace currently bound to the running task and the current time. It builds one database query for funnel stages, such as seated, connected, invited, active, and paid, and another query for attachments, such as installed surfaces, proved addresses, credentials, connectors, and apps. After reading those results inside a workspace database transaction, it emits one metric per stage and one metric per attachment found.

**Call relations**: This is a top-level census job rather than a helper for another function in this file. During its work it calls `_member_turn` so chat-based stages match onboarding chat steps, uses the database transaction helper to read consistent workspace data, and hands the final counts to the metric emitter.

*Call graph*: calls 1 internal fn (_member_turn); 9 external calls (now, timedelta, exists, literal, select, union_all, workspace_tx, emit_metric, ws_current).


##### `_utc`  (lines 188–189)

```
def _utc(moment: datetime) -> datetime
```

**Purpose**: Makes sure a timestamp can be compared as a UTC time. This prevents time math from going wrong when one timestamp has timezone information and another does not.

**Data flow**: It receives a datetime value. If the value already has timezone information, it returns it unchanged. If it has no timezone, it treats it as UTC and returns a version marked that way.

**Call relations**: `_elapsed_ms` uses this before subtracting two times. `onboarding_census` also uses it when deciding whether a step happened inside the current census window.

*Call graph*: called by 2 (_elapsed_ms, onboarding_census); 1 external calls (replace).


##### `_elapsed_ms`  (lines 192–193)

```
def _elapsed_ms(created_at: datetime, at: datetime) -> int
```

**Purpose**: Calculates how many milliseconds passed between workspace creation and an onboarding step. The result becomes the latency measurement shown on onboarding dashboards.

**Data flow**: It receives two timestamps: when the workspace was created and when the step happened. It normalizes both through `_utc`, subtracts them, converts the difference to milliseconds, and never returns a negative number.

**Call relations**: `record_onboarding_step` uses it for steps reported at the moment they happen. `onboarding_census` uses it for steps discovered later from database rows.

*Call graph*: calls 1 internal fn (_utc); called by 2 (onboarding_census, record_onboarding_step).


##### `_emit_step`  (lines 196–203)

```
def _emit_step(step: str, status: str, surface: str, provider: str, latency_ms: int | None) -> None
```

**Purpose**: Sends the metrics for one onboarding step. It always counts that the step happened, and when a duration is available, it also records how long the workspace took to reach it.

**Data flow**: It receives the step name, success or failure status, surface, provider, and an optional latency in milliseconds. It emits a count metric with the step details as tags. If latency is present, it also emits a histogram value, which is a metric type used to summarize distributions like “how long did this usually take?”

**Call relations**: Both `record_onboarding_step` and `onboarding_census` hand their finished step facts to this function. It is the final bridge from product/onboarding logic into the observability system.

*Call graph*: called by 2 (onboarding_census, record_onboarding_step); 2 external calls (emit_histogram, emit_metric).


##### `record_onboarding_step`  (lines 206–231)

```
async def record_onboarding_step(workspace_id: UUID, step: str, status: str, *, surface: str, provider: str=NO_PROVIDER) -> None
```

**Purpose**: Records an onboarding step that cannot be reliably rediscovered from database rows later. Examples include a skipped screen or a setup failure that leaves no normal product record.

**Data flow**: It receives the workspace ID, step name, status, surface, and optional provider. It reads the workspace creation time from the database, computes how long after creation the step happened if possible, and emits the step metric immediately.

**Call relations**: This function is used by call sites that know a non-persistent onboarding outcome just happened. It uses `_elapsed_ms` to compute timing and `_emit_step` to send the result, while leaving row-derived steps to `onboarding_census`.

*Call graph*: calls 2 internal fn (_elapsed_ms, _emit_step); 3 external calls (now, select, workspace_tx).


##### `onboarding_census`  (lines 234–326)

```
async def onboarding_census() -> None
```

**Purpose**: Finds onboarding milestones that first appeared during the most recent census window and emits one completed-step metric for each. This lets the system count setup progress without requiring every milestone to be manually logged as an event.

**Data flow**: It starts with the currently bound workspace and the current time. It queries the database for the workspace creation time and the first occurrence of key milestones, such as first member chat, first connector, first surface install, first invite, first app build, and first invited member chat. It skips missing milestones and milestones older than the current window, then emits completed-step metrics with the time from workspace creation to each step.

**Call relations**: This is the scheduled counterpart to `record_onboarding_step`. It calls `_member_turn` so chat milestones match the funnel definition, uses `_utc` to compare timestamps safely, uses `_elapsed_ms` to calculate latency, and hands each completed milestone to `_emit_step`.

*Call graph*: calls 4 internal fn (_elapsed_ms, _emit_step, _member_turn, _utc); 5 external calls (now, timedelta, select, workspace_tx, ws_current).


### Runtime service bootstrap
Prepares shared runtime inputs, starts the main service fleet, and connects feature-flag providers needed before traffic or workers run.

### `core/src/ufo/proxy_serve.py`

`config` · `startup/config load`

This file is a small “front desk” for shared proxy services. Its job is to gather secrets and connection details from the deployment environment, check that the important ones exist, and turn them into the forms the rest of the system expects.

The first concern is model access. Sandboxed code is not allowed to freely call the internet. Instead, it gets an egress rule set, meaning a list of outside hosts it is allowed to reach. `model_rule_base` looks for configured model-provider API keys, such as Anthropic or OpenAI keys. If a key exists, it asks the egress-rule code to build the matching rules for that provider. It combines the allowed hosts into one scope rule and keeps any extra key-substitution rules. If no model key is available at all, it stops immediately, because the sandbox would have no route to a model provider.

The second concern is database access for a shared ingress service. Normal database URLs may be limited by row-level security, which means the database hides rows depending on the caller. This shared service intentionally uses an owner connection string that bypasses that protection, then relies on explicit workspace filters in queries. `owner_dsn` finds that special connection string and rewrites its driver prefix so the async PostgreSQL driver is used.

#### Function details

##### `model_rule_base`  (lines 18–37)

```
def model_rule_base(config: Config) -> tuple[Rule, ...]
```

**Purpose**: This function builds the basic network permission rules that let every sandbox reach configured model providers. It also makes sure the deployment has at least one usable model API key, so failures happen early instead of later inside a sandbox.

**Data flow**: It receives the main `Config` object and reads the environment variable names for Anthropic and OpenAI keys from it. For each key name, it asks `deploy_env` for the actual deployed secret; when a key is present, it calls `derive_model_rules` to turn a model probe and key into egress rules. It gathers allowed host names into one `ScopeRule`, keeps any other rules beside it, and returns them as a tuple; if no hosts were found, it raises an error instead of returning an empty rule set.

**Call relations**: During shared-service setup, this function is the place where deployment secrets become sandbox network permissions. It relies on `deploy_env` to read secrets from the deployment environment, on `derive_model_rules` to know what each model provider needs, and on `ScopeRule` to package the final allowed-host list for the egress-control layer.

*Call graph*: 3 external calls (__init__, deploy_env, derive_model_rules).


##### `owner_dsn`  (lines 40–52)

```
def owner_dsn(config: Config) -> str
```

**Purpose**: This function chooses the special database connection string used by the shared ingress service. That connection uses the database owner role, so it can bypass row-level security while the service itself is responsible for filtering each query by workspace.

**Data flow**: It receives the main `Config` object, then first checks the `UFO_OWNER_DSN` environment variable. If that is not set, it falls back to `config.database.owner_url`. If neither exists, it raises a clear error explaining what must be configured. When it finds a connection string, it changes a leading `postgresql://` prefix to `postgresql+psycopg://` so the async PostgreSQL driver is selected, then returns the rewritten string.

**Call relations**: This function is called when the shared ingress service needs to open its database connection. It does not call other project helpers; it simply reads the process environment and the loaded configuration, validates that the owner connection exists, and hands back the exact database URL the service should use.


### `core/src/ufo/serve.py`

`entrypoint` · `startup, main loop, background work, shutdown`

Think of this file as the control room for a shared service that serves many workspaces at once. At startup it reads configuration, opens database access, loads installed extensions, checks that required secrets exist, chooses storage and messaging backends, prepares model and memory services, sets up sandbox execution, and registers background jobs. It then builds a FastAPI web app, mounts extension routes and user-facing “surface” routes, and starts uvicorn, the HTTP server.

A central concern here is workspace safety. One process can receive requests for many different workspaces, so each request must be tied to the correct workspace before it reads data or credentials. The `WorkspaceScopeBoundary` middleware clears stale workspace state before and after each HTTP request, while surface route handlers set the workspace after checking the request’s identity.

The file also starts long-running housekeeping work: heartbeats that say this process is alive, recovery sweeps for abandoned jobs, pollers that deliver durable surface writebacks, and listeners from extensions. On shutdown it carefully stops DBOS, the durable workflow executor, and only marks this process’s “seat” as retired if no workflow is still running. Without this file, the project would have parts, but no complete running service.

#### Function details

##### `_payload_digest`  (lines 216–218)

```
def _payload_digest(payload: object) -> str
```

**Purpose**: Creates a stable fingerprint for a piece of configuration-like data. This lets the service record exactly which settings produced the running runtime identity.

**Data flow**: It receives any JSON-compatible value, turns it into a consistently ordered JSON byte string, hashes those bytes with SHA-256, and returns the hash as text prefixed with `sha256:`.

**Call relations**: It is used by `_runtime_identity` when the service is starting, so the runtime record can include compact fingerprints instead of copying large configuration objects.

*Call graph*: called by 1 (_runtime_identity); 2 external calls (sha256, dumps).


##### `_runtime_identity`  (lines 221–239)

```
def _runtime_identity(config: Config, carrier: CarrierSpec) -> RuntimeIdentity
```

**Purpose**: Builds a description of the running service version and sandbox setup. This helps surfaces and clients know what runtime image, revision, and configuration they are talking to.

**Data flow**: It reads the loaded configuration, the selected sandbox carrier, and optional environment variables for runtime revision and image. It validates that revision and image are provided together, digests the configuration and sandbox settings, and returns a `RuntimeIdentity` object.

**Call relations**: `run` calls this during startup after choosing the sandbox carrier. It relies on `_payload_digest` to make stable fingerprints and asks the carrier for its own runtime digest when available.

*Call graph*: calls 1 internal fn (_payload_digest); called by 1 (run); 3 external calls (__init__, model_dump, runtime_digest).


##### `_assert_no_reserved_routes`  (lines 242–258)

```
def _assert_no_reserved_routes(app: FastAPI) -> None
```

**Purpose**: Prevents this service from accidentally claiming URL paths that belong to the sign-in and onboarding gateway. This catches a routing mistake at boot instead of letting the deployment silently send traffic to the wrong place.

**Data flow**: It reads the routes already mounted on the FastAPI app, looks for paths that start with reserved prefixes such as login, logout, join, onboarding, or `/ufo`, and raises an error if it finds any.

**Call relations**: `run` calls it after all routes have been mounted and before the web server starts. It is a final safety check on the assembled HTTP app.

*Call graph*: called by 1 (run).


##### `run`  (lines 261–509)

```
def run() -> None
```

**Purpose**: Starts the shared UFO fleet process. It is the top-level function that turns configuration, extensions, databases, jobs, sandboxes, and HTTP routes into a live server.

**Data flow**: It reads configuration and environment variables, initializes observability and databases, loads manifests, creates credential storage, chooses backends, builds the runtime object, registers jobs and routes, launches DBOS, then starts uvicorn. When uvicorn exits, it performs controlled executor shutdown.

**Call relations**: This is the main orchestrator in the file. It calls nearly every helper here: selection helpers choose backends, mounting helpers install HTTP routes, `_launch_jobs` registers background work, and `_stop_executor` cleans up at the end.

*Call graph*: calls 23 internal fn (from_env, from_skills, _assert_no_reserved_routes, _connect_flow, _connector_registry, _launch_jobs, _mount_ext_routes, _mount_shared_surfaces, _one_shot, _preview_settings (+13 more)); 59 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


##### `run.invoker_for`  (lines 327–328)

```
def invoker_for(workspace_id: UUID) -> AdmissionInvoker
```

**Purpose**: Creates an admission invoker for a specific workspace. An admission invoker is the object used to admit or start work on behalf of that workspace.

**Data flow**: It receives a workspace ID, combines it with the shared `Admission` object prepared by `run`, and returns an `AdmissionInvoker` bound to that workspace.

**Call relations**: `run` defines this small helper so many later pieces can ask for workspace-specific admission without rebuilding the shared admission machinery.

*Call graph*: 1 external calls (__init__).


##### `_one_shot`  (lines 512–524)

```
def _one_shot(coro: Coroutine[Any, Any, T]) -> T
```

**Purpose**: Runs one asynchronous database-related action on a temporary event loop and then cleans up that loop’s database engines. This avoids leaving pooled database connections attached to a loop that has already closed.

**Data flow**: It receives a coroutine, wraps it in a cleanup step, runs it with `asyncio.run`, and returns the coroutine’s result after cleanup has been attempted.

**Call relations**: `run`, `_proxy_endpoint`, and `_stop_executor` use it for startup or shutdown actions that need async database work before the long-lived server loop exists or after it is not suitable.

*Call graph*: called by 3 (_proxy_endpoint, _stop_executor, run); 1 external calls (run).


##### `_one_shot.step`  (lines 518–522)

```
async def step() -> T
```

**Purpose**: Performs the actual temporary-loop work for `_one_shot` and guarantees database loop cleanup afterward.

**Data flow**: It awaits the coroutine passed to `_one_shot`. Whether that coroutine succeeds or fails, it then asks the database layer to dispose of engines tied to the temporary loop.

**Call relations**: This nested function is only used inside `_one_shot`. It is the reason `_one_shot` can safely run short async actions without leaking loop-bound database resources.

*Call graph*: 1 external calls (dispose_loop_engines).


##### `_stop_executor`  (lines 527–541)

```
def _stop_executor(dbos: DBOS, heartbeat: Heartbeat, graceful_shutdown_seconds: int) -> None
```

**Purpose**: Stops DBOS workflow execution safely during shutdown. It avoids marking this process as gone while work may still be running inside it.

**Data flow**: It asks DBOS to drain and destroy workflow execution within the configured grace period, checks whether any workflows are still active, and either keeps the process seat alive or retires it through the heartbeat record.

**Call relations**: `run` calls this in its `finally` block after uvicorn stops. It uses `_one_shot` to retire the heartbeat asynchronously only when it is safe.

*Call graph*: calls 2 internal fn (retire, _one_shot); called by 1 (run); 2 external calls (destroy, log).


##### `_shared_owner_dsn`  (lines 544–558)

```
def _shared_owner_dsn(config: Config) -> str
```

**Purpose**: Finds the special database connection string used for cross-workspace owner-level reads. This is needed for jobs that first enumerate work across all workspaces and then re-enter each workspace safely.

**Data flow**: It reads an owner database URL from an environment variable or configuration. If neither is set, it raises a clear startup error; otherwise it returns the selected connection string.

**Call relations**: `run` calls it before initializing the owner database connection. Without it, shared fleet sweeps would not have the database permissions they need to enumerate workspaces.

*Call graph*: called by 1 (run).


##### `_launch_jobs`  (lines 561–628)

```
def _launch_jobs(runtime: Runtime, invoker_for: InvokerFactory, sync_driver: SyncDriver, page_feed: CorePageFeed) -> None
```

**Purpose**: Registers and starts the background jobs this service must run. These include core jobs such as source sync, turn dispatch, delivery cleanup, page indexing, and extension-provided jobs.

**Data flow**: It receives the assembled runtime, an invoker factory, a source sync driver, and a page feed. It builds helper objects for probes, page changes, optional previews, job bindings, and then launches a `JobRunner`.

**Call relations**: `run` calls this after DBOS and runtime pieces are ready. The job runner it starts feeds work into the durable workflow system and extension job system.

*Call graph*: called by 1 (run); 13 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, connector_clis (+3 more)).


##### `_source_backends`  (lines 631–645)

```
def _source_backends(manifests: tuple[Manifest, ...]) -> dict[str, SourceBackend]
```

**Purpose**: Builds the list of source-sync backends available in this deployment. A source backend is code that knows how to sync a particular kind of external or internal source.

**Data flow**: It starts with the built-in folder source, then reads each extension manifest for additional source providers. It gives each provider credential access limited to that extension’s declared credential slots and returns a backend-name-to-backend map.

**Call relations**: `run` uses this map when constructing the `SyncDriver`. It rejects duplicate backend names so a configured source always points to one clear implementation.

*Call graph*: called by 1 (run); 2 external calls (__init__, __init__).


##### `_source_identity_resolvers`  (lines 648–689)

```
def _source_identity_resolvers(manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: WorkspaceBlobStore) -> dict[str, SourceIdentityResolver]
```

**Purpose**: Builds helpers that can ask a surface who the current user is for source-sync purposes. This lets synced source data be tied to the right external identity.

**Data flow**: It scans extension surfaces for `self_user_id` handlers, wraps each one with workspace binding and credential checks, and returns a map from surface name to resolver function.

**Call relations**: `run` passes these resolvers into the `SyncDriver`. The nested `resolve` function is what actually runs later when source sync needs a surface-specific identity.

*Call graph*: called by 1 (run).


##### `_source_identity_resolvers.resolve`  (lines 662–686)

```
async def resolve(workspace_id: UUID, handler=surface.self_user_id, slots=declared, store=credentials) -> str | None
```

**Purpose**: Resolves one workspace’s user identity for a particular surface. It runs the extension’s identity handler inside the correct workspace scope.

**Data flow**: It receives a workspace ID, creates a credential reader limited to the extension’s declared slots, binds the workspace with `ws(...)`, builds a `SurfaceIdentityContext`, and awaits the surface’s identity handler. It returns the user ID or `None`.

**Call relations**: This function is created by `_source_identity_resolvers` for each surface that declares identity support. The sync driver calls it when it needs to know which external user a workspace corresponds to.

*Call graph*: 2 external calls (__init__, ws).


##### `_source_identity_resolvers.resolve.credential`  (lines 668–677)

```
async def credential(credential_slot: str) -> str
```

**Purpose**: Provides safe credential lookup for a surface identity resolver. It prevents an extension from reading credential slots it did not declare.

**Data flow**: It receives a credential slot name, checks that the slot was declared by the extension, checks that a credential store exists, and returns the stored credential value for the workspace.

**Call relations**: This nested helper is passed into the surface identity handler through `SurfaceIdentityContext`, so extension code can request only approved credentials while resolving identity.


##### `_select_hub`  (lines 692–710)

```
def _select_hub(config: Config, manifests: tuple[Manifest, ...]) -> Hub
```

**Purpose**: Chooses the live message hub used by the process. The hub is the place where live surface updates and events are passed around.

**Data flow**: It creates a registry of hub builders from the built-in in-process hub and extension-provided hubs, checks for duplicate names, looks up the configured backend, and returns the built hub.

**Call relations**: `run` calls it during startup before building surfaces and the runtime. If configuration names a missing hub backend, it fails immediately instead of starting with broken live updates.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_terminal_transport`  (lines 713–755)

```
def _select_terminal_transport(config: Config, manifests: tuple[Manifest, ...], blob: FleetBlobStore) -> TerminalTransport
```

**Purpose**: Chooses how sandbox terminal connections rendezvous with users and workflows. In multi-process deployments this must be a shared transport, not memory local to one process.

**Data flow**: It checks for an unsafe combination of cross-process hub with in-process terminal transport, builds a registry of terminal transport builders, resolves the configured backend, and returns the selected transport.

**Call relations**: `run` calls it when constructing the conversation sandbox system. It mirrors `_select_hub`, but also enforces a deployment safety rule so terminal sessions are not stranded on the wrong pod.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_cdp_provider`  (lines 758–786)

```
def _select_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> CdpProvider | None
```

**Purpose**: Chooses the browser automation provider, if one is installed and selected. CDP means Chrome DevTools Protocol, a way to control a browser programmatically.

**Data flow**: It scans extension manifests for CDP providers, rejects duplicate backend names, finds the configured provider, verifies credential support if needed, and returns a built provider or `None`.

**Call relations**: `run` uses it to provide browser capability to the runtime. `_require_cdp_provider` also calls it when an extension declares that browser control is mandatory.

*Call graph*: called by 2 (_require_cdp_provider, run); 1 external calls (__init__).


##### `_validate_requires`  (lines 789–813)

```
def _validate_requires(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Checks extension-declared requirements before the service starts serving traffic. This turns missing optional-looking pieces into clear startup errors when an active extension needs them.

**Data flow**: It reads each manifest’s `requires` list, looks up the matching validation function, and runs it. If a requirement is unknown or unavailable, it raises an error naming the extension and missing seam.

**Call relations**: `run` calls this soon after loading manifests and credentials. It delegates specific checks to functions such as `_require_cdp_provider`, `_require_search_provider`, and `_require_memory_search`.

*Call graph*: called by 1 (run).


##### `_require_cdp_provider`  (lines 816–830)

```
def _require_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that a usable browser automation provider exists when an extension requires one.

**Data flow**: It calls `_select_cdp_provider` with the current configuration, manifests, and credentials. If no provider is selected and available, it raises a startup error.

**Call relations**: _validate_requires calls this for extensions that list the `cdp_providers` requirement. It reuses the normal provider-selection logic so the requirement check matches real startup behavior.

*Call graph*: calls 1 internal fn (_select_cdp_provider).


##### `_select_search_provider`  (lines 833–868)

```
def _select_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> SearchProvider | None
```

**Purpose**: Chooses the research search provider, if configured. This is the backend used when tools need web or external search capability.

**Data flow**: It scans extension manifests for search providers, rejects duplicate names, returns `None` if search is unset, validates the configured backend and credential key, then builds and returns the provider.

**Call relations**: `run` calls it while assembling the runtime. `_require_search_provider` also calls it to turn missing or broken search configuration into a startup failure for research extensions.

*Call graph*: called by 2 (_require_search_provider, run); 2 external calls (__init__, __init__).


##### `_require_search_provider`  (lines 871–884)

```
def _require_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that research tools have a configured and usable search backend.

**Data flow**: It checks that the search provider configuration is set, then calls `_select_search_provider` to validate and build the selected backend.

**Call relations**: _validate_requires calls this when an extension declares the `search_providers` requirement. It ensures research tools do not fail only at the first user request.

*Call graph*: calls 1 internal fn (_select_search_provider).


##### `_select_flag_provider`  (lines 887–914)

```
def _select_flag_provider(config: Config, manifests: tuple[Manifest, ...]) -> FeatureProvider | None
```

**Purpose**: Chooses the feature flag provider. Feature flags are switches that can turn behavior on or off without changing code.

**Data flow**: It scans extension manifests for flag providers, rejects duplicate names, returns `None` if no backend is configured, builds the selected provider, and warns if the selected provider cannot be keyed.

**Call relations**: `run` calls this before initializing the flag system. If configuration names a nonexistent provider, startup fails because silent flag disablement would be misleading.

*Call graph*: called by 1 (run); 2 external calls (__init__, warn).


##### `_require_memory_search`  (lines 917–943)

```
def _require_memory_search(_config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Checks that the default memory-search provider exists exactly once and is usable. Memory search is how the system looks up stored contextual information.

**Data flow**: It scans manifests for providers with the default memory-search name, rejects zero or multiple matches, and verifies that required credentials can be read if the provider declares credential slots.

**Call relations**: _validate_requires calls this when an extension requires memory search. It does not build the provider itself; it validates that the provider selection will be possible.


##### `_select_auth_proxy`  (lines 955–992)

```
def _select_auth_proxy(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> AuthProxy | None
```

**Purpose**: Chooses the fallback authentication proxy used by connectors that do not have their own broker. This proxy helps resolve credentials for external services.

**Data flow**: It gathers auth proxy specs from manifests, handles the case where configuration leaves the backend unset, rejects ambiguous or unknown choices, verifies a credential key exists, and returns the built proxy or `None`.

**Call relations**: _connector_registry calls this while building connector routing. Brokered connectors can bypass this fallback, but unbrokered ones depend on the selected proxy.

*Call graph*: called by 1 (_connector_registry); 2 external calls (__init__, __init__).


##### `_mount_ext_routes`  (lines 995–1043)

```
def _mount_ext_routes(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, index: IndexBackend, embed: EmbedClient, public_base_url: str | None) -> None
```

**Purpose**: Adds HTTP routes supplied by extensions under `/ext/<extension-name>/...`. Each route is protected by an identity check before extension code can run.

**Data flow**: It walks extension manifests, builds an extension context with access to indexing, embedding, public URLs, and declared credentials, then adds each route to the FastAPI app. If an extension serves routes without credential support, startup fails.

**Call relations**: `run` calls this after core setup and before starting the server. The nested endpoint function is what handles each mounted extension request later.

*Call graph*: calls 1 internal fn (home_surface); called by 1 (run); 2 external calls (add_route, context_for).


##### `_mount_ext_routes.endpoint`  (lines 1027–1037)

```
async def endpoint(request: Request, handler=spec.handler, identify=spec.identify, extension_context=context) -> Response
```

**Purpose**: Handles one incoming extension route request. It verifies which workspace the request belongs to before calling the extension’s route handler.

**Data flow**: It receives a browser request, asks the route’s `identify` function for a workspace, returns HTTP 401 if not identified, otherwise binds that workspace and awaits the extension handler. The handler’s response is returned to the client.

**Call relations**: This endpoint is created inside `_mount_ext_routes` for each extension route. It is called by FastAPI during request handling.

*Call graph*: 2 external calls (Response, ws).


##### `WorkspaceScopeBoundary.__call__`  (lines 1064–1072)

```
async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None
```

**Purpose**: Clears workspace state at the start and end of each HTTP request. This prevents one request from accidentally inheriting another workspace’s identity.

**Data flow**: It receives the low-level ASGI request scope, receive function, and send function. For non-HTTP traffic it passes through unchanged; for HTTP it sets `current_workspace` to `None`, runs the downstream app, then clears it again even if an error occurs.

**Call relations**: _mount_shared_surfaces installs this middleware on the FastAPI app. Surface endpoints set the workspace after authentication, and this boundary guarantees that binding does not leak to the next request.

*Call graph*: 1 external calls (set).


##### `_mount_shared_surfaces`  (lines 1075–1263)

```
def _mount_shared_surfaces(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: WorkspaceBlobStore, sandboxes: ConversationSandbox, hub: Hub, dbos_client: DBOSClie
```

**Purpose**: Mounts the user-facing shared surfaces, such as chat or browser-facing interfaces, so one service process can serve many workspaces safely. It also prepares pollers and listeners tied to those surfaces.

**Data flow**: It receives the app and all runtime support objects, installs workspace-bound middleware, builds shared admission, tailing, stopping, skill, connector, object, memory, and preview context, then adds each surface route. It stores listeners and durable pollers on app state for the lifespan runner.

**Call relations**: `run` calls this after the runtime is initialized. It creates the nested `context_for` helper used by routes, listeners, and pollers, and calls `_mount_home` so the bare host can redirect to the chosen home surface.

*Call graph*: calls 5 internal fn (bundled_skills, from_skills, _connector_entries, _mount_home, home_surface); called by 1 (run); 23 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+13 more)).


##### `_mount_shared_surfaces.context_for`  (lines 1157–1197)

```
def context_for(workspace_id: UUID, surface: str) -> SurfaceContext
```

**Purpose**: Builds the complete context object a surface route or listener needs for one workspace. This is like packing a toolbox for a surface request.

**Data flow**: It receives a workspace ID and surface name, combines them with shared services such as blob storage, sandbox access, admission, credentials, models, skills, connectors, memory, object schemas, preview settings, and URLs, and returns a `SurfaceContext`.

**Call relations**: It is created inside `_mount_shared_surfaces` and used by mounted surface endpoints, surface listeners, and durable writeback pollers whenever they need to act for a particular workspace.

*Call graph*: calls 1 internal fn (home_surface); 3 external calls (__init__, __init__, frame_admissible).


##### `_mount_shared_surfaces.endpoint`  (lines 1226–1240)

```
async def endpoint(request: Request, handler=route.handler, identify=resolver, surface=spec.name, surface_auth=auth) -> Response
```

**Purpose**: Handles one incoming surface route request. It authenticates the request, binds the correct workspace, and then calls the surface’s handler.

**Data flow**: It receives a request, asks the surface’s identity resolver to identify the workspace, returns an early response or HTTP 401 if authorization fails, sets `current_workspace` to the resolved workspace, builds a `SurfaceContext`, and returns the handler’s response.

**Call relations**: This endpoint is created for each surface route by `_mount_shared_surfaces`. It works together with `WorkspaceScopeBoundary`, which clears the workspace after the response is fully done.

*Call graph*: 2 external calls (Response, set).


##### `home_surface`  (lines 1266–1273)

```
def home_surface(manifests: tuple[Manifest, ...]) -> str | None
```

**Purpose**: Finds the single surface marked as the browser home page. This gives the deployment one obvious place to send users who open the bare host.

**Data flow**: It scans all manifests for surfaces marked as home. If more than one claims that role it raises an error; if exactly one exists it returns its name; otherwise it returns `None`.

**Call relations**: `run`, `_mount_home`, `_mount_shared_surfaces`, `_mount_ext_routes`, and `_connect_flow` use this to build links and redirects that point back to the user’s main surface.

*Call graph*: called by 6 (_connect_flow, _mount_ext_routes, _mount_home, _mount_shared_surfaces, context_for, run).


##### `_mount_home`  (lines 1276–1288)

```
def _mount_home(app: FastAPI, manifests: tuple[Manifest, ...]) -> None
```

**Purpose**: Adds a simple `GET /` route that redirects users to the configured home surface. This makes the service root act like a front door instead of a dead end.

**Data flow**: It asks `home_surface` for the home surface name. If there is one, it adds a FastAPI route for `/` whose handler returns a redirect to `/surface/<name>`.

**Call relations**: _mount_shared_surfaces calls this after all surface routes have been mounted. The nested `home` handler runs only when a browser requests the root path.

*Call graph*: calls 1 internal fn (home_surface); called by 1 (_mount_shared_surfaces); 1 external calls (add_route).


##### `_mount_home.home`  (lines 1285–1286)

```
async def home(_request: Request) -> Response
```

**Purpose**: Returns the redirect response for the service root. It sends browsers to the chosen home surface.

**Data flow**: It ignores the request details and returns an HTTP 303 redirect pointing at the surface path prepared by `_mount_home`.

**Call relations**: FastAPI calls this handler for `GET /` after `_mount_home` registers it.

*Call graph*: 1 external calls (RedirectResponse).


##### `_serve_lifespan`  (lines 1292–1321)

```
async def _serve_lifespan(app: FastAPI) -> AsyncIterator[None]
```

**Purpose**: Runs background tasks tied to the FastAPI app’s lifetime. These tasks recover stranded workflows, reconcile cancellations, register configured sources, run surface pollers, and run surface listeners.

**Data flow**: On app startup it registers configured sources, creates an async task group, starts recovery and reconciler tasks plus any pollers and listeners stored on app state, then yields control to the web server. On shutdown it cancels those tasks.

**Call relations**: `run` passes this as the FastAPI lifespan handler. It covers app-loop background work, while the heartbeat is deliberately started separately in `run` on its own thread.

*Call graph*: 5 external calls (__init__, __init__, __init__, TaskGroup, register_sources).


##### `_preview_settings`  (lines 1324–1333)

```
def _preview_settings(config: Config) -> tuple[tuple[str, int], str] | None
```

**Purpose**: Reads and validates the optional sandbox preview service configuration. The preview service can render or expose sandbox previews when enabled.

**Data flow**: It parses the preview service address from configuration. If preview is disabled it returns `None`; if enabled it requires a preview token from the environment and returns the service address plus token.

**Call relations**: `run` uses it when setting up document and site preview support. `_proxy_endpoint` also uses it so egress rules can include preview access when needed.

*Call graph*: called by 2 (_proxy_endpoint, run); 1 external calls (parse_preview_service).


##### `_proxy_endpoint`  (lines 1336–1413)

```
def _proxy_endpoint(app: FastAPI, config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, pricing: Pricing, run_tokens: RunTokenCodec, blob: FilesystemBlobStore | S3BlobS
```

**Purpose**: Sets up sandbox egress control: the policy path that decides what network requests sandboxes may make. It also returns the proxy connection details that sandboxes need.

**Data flow**: It reads proxy, cache, preview, certificate, and token settings; creates a per-agent rules resolver from model, artifact, credential, internet, connector, cache, and preview rules; mounts egress-control routes on the FastAPI app; and returns a `ProxyEndpoint` with port, certificate, and public URL.

**Call relations**: `run` calls this while constructing `ConversationSandbox`. It may call `_ephemeral_egress_ca` for local no-egress defaults, `_preview_settings` for preview tokens, and `_one_shot` to derive artifact store rules.

*Call graph*: calls 3 internal fn (_ephemeral_egress_ca, _one_shot, _preview_settings); called by 1 (run); 13 external calls (__init__, __init__, __init__, __init__, include_router, token_urlsafe, parse_cache_daemon, connector_clis, injecting_slots, model_rule_base (+3 more)).


##### `_ephemeral_egress_ca`  (lines 1416–1435)

```
def _ephemeral_egress_ca() -> str
```

**Purpose**: Creates a temporary certificate authority certificate for local startup when no shared egress certificate is configured. A certificate authority is a trust anchor used to verify certificates.

**Data flow**: It generates a private key, builds a self-signed certificate named `ufo-egress-local`, marks it as a certificate authority, signs it, and returns the certificate text in PEM format. The signing key is not returned.

**Call relations**: _proxy_endpoint calls this only for local-style boots without a configured proxy public URL and without a supplied CA certificate. It lets the sandbox receive a well-formed trust anchor even when no proxy process is running.

*Call graph*: called by 1 (_proxy_endpoint); 9 external calls (generate_private_key, SHA256, BasicConstraints, CertificateBuilder, Name, NameAttribute, random_serial_number, now, timedelta).


##### `_connector_registry`  (lines 1441–1455)

```
def _connector_registry(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> ConnectorRegistry
```

**Purpose**: Builds the routing registry for external-service connectors. Connectors are integrations that can authorize and communicate with providers such as third-party services.

**Data flow**: It collects connector entries from manifests, creates a namespace resolver, chooses a fallback auth proxy if needed, and returns a `ConnectorRegistry`.

**Call relations**: `run` uses this registry in the runtime and sync driver. It relies on `_connector_entries` for the provider list and `_select_auth_proxy` for unbrokered credential resolution.

*Call graph*: calls 2 internal fn (_connector_entries, _select_auth_proxy); called by 1 (run); 2 external calls (__init__, open_connector_namespace).


##### `_connector_entries`  (lines 1458–1468)

```
def _connector_entries(manifests: tuple[Manifest, ...]) -> dict[str, ConnectorEntry]
```

**Purpose**: Collects connector provider metadata from extension manifests. It ensures each provider name is owned by only one extension.

**Data flow**: It scans all manifest connectors, checks for duplicate OAuth provider names, and returns a map from provider name to `ConnectorEntry` with label and broker information.

**Call relations**: _connector_registry uses it to build the main connector registry. `_mount_shared_surfaces` also uses it when it needs a default connector registry for surface contexts.

*Call graph*: called by 2 (_connector_registry, _mount_shared_surfaces); 1 external calls (__init__).


##### `_connect_flow`  (lines 1471–1510)

```
def _connect_flow(credentials: CredentialStore | None, config: Config, manifests: tuple[Manifest, ...], index: IndexBackend | None=None, embed: EmbedClient | None=None, resumption: ConnectResume | Non
```

**Purpose**: Builds the OAuth connection flow used when users connect external services. OAuth is the browser-based authorization handoff where a provider grants access tokens.

**Data flow**: If no credential store exists, it returns `None` because grants cannot be safely stored. Otherwise it gathers connector OAuth providers, checks for duplicate provider names, builds callback and portal URLs, attaches connection hooks, and returns a `ConnectFlow`.

**Call relations**: `run` calls this and installs the returned flow for tools, private surface authorization, and OAuth callbacks. It calls `_connect_redirect_uri` and `home_surface` to create user-facing URLs.

*Call graph*: calls 2 internal fn (_connect_redirect_uri, home_surface); called by 1 (run); 5 external calls (__init__, __init__, connection_hooks, open_connector_namespace, portal_url).


##### `_connect_redirect_uri`  (lines 1513–1539)

```
def _connect_redirect_uri(config: Config, providers: Mapping[str, OAuthProvider]) -> str
```

**Purpose**: Builds and validates the public OAuth callback URL. This must be a real browser-openable address because external providers redirect the user’s browser back to it.

**Data flow**: It reads `connect.public_base_url` from configuration and the set of connector providers. If no providers exist, it returns a best-effort callback path or an empty string. If providers exist, it requires a valid HTTP or HTTPS URL with a concrete host and returns that URL plus the callback path.

**Call relations**: _connect_flow calls this when assembling the OAuth connection system. It fails early for missing, malformed, or wildcard public URLs so connector setup does not break during a user’s authorization attempt.

*Call graph*: called by 1 (_connect_flow); 1 external calls (urlparse).


### `extensions/flagship/ufo_ext_flagship.py`

`io_transport` · `startup for provider registration; operator command handling for flag writes`

Feature flags let the product turn behavior on or off without shipping new code. This file makes Cloudflare Flagship the outside service that answers those flag questions, while keeping the rest of the app talking through OpenFeature, a common feature-flag interface. That matters because the main code can ask “is this flag on?” without knowing anything about Cloudflare.

At startup, the `build` function looks for the deploy-level Cloudflare Flagship settings: the app id, account id, and read token. If any are missing, it warns and returns no provider. In that case, every flag falls back to the default written in code, which is the safe closed state. If the settings exist, it creates a Flagship provider with a short timeout and no retries, so a slow or unreachable flag service cannot hold the product for long.

The file also defines `FlagshipAdmin`, used by command-line tooling such as `ufoctl flags set`. This is deliberately separate from the read path and uses a write token. It reads the whole flag record, changes only the served variation, and writes the record back. That is like taking a form from a filing cabinet, changing one checkbox, and returning the full form so no other fields are lost.

#### Function details

##### `build`  (lines 52–73)

```
def build(cache_ttl_seconds: float) -> FeatureProvider | None
```

**Purpose**: Creates the Cloudflare Flagship read provider that the app uses to answer feature-flag checks. If the deploy is not configured with the needed Cloudflare values, it returns nothing so the app safely uses its built-in defaults.

**Data flow**: It reads three deploy environment values: the Flagship app id, the Cloudflare account id, and the read token. If any value is missing, it records a warning showing which pieces were present and returns `None`. If all are present, it passes them, along with timeout, retry, and cache settings, into the Flagship provider and returns that provider to the caller.

**Call relations**: This is the construction hook named in the file’s manifest. When the core feature-flag system asks the extension for a provider, this function either hands back a ready Cloudflare-backed provider or declines by returning `None`; the actual provider object then talks to Flagship for later flag reads.

*Call graph*: 3 external calls (FlagshipServerProvider, deploy_env, warn).


##### `FlagshipAdmin.serve`  (lines 95–103)

```
def serve(self, key: str, *, on: bool) -> None
```

**Purpose**: Changes which variation of one Flagship flag is served by default, usually to turn a flag on or off. It is careful to change only that served value and preserve the rest of the flag record.

**Data flow**: It takes a flag key and an `on` choice. First it asks Flagship for the current full flag record. It checks that the record is readable and that the requested variation, either `on` or `off`, exists. It then copies all writable fields from the record, replaces `default_variation`, and sends the updated record back. It returns nothing if the write succeeds, and raises an error if the flag cannot be read or cannot serve the requested variation.

**Call relations**: This method uses `_call` for both the read and the write, so all HTTP details and Cloudflare error checking stay in one place. It is the admin-side action used when an operator wants to move a flag, rather than when product code merely reads a flag.

*Call graph*: calls 1 internal fn (_call).


##### `FlagshipAdmin.list`  (lines 105–117)

```
def list(self) -> tuple[str, ...]
```

**Purpose**: Fetches the flag keys that currently exist in the Cloudflare Flagship app. This helps compare what the service actually contains with what infrastructure code or application code expects.

**Data flow**: It sends a request for the app’s flag collection. It expects Cloudflare to return a list of flag summaries. From each summary it takes the flag key, converts it to text, sorts all keys, and returns them as an immutable tuple. If the response is not a readable list, it raises an error.

**Call relations**: Like `serve`, this method relies on `_call` to perform the Cloudflare request and detect failures. It provides the service’s view of available flags, while other parts of the system may know the desired flags from Terraform or the flags the code reads.

*Call graph*: calls 1 internal fn (_call).


##### `FlagshipAdmin._call`  (lines 119–134)

```
def _call(self, method: str, path: str, body: dict[str, object] | None=None) -> dict[str, object]
```

**Purpose**: Sends one authenticated request to Cloudflare’s Flagship API and turns Cloudflare’s response into either a Python dictionary or a clear runtime error. It is the shared doorway for all admin read and write requests.

**Data flow**: It receives an HTTP method, a path under the Flagship flags API, and optionally a JSON body. It builds the full Cloudflare URL using the admin object’s account id and app id, adds the bearer token, sends the request, and parses the JSON response if present. If Cloudflare reports failure through either the HTTP status code or the response body, it raises an error containing the method, path, status, and error details. Otherwise it returns the parsed response dictionary.

**Call relations**: Both `FlagshipAdmin.serve` and `FlagshipAdmin.list` call this helper whenever they need to speak to Cloudflare. That keeps authentication, URL construction, response parsing, and error handling consistent for the admin tools.

*Call graph*: called by 2 (list, serve).


##### `build_admin`  (lines 137–154)

```
def build_admin() -> FlagshipAdmin
```

**Purpose**: Creates the admin client used for writing Flagship flag values. Unlike the read provider, it fails loudly if required write credentials are missing, because an operator command should clearly explain why it cannot change anything.

**Data flow**: It reads the Cloudflare account id, Flagship app id, and write token from deploy environment values. It collects the names of any missing values. If anything is missing, it raises an error naming the required settings. If everything is present, it creates and returns a `FlagshipAdmin` with those credentials.

**Call relations**: This is the setup path for write-side tooling such as flag-setting commands. After it builds a `FlagshipAdmin`, that object’s `serve` or `list` methods can perform the actual Cloudflare API work.

*Call graph*: 2 external calls (__init__, deploy_env).


##### `manifest`  (lines 157–163)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the wider system: its name, version, needed deploy keys, and the feature-flag provider it can build. This is how the extension becomes discoverable without hard-coding it into the core.

**Data flow**: It creates a manifest containing the extension metadata, the three deploy keys needed for read-side Flagship access, and a flag-provider specification that points at `build`. The result is returned to whatever loads extensions.

**Call relations**: The extension loader reads this manifest to learn that this file supplies a Flagship-backed feature-flag provider. The provider specification inside the manifest points back to `build`, which is later used to create the actual OpenFeature provider at startup.

*Call graph*: 2 external calls (__init__, __init__).

## 📊 State Registers Touched

- `reg-effective-config` — The merged deployment settings that tell the service how to start, where storage is, and which runtime options are enabled.
- `reg-feature-flags` — The shared on/off switches used to safely change product behavior without changing code.
- `reg-pack-extension-registry` — The approved set of installed packs and extensions, including what tools, jobs, agents, hooks, providers, and surfaces they add.
- `reg-model-provider-catalog` — The shared list of available AI models and providers, including limits, prices, credentials, and adapter rules.
- `reg-runtime-fleet-claims` — The attendance and claim sheet for running service processes, including heartbeats, work ownership, and surface listener claims.
- `reg-sandbox-handles` — The durable handles and leases that let conversations reconnect to their sandbox, files, ports, hosted previews, and work directories.
- `reg-execution-environment-policy` — The shared rules for where commands and tools may run, such as local execution, Docker, cloud sandboxes, terminals, and browser sessions.
- `reg-egress-proxy-policy` — The network access rules and proxy state that decide which outside hosts can be reached and when secrets may be attached.
- `reg-billing-ledger-balance` — The shared money and usage record, including spend caps, model costs, sandbox and egress usage, prepaid balances, and export progress.
- `reg-database-connection-pools` — Process-global database engines, sessions, transaction handles, and connection pools shared by serving, workers, migrations, and cleanup code.
- `reg-deployment-artifact-state` — The built runtime bundle and sandbox image/client artifact state, including image recipe/version alignment and deployment preflight results used by startup and sandbox execution.
- `reg-product-census-telemetry` — Derived product analytics/census state summarizing workspace activity, onboarding progress, tool connections, and payment funnel status for dashboards.
- `reg-external-client-connection-pools` — Process-global HTTP/gRPC client sessions, proxy clients, DNS/TLS state, and connection pools used for model providers, connectors, cloud storage, and sandbox services.
- `reg-update-check-state` — Cached software/version update-check results, last-check timestamps, retry timing, and dismissed or shown update notices for CLI and service maintenance flows.
- `reg-service-worker-lifecycle-state` — Process-local supervisor state for background loops and workers, including async task handles, startup readiness, shutdown signals, and drain status not represented by durable job tables.
