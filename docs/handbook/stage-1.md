# Operator entrypoints, deployment recipes, and product selection  `stage-1`

This stage is the front door of the system. It runs before the main server or background jobs start, when an operator or deployment script decides what kind of UFO workspace to create and what it should include. The main command tool, ufoctl, turns human actions such as initializing, serving, inspecting, or repairing a workspace into concrete setup work. The bundle builder then packages a repeatable deployment with the app, configuration, extension lockfile, and sandbox client. The extension store is the catalog-and-shopping-cart layer: it finds extensions, pins them into the lockfile, and removes them when needed. The sandbox client helper simply locates the already-built client program and gives a clear error if it is missing.

The pack files are product recipes. They choose groups of extensions, skills, and services for local assistant use, hosted assistant use, billing development, evaluations, DSQA, GDPVal, and a small sample pack. Finally, the sandbox scripts build the agent runtime image and verify that its internet proxy path is safe before deployment proceeds.

## Files in this stage

### Operator commands and bundling
Operator-facing commands enter through ufoctl, which installs extensions, creates repeatable deploy bundles, and locates the sandbox client binary needed by those bundles.

### `core/src/ufo/cli.py`

`entrypoint` · `operator commands, startup, maintenance, and local development`

This file is a large command-line control panel for UFO. Without it, a newcomer would have to create config files by hand, run database setup manually, mint tokens themselves, poke database tables directly, and know which internal service to call for each task. The file uses Click, a Python library for building command-line commands, to group many operator actions under one `ufoctl` program.

The main flow starts by loading a local `.env` file, which is a simple file of secret settings. The `init` command then writes a default config if needed, creates developer secrets, applies database migrations, creates the first workspace and owner, and saves a local CLI login token. `serve` starts the runtime. `portal` opens the web portal and quietly hands the browser the saved token through a short-lived local web page, rather than putting the token in a URL.

Other command groups are maintenance tools: spending caps, prepaid balances, feature flags, spend reports, transcript-read audit logs, OAuth grants, encrypted credentials, extension install/remove/search, bundle creation, turn cancellation, and demo seeding. A recurring pattern is: load config, open the right database scope, do one focused operation, print a plain result, and always close database connections afterward.

#### Function details

##### `_ufoctl_dir`  (lines 117–119)

```
def _ufoctl_dir() -> Path
```

**Purpose**: Finds the private directory where this machine stores `ufoctl` state, such as the saved CLI token. It lets tests or special deployments override the location with an environment variable.

**Data flow**: It reads the `UFOCTL_DIR` environment variable. If it is set, that path is used; otherwise it builds the default path under the user's home directory, `~/.ufoctl`. It returns that path without creating it.

**Call relations**: `init` calls this when it needs to write the local token, and `portal` calls it when it needs to read that token back before opening the browser.

*Call graph*: called by 2 (init, portal); 2 external calls (Path, home).


##### `_dotenv_path`  (lines 122–123)

```
def _dotenv_path() -> Path
```

**Purpose**: Points to the `.env` file that sits beside the UFO config file. This keeps local secrets close to the config that names them.

**Data flow**: It asks the config system where `ufo.toml` lives, takes that file's directory, and appends `.env`. The result is a filesystem path.

**Call relations**: The environment-loading and secret-writing helpers call this so they all agree on the same `.env` location.

*Call graph*: called by 4 (_load_dotenv, _missing_deploy_keys, _write_dev_secrets, init); 1 external calls (config_path).


##### `_dotenv_pairs`  (lines 126–160)

```
def _dotenv_pairs(text: str) -> list[tuple[str, str]]
```

**Purpose**: Reads `.env` text and turns it into name/value pairs. It supports the small format this project expects, including quoted multi-line secrets like private keys.

**Data flow**: It receives the raw text of a `.env` file. It skips blank lines and comments, removes an optional `export`, strips matching quotes, joins multi-line quoted values, and returns a list of `(name, value)` pairs. If a quoted value never closes, it raises an error.

**Call relations**: `_load_dotenv`, `_missing_deploy_keys`, and `_write_dev_secrets` all rely on this parser so they interpret local secret files consistently.

*Call graph*: called by 3 (_load_dotenv, _missing_deploy_keys, _write_dev_secrets).


##### `_load_dotenv`  (lines 163–179)

```
def _load_dotenv() -> None
```

**Purpose**: Loads local secret settings into the current process before commands run. It also refuses dangerous bare provider-key names so UFO-specific secrets do not accidentally leak into every tool that reads `.env`.

**Data flow**: It finds the `.env` path, returns if the file does not exist, parses it into pairs, checks for reserved names like `OPENAI_API_KEY`, and then writes the approved names into `os.environ`. If a reserved name is found, it stops with a user-facing Click error.

**Call relations**: `main` calls this once at the start of every CLI invocation, so all subcommands see the same local environment.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (main); 1 external calls (ClickException).


##### `main`  (lines 183–185)

```
def main() -> None
```

**Purpose**: Defines the top-level `ufoctl` command group. It is the doorway through which all subcommands are reached.

**Data flow**: When the CLI starts, this function loads `.env` settings into the process. It does not return user data; it prepares the environment for whichever subcommand Click dispatches next.

**Call relations**: Click treats this as the root command. Its main job is to call `_load_dotenv` before commands like `init`, `serve`, `portal`, or `balance` do their work.

*Call graph*: calls 1 internal fn (_load_dotenv).


##### `_one_address`  (lines 188–193)

```
def _one_address(_ctx: click.Context, _param: click.Parameter, value: str) -> str
```

**Purpose**: Validates that an email option really looks like one local address at one domain. This catches owner-email typos before the system tries to create database records.

**Data flow**: It receives a command-line value, checks it with `email_domain`, and returns the original value if valid. If not, it raises a Click parameter error with a readable message.

**Call relations**: Click uses this as the callback for `init --email`, so bad owner addresses are rejected before `init` begins onboarding.

*Call graph*: 2 external calls (BadParameter, email_domain).


##### `init`  (lines 205–240)

```
def init(email: str, model: str, reasoning: ReasoningEffort) -> None
```

**Purpose**: Sets up a new UFO workspace for local use or first deployment. It writes default config, creates needed secrets, applies the database schema, creates the owner and default agent, and saves a CLI token.

**Data flow**: It receives the owner's email, model name, and reasoning setting from the command line. It creates `ufo.toml` if missing, writes missing development secrets to `.env`, creates a PostgreSQL system database if needed, applies migrations, runs onboarding, mints a long-lived local token, writes it under the `ufoctl` directory, and prints next steps or missing provider keys.

**Call relations**: This is one of the main user-facing commands. It coordinates helpers such as `_write_dev_secrets`, `_create_postgres_system_database`, `_onboard`, `_ufoctl_dir`, and `_missing_deploy_keys` so first setup is one command instead of many manual steps.

*Call graph*: calls 6 internal fn (_create_postgres_system_database, _dotenv_path, _missing_deploy_keys, _onboard, _ufoctl_dir, _write_dev_secrets); 7 external calls (run, ClickException, echo, mint_token, config_path, load_config, apply_migrations).


##### `_missing_deploy_keys`  (lines 243–259)

```
def _missing_deploy_keys(config: Config) -> tuple[str, ...]
```

**Purpose**: Finds extension provider keys that are declared but not currently available. It helps developers learn which optional secrets they still need before using features that depend on them.

**Data flow**: It loads active extension manifests, collects their declared deploy keys, reads names already present in `.env` and the process environment, and returns missing names in UFO-prefixed form such as `UFO_OPENAI_API_KEY`.

**Call relations**: `init` calls this after setup and reports missing keys as warnings. It does not block startup, because UFO can still run without optional provider features.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 1 external calls (load_manifests).


##### `_write_dev_secrets`  (lines 262–284)

```
def _write_dev_secrets(config: Config) -> tuple[str, ...]
```

**Purpose**: Creates local secret values that a zero-config development server needs. It avoids overwriting any secret the user already supplied.

**Data flow**: It builds fresh values for the credential encryption key, artifact token secret, and CLI bearer-token secret. It compares those names against existing `.env` entries and environment variables, appends only missing ones to `.env`, also puts them into `os.environ`, and returns the names it added.

**Call relations**: `init` calls this before onboarding so token minting and encrypted credential storage have the secrets they need.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 2 external calls (generate_key, token_urlsafe).


##### `_onboard`  (lines 287–307)

```
async def _onboard(config: Config, email: str, model: str, reasoning: ReasoningEffort) -> Onboarded
```

**Purpose**: Creates the initial workspace, owner, default agent, model setup, and extension onboarding data. It is the database-backed core of `ufoctl init`.

**Data flow**: It opens the database, optionally builds a credential store if the encryption key is present, creates an `Onboarding` object with config, owner email, model, reasoning, credentials, and extension manifests, then asks it to create core records and run extension steps. It returns an `Onboarded` result and always closes database resources afterward.

**Call relations**: `init` calls this after migrations are applied. This helper hands the actual creation work to the onboarding subsystem and protects the CLI from leaving database connections open.

*Call graph*: called by 1 (init); 6 external calls (__init__, __init__, Fernet, dispose_db, init_db, load_manifests).


##### `_create_postgres_system_database`  (lines 310–321)

```
async def _create_postgres_system_database(config: Config) -> None
```

**Purpose**: Creates UFO's separate PostgreSQL system database if it does not already exist. This makes first setup smoother for PostgreSQL-backed installs.

**Data flow**: It converts the configured async database URL into a plain PostgreSQL connection string, connects to the application database, checks whether the named system database exists, creates it if absent, and closes the connection.

**Call relations**: `init` calls this only when the configured database URL points at PostgreSQL. It prepares the database environment before migrations run.

*Call graph*: called by 1 (init); 1 external calls (connect).


##### `migrate`  (lines 325–342)

```
def migrate() -> None
```

**Purpose**: Brings the database schema up to date. A schema is the set of tables and columns the application expects.

**Data flow**: It loads config, chooses either an owner database URL from the environment or the normal configured database URL, applies core and extension migrations, and prints confirmation.

**Call relations**: Operators run this after installing extensions or during deployment. It delegates the actual migration work to `apply_migrations`.

*Call graph*: 3 external calls (echo, load_config, apply_migrations).


##### `_one_slug`  (lines 345–348)

```
def _one_slug(_ctx: click.Context, _param: click.Parameter, value: str) -> str
```

**Purpose**: Checks that a new migration name is safe snake_case text. This prevents odd filenames and inconsistent migration labels.

**Data flow**: It receives a command-line slug, matches it against the allowed pattern, and returns it if valid. If it does not match, it raises a Click parameter error.

**Call relations**: Click uses this as the validator for the `new-migration` command's slug argument.

*Call graph*: 1 external calls (BadParameter).


##### `new_migration`  (lines 353–370)

```
def new_migration(slug: str) -> None
```

**Purpose**: Creates a new empty core database migration file. Migrations are small scripts that change the database schema over time.

**Data flow**: It reads the current core migration head, creates a timestamp revision, writes a migration template file with that revision and previous head, then rewrites the `HEAD` marker to the new revision.

**Call relations**: Developers use this when changing core tables. It uses `contained_file` to safely write inside the migrations directory and `core_migration_head` to chain the new migration to the current one.

*Call graph*: 4 external calls (echo, now, core_migration_head, contained_file).


##### `serve`  (lines 374–383)

```
def serve() -> None
```

**Purpose**: Starts the UFO runtime: web surfaces, workers, jobs, and proxy-facing services. This is the command that makes the application actually run.

**Data flow**: It loads config and extension manifests, finds the pack's home browser surface if one exists, prints the portal URL, then calls the main server runner. It does not return until the server runner stops.

**Call relations**: This user-facing command bridges CLI setup to the serving subsystem. It calls `_serve_base` only to print the correct portal address.

*Call graph*: calls 1 internal fn (_serve_base); 5 external calls (echo, load_config, load_manifests, home_surface, run).


##### `portal`  (lines 387–405)

```
def portal() -> None
```

**Purpose**: Opens the UFO browser portal already signed in with this machine's CLI token. It saves the user from copying and pasting a token manually.

**Data flow**: It loads config and extension manifests, verifies that a portal surface exists, reads the saved token from the `ufoctl` directory, checks that the server is reachable, then creates a `BrowserHandoff` to open a temporary local page that posts the token to the portal.

**Call relations**: Users run this after `serve` is running. It uses `_serve_base` to choose the portal host and `BrowserHandoff` to safely pass the token to the browser.

*Call graph*: calls 2 internal fn (_serve_base, _ufoctl_dir); 7 external calls (__init__, ClickException, echo, get, load_config, load_manifests, home_surface).


##### `_serve_base`  (lines 408–415)

```
def _serve_base(config: Config) -> str
```

**Purpose**: Chooses the base URL users and browser sessions should use for the running server. This matters because browser cookies are tied to hostnames.

**Data flow**: It reads the config. If `connect.public_base_url` is set, it returns that public URL; otherwise it builds a local URL from the configured serve host and port.

**Call relations**: `serve` uses this for the URL it prints, and `portal` uses it for the URL it opens and reachability-checks.

*Call graph*: called by 2 (portal, serve).


##### `BrowserHandoff.open`  (lines 430–438)

```
def open(self) -> None
```

**Purpose**: Starts a one-request local web server that gives the browser a signed-in portal page. It is a safer handoff than placing the token directly in a URL.

**Data flow**: It creates a random unguessable path, starts an HTTP server on `127.0.0.1` using a generated responder, opens that local URL in the default browser, and serves requests until the token page has been delivered once.

**Call relations**: `portal` creates a `BrowserHandoff` and calls this. This method uses `_responder` to build the temporary request handler.

*Call graph*: calls 1 internal fn (_responder); 5 external calls (echo, HTTPServer, token_urlsafe, Event, open).


##### `BrowserHandoff._responder`  (lines 440–457)

```
def _responder(self, path: str, delivered: threading.Event) -> type[BaseHTTPRequestHandler]
```

**Purpose**: Builds the tiny HTTP request handler used during browser sign-in handoff. It knows which one secret path is allowed.

**Data flow**: It receives the random path and an event used to mark delivery. It pre-builds the HTML page, defines a handler class that serves that page only at the matching path, and returns that handler class.

**Call relations**: `BrowserHandoff.open` passes the returned handler to `HTTPServer`. The handler uses `_page` to get the form that posts the token to the portal.

*Call graph*: calls 1 internal fn (_page); called by 1 (open).


##### `BrowserHandoff._responder.do_GET`  (lines 444–453)

```
def do_GET(self) -> None
```

**Purpose**: Serves the one temporary handoff page to the browser. Any request for another path gets a not-found response.

**Data flow**: It reads the incoming HTTP path. If it is not the expected random path, it sends a 404 error. If it matches, it sends the HTML page, writes it to the response, and marks the delivery event as complete.

**Call relations**: The local `HTTPServer` calls this when the browser visits the temporary URL created by `BrowserHandoff.open`.


##### `BrowserHandoff._responder.log_message`  (lines 455–455)

```
def log_message(self, *args: object) -> None
```

**Purpose**: Suppresses normal HTTP server logging for the short-lived handoff server. This keeps token handoff quiet and avoids noisy console output.

**Data flow**: It receives log arguments from the standard HTTP server but intentionally does nothing. Nothing is returned and nothing is printed.

**Call relations**: The generated responder class includes this method so requests served during `BrowserHandoff.open` do not produce default request logs.


##### `BrowserHandoff._page`  (lines 459–466)

```
def _page(self) -> str
```

**Purpose**: Builds the HTML page that submits the CLI token to the portal. The token travels in a form body, not in the address bar.

**Data flow**: It reads the handoff object's portal URL and token, escapes them for safe HTML, and returns a small page with a hidden token field and JavaScript that submits the form automatically.

**Call relations**: `_responder` calls this before serving the temporary local page used by `BrowserHandoff.open`.

*Call graph*: called by 1 (_responder); 1 external calls (escape).


##### `ingress`  (lines 470–472)

```
def ingress() -> None
```

**Purpose**: Starts the sandbox ingress server, which is a guarded reverse proxy to ports inside conversation sandboxes. In plain terms, it is the controlled doorway into isolated workspaces.

**Data flow**: It takes no command-line data here. It simply hands control to the sandbox ingress runner.

**Call relations**: This is a top-level operator command. It delegates all real serving behavior to `ufo.harness.sandbox.ingress_serve.run`.

*Call graph*: 1 external calls (run).


##### `spend_cap`  (lines 476–477)

```
def spend_cap() -> None
```

**Purpose**: Defines the `spend-cap` command group for reading and setting limits on model spending. These caps help stop a workspace, member, or agent from spending too much in a time window.

**Data flow**: It receives no data and performs no action itself. It exists so Click can attach subcommands such as `set` and `list` underneath it.

**Call relations**: Click uses this group to route `ufoctl spend-cap set` and `ufoctl spend-cap list`.


##### `spend_cap_set`  (lines 488–506)

```
def spend_cap_set(scope: str, subject_id: str, window_seconds: int, limit_micro_usd: int, on_breach: str) -> None
```

**Purpose**: Creates or updates a spending cap for a workspace, member, or agent. It lets an operator define what should happen when the cap is reached.

**Data flow**: It receives scope, optional subject ID, window length, limit in micro-dollars, and breach behavior. It validates which scopes need a subject, converts IDs where needed, loads config, writes the cap through `_write_spend_cap`, and prints a dollar-formatted summary.

**Call relations**: This command is the user-facing wrapper around `_write_spend_cap`, which performs the database insert or update.

*Call graph*: calls 1 internal fn (_write_spend_cap); 5 external calls (run, ClickException, echo, load_config, UUID).


##### `spend_cap_list`  (lines 510–520)

```
def spend_cap_list() -> None
```

**Purpose**: Prints all spend caps currently set for the workspace. It gives operators a quick view of active limits.

**Data flow**: It loads config, reads cap rows through `_read_spend_caps`, and either prints that none exist or formats each cap with scope, subject, dollar limit, time window, and breach action.

**Call relations**: This command delegates database reading to `_read_spend_caps` and handles only presentation.

*Call graph*: calls 1 internal fn (_read_spend_caps); 3 external calls (run, echo, load_config).


##### `_write_spend_cap`  (lines 523–577)

```
async def _write_spend_cap(config: Config, scope: str, subject: UUID | None, window_seconds: int, limit_micro_usd: int, on_breach: str) -> UUID
```

**Purpose**: Writes the database record for a spend cap, updating an existing matching cap instead of duplicating it. This keeps one cap per matching scope, subject, and window.

**Data flow**: It opens the workspace database, finds the workspace ID, checks for an existing cap with the same scope, subject, and window, updates it if found, or inserts a new cap with a fresh UUID if not. It returns the cap ID and closes the database afterward.

**Call relations**: `spend_cap_set` calls this after validating command-line arguments. It uses the schema table definitions and workspace transaction helper to make the change safely.

*Call graph*: called by 1 (spend_cap_set); 7 external calls (insert, select, update, dispose_db, init_db, workspace_tx, uuid4).


##### `_read_spend_caps`  (lines 580–606)

```
async def _read_spend_caps(config: Config) -> list[tuple[UUID, str, UUID | None, int, int, str]]
```

**Purpose**: Reads all spend caps for the current workspace. It provides the raw data that the list command prints.

**Data flow**: It opens the database, finds the workspace ID, selects cap fields ordered by scope, converts database rows into simple tuples, returns them, and then disposes database resources.

**Call relations**: `spend_cap_list` calls this and turns the returned tuples into human-readable output.

*Call graph*: called by 1 (spend_cap_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `balance`  (lines 610–611)

```
def balance() -> None
```

**Purpose**: Defines the `balance` command group for prepaid workspace billing. It groups commands for showing, crediting, and reserving balance.

**Data flow**: It does no work directly. It exists as the Click parent for balance subcommands.

**Call relations**: Click uses this group to route `balance show`, `balance credit`, and `balance reserve`.


##### `balance_show`  (lines 616–631)

```
def balance_show(workspace_id: str) -> None
```

**Purpose**: Displays the current prepaid balance, reserve, and lifetime totals for a workspace. The reserve is the minimum headroom needed before a turn may start.

**Data flow**: It receives an optional workspace ID, loads config, reads the balance through `_read_balance`, and prints either `no balance` or formatted dollar amounts for balance, reserve, granted, charged, and last purchase time.

**Call relations**: This command wraps `_read_balance`, which opens the correct workspace scope and asks the billing subsystem for the balance.

*Call graph*: calls 1 internal fn (_read_balance); 3 external calls (run, echo, load_config).


##### `balance_credit`  (lines 639–652)

```
def balance_credit(granted_micro_usd: int, charged_micro_usd: int, reference: str, workspace_id: str) -> None
```

**Purpose**: Adds prepaid credit to a workspace, once per reference key. The reference makes the operation safe to retry without double-crediting.

**Data flow**: It receives granted amount, charged amount, reference, and optional workspace ID. It refuses a zero grant, loads config, calls `_credit_balance`, and prints either that credit was applied or that this reference was already used.

**Call relations**: This command is the human-facing wrapper around the billing `credit` operation reached through `_credit_balance`.

*Call graph*: calls 1 internal fn (_credit_balance); 4 external calls (run, ClickException, echo, load_config).


##### `balance_reserve`  (lines 658–666)

```
def balance_reserve(micro_usd: int, workspace_id: str) -> None
```

**Purpose**: Sets the minimum prepaid balance a workspace must have before a turn is admitted. This helps avoid starting work when there is not enough headroom.

**Data flow**: It receives a reserve amount and optional workspace ID, rejects negative values, loads config, calls `_set_reserve`, and prints the new reserve or errors if no balance exists yet.

**Call relations**: This command delegates the actual update to `_set_reserve`, which works inside the proper workspace database scope.

*Call graph*: calls 1 internal fn (_set_reserve); 4 external calls (run, ClickException, echo, load_config).


##### `_target_workspace`  (lines 669–691)

```
async def _target_workspace(named: str) -> UUID
```

**Purpose**: Chooses which workspace an operator command should act on. If no workspace is named, it only proceeds when there is exactly one workspace.

**Data flow**: Inside an owner-level database transaction, it either verifies the provided workspace UUID exists, or reads all workspace IDs. It returns the chosen UUID, or raises a clear error if there are zero, many, or no matching workspaces.

**Call relations**: `_balance_scope` and `_seed_target` call this before doing workspace-specific work. It prevents multi-workspace deployments from accidentally modifying the wrong workspace.

*Call graph*: called by 2 (_balance_scope, _seed_target); 4 external calls (ClickException, select, owner_tx, UUID).


##### `_balance_scope`  (lines 695–712)

```
async def _balance_scope(config: Config, named: str) -> AsyncIterator[tuple[AsyncConnection, UUID]]
```

**Purpose**: Opens the correct database context for balance commands. It is an async context manager, meaning it sets things up before the operation and reliably cleans up afterward.

**Data flow**: It initializes the app database and, if available, the owner database. It resolves the target workspace, pins the current execution to that workspace, opens a workspace transaction, yields the connection and workspace ID to the caller, then disposes database resources at the end.

**Call relations**: `_read_balance`, `_credit_balance`, and `_set_reserve` all use this so balance operations share the same safe workspace-selection behavior.

*Call graph*: calls 1 internal fn (_target_workspace); called by 3 (_credit_balance, _read_balance, _set_reserve); 5 external calls (dispose_db, init_db, init_owner_db, workspace_tx, ws).


##### `_read_balance`  (lines 715–717)

```
async def _read_balance(config: Config, named: str) -> Balance | None
```

**Purpose**: Reads the current billing balance for one workspace. It is the database helper behind `balance show`.

**Data flow**: It opens `_balance_scope`, receives a database connection and workspace ID, calls the billing `read_balance` function, and returns either a `Balance` object or `None`.

**Call relations**: `balance_show` calls this and formats the returned balance for the terminal.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_show); 1 external calls (read_balance).


##### `_credit_balance`  (lines 720–726)

```
async def _credit_balance(config: Config, named: str, granted_micro_usd: int, charged_micro_usd: int, reference: str) -> bool
```

**Purpose**: Applies a prepaid credit or adjustment to one workspace. It is the database helper behind `balance credit`.

**Data flow**: It opens `_balance_scope`, passes the connection, workspace ID, granted amount, charged amount, and reference to the billing `credit` function, and returns whether a new credit was applied.

**Call relations**: `balance_credit` calls this after validating that the grant amount is not zero.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_credit); 1 external calls (credit).


##### `_set_reserve`  (lines 729–731)

```
async def _set_reserve(config: Config, named: str, reserve_micro_usd: int) -> bool
```

**Purpose**: Updates the reserve amount for one workspace's prepaid balance. It is the database helper behind `balance reserve`.

**Data flow**: It opens `_balance_scope`, passes the connection, workspace ID, and new reserve amount to the billing `set_reserve` function, and returns whether the update succeeded.

**Call relations**: `balance_reserve` calls this after rejecting negative values.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_reserve); 1 external calls (set_reserve).


##### `flags`  (lines 738–743)

```
def flags() -> None
```

**Purpose**: Defines the `flags` command group for changing feature-flag values outside a deployment. A feature flag is a switch that lets code serve one behavior or another.

**Data flow**: It performs no direct data work. It provides the Click parent for flag subcommands.

**Call relations**: Click routes commands such as `ufoctl flags set` through this group.


##### `flags_set`  (lines 749–771)

```
def flags_set(key: str, on: bool) -> None
```

**Purpose**: Sets one declared feature flag on or off for the current environment. It refuses unknown flags so the flag service does not hold settings no active code reads.

**Data flow**: It receives a flag key and boolean value, loads config, verifies a flag backend is configured, loads extension manifests, checks that an active extension declares the key, imports the backend admin module, asks it to serve the value, and prints the result.

**Call relations**: This command bridges extension declarations to the configured flag backend. It dynamically imports the backend writer named by config.

*Call graph*: 5 external calls (ClickException, echo, import_module, load_config, load_manifests).


##### `spend`  (lines 779–802)

```
def spend(window_seconds: int) -> None
```

**Purpose**: Prints a spending report over a recent time window. It shows the total and then breaks cost down by useful categories such as member, agent, origin, and price version.

**Data flow**: It receives a window length in seconds, loads config, reads a `SpendReport` through `_read_spend`, converts micro-dollars to dollars, and prints each rollup section.

**Call relations**: This is the user-facing reporting command. `_read_spend` performs the database query through the billing accounting subsystem.

*Call graph*: calls 1 internal fn (_read_spend); 3 external calls (run, echo, load_config).


##### `_read_spend`  (lines 805–812)

```
async def _read_spend(config: Config, window_seconds: int) -> SpendReport
```

**Purpose**: Reads billing ledger rollups for the current workspace. A rollup is a summarized view of many individual ledger entries.

**Data flow**: It initializes the database, finds the workspace ID, creates a `SpendRollup` reader for that workspace, asks it to read the requested time window, returns the report, and closes database resources.

**Call relations**: `spend` calls this and then prints the returned report.

*Call graph*: called by 1 (spend); 5 external calls (__init__, select, dispose_db, init_db, workspace_tx).


##### `transcript_reads`  (lines 820–833)

```
def transcript_reads(limit: int) -> None
```

**Purpose**: Lists audit records for admin reads of another member's private transcript. This gives operators visibility into sensitive access disclosures.

**Data flow**: It receives a limit, rejects values below one, loads config, reads recent access records through `_read_transcript_accesses`, and prints either no records or one line per disclosure with reader, subject, conversation, and time.

**Call relations**: This command is the presentation layer for `_read_transcript_accesses`, which does the database join.

*Call graph*: calls 1 internal fn (_read_transcript_accesses); 4 external calls (run, ClickException, echo, load_config).


##### `_read_transcript_accesses`  (lines 836–870)

```
async def _read_transcript_accesses(config: Config, limit: int) -> list[tuple[str, str, UUID, datetime]]
```

**Purpose**: Fetches recent transcript-access audit records for the current workspace. It joins member records so emails can be shown instead of only IDs.

**Data flow**: It initializes the database, aliases the member table as reader and subject, finds the workspace ID, selects recent transcript access rows joined to both members, limits the result, converts rows into tuples, and disposes the database.

**Call relations**: `transcript_reads` calls this and formats the returned audit entries.

*Call graph*: called by 1 (transcript_reads); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `grants`  (lines 874–886)

```
def grants() -> None
```

**Purpose**: Lists OAuth account grants available to agents. OAuth is a standard way to let an app access another service account with permission.

**Data flow**: It loads config, reads grant summaries through `_read_grants`, and prints either `no grants` or one line per agent/provider/account with whether it is shared or private.

**Call relations**: This command delegates the actual grant lookup to `_read_grants`, then formats the result.

*Call graph*: calls 1 internal fn (_read_grants); 3 external calls (run, echo, load_config).


##### `_read_grants`  (lines 889–896)

```
async def _read_grants(config: Config) -> tuple[GrantSummary, ...]
```

**Purpose**: Reads summarized OAuth grants for the current workspace. It supplies the data behind the `grants` command.

**Data flow**: It initializes the database, finds the workspace ID in a workspace transaction, calls `workspace_grant_summaries`, returns the summaries, and disposes database resources.

**Call relations**: `grants` calls this and prints each `GrantSummary`.

*Call graph*: called by 1 (grants); 5 external calls (select, workspace_grant_summaries, dispose_db, init_db, workspace_tx).


##### `credential`  (lines 900–902)

```
def credential() -> None
```

**Purpose**: Defines the `credential` command group for operator-entered BYOK secrets. BYOK means “bring your own key,” where the user supplies a secret needed by an extension.

**Data flow**: It does not process data itself. It groups credential subcommands under Click.

**Call relations**: Click routes `credential set` and `credential list` through this group.


##### `credential_set`  (lines 907–930)

```
def credential_set(slot: str) -> None
```

**Purpose**: Stores one encrypted credential value for a declared slot. It reads the secret from a hidden prompt or standard input, never from command-line arguments.

**Data flow**: It receives a slot name, loads config, checks that the slot is declared and fillable by a member/operator, checks that the encryption key environment variable exists, reads the secret value, rejects empty input, writes it through `_write_credential`, and prints confirmation.

**Call relations**: This command uses `_declared_slots` and `_fillable_slots` for validation, then hands secure storage to `_write_credential`.

*Call graph*: calls 3 internal fn (_declared_slots, _fillable_slots, _write_credential); 5 external calls (run, ClickException, echo, prompt, load_config).


##### `credential_list`  (lines 934–944)

```
def credential_list() -> None
```

**Purpose**: Lists declared credential slots and whether each has a stored value. It never reads or prints the secret values themselves.

**Data flow**: It loads config, reads declared slots from extension manifests, reads stored slot names from the database, and prints each slot with its owning extension and `set` or `unset` status.

**Call relations**: This command combines manifest data from `_declared_slots` with database data from `_read_stored_slots`.

*Call graph*: calls 2 internal fn (_declared_slots, _read_stored_slots); 3 external calls (run, echo, load_config).


##### `_declared_slots`  (lines 947–952)

```
def _declared_slots(config: Config) -> dict[str, str]
```

**Purpose**: Collects credential slots declared by active extensions. This tells the CLI which secret names are valid.

**Data flow**: It loads extension manifests for the configured pack and returns a dictionary mapping each credential slot name to the extension that declared it. Manifest loading errors become user-facing Click errors.

**Call relations**: `credential_set` uses this to reject unknown slots, and `credential_list` uses it to decide what to display.

*Call graph*: called by 2 (credential_list, credential_set); 2 external calls (ClickException, load_manifests).


##### `_fillable_slots`  (lines 955–964)

```
def _fillable_slots(config: Config) -> frozenset[str]
```

**Purpose**: Finds which declared credential slots may be filled manually. Some slots are written by the deploy itself and should not accept typed values.

**Data flow**: It loads extension manifests, filters credential declarations to those marked `member_filled`, and returns their names as an immutable set. Manifest loading errors become Click errors.

**Call relations**: `credential_set` calls this after confirming the slot exists, so it can refuse slots that are not meant for manual entry.

*Call graph*: called by 1 (credential_set); 2 external calls (ClickException, load_manifests).


##### `_write_credential`  (lines 967–974)

```
async def _write_credential(config: Config, key: str, slot: str, value: str) -> None
```

**Purpose**: Encrypts and stores a credential value for the current workspace. It is the secure database helper behind `credential set`.

**Data flow**: It initializes the database, finds the current workspace ID, builds a `CredentialStore` using the provided Fernet encryption key, writes the slot value, and disposes database resources afterward.

**Call relations**: `credential_set` calls this only after validating the slot and reading the secret value safely.

*Call graph*: called by 1 (credential_set); 6 external calls (__init__, Fernet, select, dispose_db, init_db, workspace_tx).


##### `_read_stored_slots`  (lines 977–991)

```
async def _read_stored_slots(config: Config) -> frozenset[str]
```

**Purpose**: Reads which credential slots currently have values stored for the workspace. It returns names only, not secret contents.

**Data flow**: It opens the database, finds the workspace ID, selects slot names from the credential table for that workspace, returns them as an immutable set, and closes the database.

**Call relations**: `credential_list` calls this to decide whether each declared slot should be shown as set or unset.

*Call graph*: called by 1 (credential_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `ext`  (lines 995–996)

```
def ext() -> None
```

**Purpose**: Defines the `ext` command group for searching, installing, and removing extensions. Extensions add capabilities to a UFO pack.

**Data flow**: It performs no work directly. It is the Click parent for extension-store commands.

**Call relations**: Click routes `ext search`, `ext install`, and `ext remove` through this group.


##### `_store`  (lines 999–1002)

```
def _store(config: Config) -> ExtensionStore
```

**Purpose**: Builds an `ExtensionStore` object from config. The store knows the available extension catalog and the local lockfile of installed extensions.

**Data flow**: It checks that an extension store is configured, reads the catalog, finds the lockfile path, constructs an `ExtensionStore`, and returns it. If no store is configured, it raises a Click error.

**Call relations**: `ext_search`, `ext_install`, and `ext_remove` all call this so extension commands use the same catalog and lockfile.

*Call graph*: called by 3 (ext_install, ext_remove, ext_search); 4 external calls (__init__, ClickException, lockfile_path, read_catalog).


##### `ext_search`  (lines 1007–1020)

```
def ext_search(query: str) -> None
```

**Purpose**: Searches the configured extension catalog and shows matching extensions. It marks whether each result is installed, available, or bundle-only.

**Data flow**: It receives a query string, loads config, builds the extension store with `_store`, asks it to search, and prints matching listings or a no-match message.

**Call relations**: This command is the read-only extension discovery path. It relies on `_store` for catalog access.

*Call graph*: calls 1 internal fn (_store); 2 external calls (echo, load_config).


##### `ext_install`  (lines 1025–1031)

```
def ext_install(name: str) -> None
```

**Purpose**: Pins an extension from the store into the deploy lockfile. Pinning means recording the exact extension version and digest to load later.

**Data flow**: It receives an extension name, loads config, builds the store, asks it to install the name, catches validation errors as Click errors, and prints the pinned version and digest.

**Call relations**: This command changes what future `serve` runs will load. It delegates lockfile updates to the extension store returned by `_store`.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `ext_remove`  (lines 1036–1042)

```
def ext_remove(name: str) -> None
```

**Purpose**: Removes an extension pin from the lockfile. The next server run will stop loading that extension.

**Data flow**: It receives an extension name, loads config, builds the store, asks it to remove the extension, turns store errors into Click errors, and prints confirmation.

**Call relations**: This command is the inverse of `ext_install`, using `_store` to reach the lockfile-backed extension store.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `_ufo_project_dir`  (lines 1048–1059)

```
def _ufo_project_dir() -> Path
```

**Purpose**: Finds the source checkout of the UFO Python project so the bundle command can build a wheel. A wheel is a packaged Python distribution file.

**Data flow**: Starting from this file's location, it walks upward through parent directories, looks for `pyproject.toml`, parses it, and returns the ancestor whose project name is `ufo`. If none is found, it raises a Click error.

**Call relations**: `bundle` calls this before running `uv build`, ensuring bundles are made from the real source project rather than whatever directory the user happened to run from.

*Call graph*: called by 1 (bundle); 3 external calls (ClickException, Path, loads).


##### `bundle`  (lines 1071–1093)

```
def bundle(out: Path, client_binary: Path) -> None
```

**Purpose**: Builds a deployable bundle containing the UFO wheel, client binary, config, lockfile, and extension information. It freezes the current deploy setup into a runnable artifact.

**Data flow**: It receives an output directory and client binary path, loads config, optionally reads the extension catalog, builds the UFO wheel with `uv`, verifies the wheel exists, constructs a `Bundle`, asks it to build, then prints the bundle path and pinned extensions.

**Call relations**: This command ties together project discovery through `_ufo_project_dir`, external packaging through `subprocess.run`, and bundle assembly through the `Bundle` class.

*Call graph*: calls 1 internal fn (_ufo_project_dir); 8 external calls (__init__, ClickException, echo, run, wheel_name, config_path, load_config, read_catalog).


##### `turn`  (lines 1097–1098)

```
def turn() -> None
```

**Purpose**: Defines the `turn` command group for actions on a single conversation turn. A turn is one unit of agent work in a conversation.

**Data flow**: It does no direct work. It provides the Click parent for turn subcommands.

**Call relations**: Click routes `turn cancel` through this group.


##### `turn_cancel`  (lines 1104–1114)

```
def turn_cancel(turn_id: str, workspace_id: str) -> None
```

**Purpose**: Cancels one stuck or unwanted turn. It is an operator escape hatch for work that a normal user cannot finish or stop.

**Data flow**: It receives a turn ID and optional workspace ID, loads config, converts the turn ID to a UUID, calls `_cancel_turn`, and prints whether the turn was cancelled or was already terminal.

**Call relations**: This command delegates the hard parts, including workspace lookup and durable workflow cancellation, to `_cancel_turn`.

*Call graph*: calls 1 internal fn (_cancel_turn); 4 external calls (run, echo, load_config, UUID).


##### `_cancel_turn`  (lines 1117–1151)

```
async def _cancel_turn(config: Config, turn_id: UUID, named_workspace: str) -> bool
```

**Purpose**: Finds the workspace for a turn if needed, cancels its durable workflow, and records the cancelled result if the turn is not already terminal. Durable workflow means long-running work that can recover after process restarts.

**Data flow**: It initializes the database, chooses a workspace from the provided argument or by looking up the turn through the owner database, creates a replay-safe workflow client, pins execution to the workspace, optionally verifies the turn exists there, calls `cancel_one_turn`, returns whether cancellation happened, and disposes database resources.

**Call relations**: `turn_cancel` calls this. It coordinates database lookup, workspace context, and the cancellation subsystem.

*Call graph*: called by 1 (turn_cancel); 11 external calls (ClickException, select, dispose_db, init_db, init_owner_db, owner_tx, workspace_tx, replay_safe_client, cancel_one_turn, ws (+1 more)).


##### `seed`  (lines 1155–1156)

```
def seed() -> None
```

**Purpose**: Defines the `seed` command group for writing demonstration content into a workspace. Seed data gives designers and developers a known example to inspect.

**Data flow**: It performs no action itself. It is a Click grouping function.

**Call relations**: Click routes `seed kitchen-sink` through this group.


##### `seed_kitchen_sink`  (lines 1161–1170)

```
def seed_kitchen_sink(workspace_id: str) -> None
```

**Purpose**: Writes a demonstration conversation containing many shapes the portal UI can display. It then prints the portal route where the conversation can be viewed.

**Data flow**: It receives an optional workspace ID, loads config, calls `_seed_kitchen_sink`, gets back a conversation ID, and prints a web surface path for that conversation.

**Call relations**: This command is the user-facing wrapper around the seeding helpers. It lets designers and reviewers recreate the same demo content.

*Call graph*: calls 1 internal fn (_seed_kitchen_sink); 3 external calls (run, echo, load_config).


##### `_seed_kitchen_sink`  (lines 1173–1184)

```
async def _seed_kitchen_sink(config: Config, named: str) -> UUID
```

**Purpose**: Prepares database and blob storage access for writing the kitchen-sink demo conversation. Blob storage is where larger files or attachments live.

**Data flow**: It initializes the app database and, if available, the owner database, builds a workspace blob store from config, calls `_seed_target`, returns the new conversation ID, and disposes database resources.

**Call relations**: `seed_kitchen_sink` calls this. It handles setup and cleanup, while `_seed_target` does the workspace-specific data lookup and write.

*Call graph*: calls 1 internal fn (_seed_target); called by 1 (seed_kitchen_sink); 5 external calls (__init__, blob_store_for, dispose_db, init_db, init_owner_db).


##### `_seed_target`  (lines 1187–1218)

```
async def _seed_target(blob: WorkspaceBlobStore, named: str) -> UUID
```

**Purpose**: Writes the kitchen-sink demo into the selected workspace. It finds the main agent and first member, then asks the seed writer to create the conversation.

**Data flow**: It resolves the workspace with `_target_workspace`, pins execution to that workspace, reads the main agent ID and earliest member from the database, errors if no member exists, constructs a `KitchenSink` writer with blob storage and IDs, and returns the conversation ID it writes.

**Call relations**: `_seed_kitchen_sink` calls this after database and blob setup. It uses `_target_workspace` to avoid writing demo content into the wrong workspace.

*Call graph*: calls 1 internal fn (_target_workspace); called by 1 (_seed_kitchen_sink); 5 external calls (__init__, ClickException, select, workspace_tx, ws).


### `core/src/ufo/bundle.py`

`orchestration` · `bundle creation before deployment`

This file turns a local UFO deploy into something that can be built into a container image. Think of it like packing a lunchbox: instead of hoping the destination has the right ingredients, it copies in the exact recipe, the exact packaged food, and a checklist of what must be inside.

The bundle output is a Docker build context, which means a folder that `docker build` can use to create an image. It contains a Dockerfile, a copied `ufo.toml` config file, a generated `ufo.lock` lockfile, and the `ufo-sandbox-client` binary. The lockfile is important because it records which extensions should be active and includes a digest, which is a fingerprint of the extension files. At startup, those fingerprints can be checked so the running system is using the expected code.

The central class is `Bundle`. Its `build` method creates the output folder, copies the needed files, writes the lockfile, writes the Dockerfile, and returns a `BundleResult` describing what it produced. Before writing the lockfile, `_pins` decides which extensions must be pinned: either those already in the current lockfile, or all discovered installed extensions if no lockfile exists, plus any catalog entries marked as bundle-only. `_dockerfile` then writes the container recipe that installs the local UFO wheel and runs `ufoctl serve` by default.

#### Function details

##### `wheel_name`  (lines 35–38)

```
def wheel_name() -> str
```

**Purpose**: This returns the expected filename of the UFO wheel package that will be copied into the Docker build context. A wheel is Python’s installable package format, and this project expects the UFO wheel to be built locally rather than downloaded from a package index.

**Data flow**: It reads the current UFO version from `ufo_version()` and inserts that version into the standard wheel filename pattern. The output is a string such as a versioned `ufo-...-py3-none-any.whl` filename.

**Call relations**: The Dockerfile generator calls this when it needs to write the `COPY` and `pip install` lines. That keeps the Dockerfile aimed at the same version of UFO that the bundle is being built for.

*Call graph*: called by 1 (_dockerfile); 1 external calls (ufo_version).


##### `Bundle.build`  (lines 62–82)

```
def build(self) -> BundleResult
```

**Purpose**: This is the main bundle-building routine. It creates the output folder and fills it with everything needed to build a runnable UFO container image.

**Data flow**: It starts with the bundle settings stored on the `Bundle`: the source config path, output directory, UFO wheel path, sandbox client binary path, and optional extension catalog. It asks `_pins` for the exact extension list and fingerprints, copies the config and client binary into the output folder, writes a fresh lockfile using the current UFO version, writes the Dockerfile text from `_dockerfile`, and returns a `BundleResult` pointing to all generated files plus the chosen pins.

**Call relations**: This is the top-level action for this file. It calls `_pins` first because the lockfile must know the exact extension set, then calls `_dockerfile` to create the container recipe. It packages those results into `BundleResult` so the caller can report or use the generated artifact paths.

*Call graph*: calls 2 internal fn (_dockerfile, _pins); 3 external calls (__init__, __init__, ufo_version).


##### `Bundle._pins`  (lines 84–128)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: This decides which extensions belong in the bundle and records a fingerprint for each one. The fingerprint helps prove later that the container is running the same extension bytes that were bundled.

**Data flow**: It first discovers installed extensions in the current environment. If a lockfile already exists, it starts from the extension names listed there; otherwise it starts from all installed extensions. If an extension catalog is available, it also adds catalog entries marked as disabled, because those are installed only at bundle time. For each final name, it checks that the extension is installed, opens the UFO wheel, extracts the files belonging to that extension’s top-level package or module, ignores cache and compiled files, computes a content digest from those files, and returns a tuple of `ExtensionPin` records. If an expected extension or package is missing, it raises an error instead of making an incomplete bundle.

**Call relations**: `Bundle.build` calls this before writing the lockfile. It relies on the extension loader to discover installed extensions, find the current lockfile, read any existing pins, and compute content digests. It also reads directly from the wheel file so the pins describe the code that will be installed in the container, not just whatever source files may be present locally.

*Call graph*: called by 1 (build); 7 external calls (__init__, Path, discovered, extension_content_digest, lockfile_path, read_lockfile, ZipFile).


##### `Bundle._dockerfile`  (lines 130–146)

```
def _dockerfile(self) -> str
```

**Purpose**: This writes the text of the Dockerfile used to build the final runnable container image. The Dockerfile installs UFO, copies in the pinned config and lockfile, installs the sandbox client, and sets the default command to serve.

**Data flow**: It uses fixed bundle filenames and the wheel filename from `wheel_name()` to assemble a list of Dockerfile lines. The output is one string with newline-separated instructions: choose the Python base image, set the working directory, set environment variables for config and lockfile locations, install the UFO wheel, copy the config and lockfile, copy the client binary into a system path, and set `ufoctl serve` as the default run command.

**Call relations**: `Bundle.build` calls this after preparing the pins and copied files. It calls `wheel_name` so the Dockerfile refers to the same wheel filename convention used by the rest of the bundle process.

*Call graph*: calls 1 internal fn (wheel_name); called by 1 (build).


### `core/src/ufo/runtime/ext/store.py`

`domain_logic` · `extension command handling`

This file is the bridge between “extensions that exist” and “extensions this UFO installation should actually use.” The catalog is a TOML file, which is a human-readable settings file, listing extension names, versions, and whether an extension is disabled for normal install. The lockfile is the more exact record used at runtime: it pins each installed extension by name, version, and a digest, which is like a fingerprint of the extension’s source. That fingerprint helps make loading repeatable and safer.

The main object is ExtensionStore. It is given one catalog and one lockfile path. Its search method shows catalog matches and marks which ones are already pinned. Its install method checks that the requested extension is in the catalog, refuses catalog entries marked disabled, verifies that the Python package is actually installed in the current environment, then writes a pin into the lockfile. Its remove method deletes a pin from the lockfile.

A useful analogy is a music library versus a playlist. The catalog is the library of available songs. The lockfile is the playlist the player will actually use. This file lets commands search the library, add a song to the playlist, or remove it, while making sure the song is really available before saving it.

#### Function details

##### `read_catalog`  (lines 48–49)

```
def read_catalog(path: Path) -> Catalog
```

**Purpose**: Reads an extension catalog file from disk and turns it into a checked Catalog object. This is used when the program needs to know which extensions the store offers.

**Data flow**: It takes a file path as input. It reads the text from that path, parses the TOML text into plain data, then validates that the data has the expected catalog shape. The result is a Catalog object containing catalog entries.

**Call relations**: This is the entry point for loading the store’s list of available extensions. It relies on the path object to read the file and on TOML parsing to understand the file contents before the rest of the store logic can search or install from that catalog.

*Call graph*: 2 external calls (read_text, loads).


##### `ufo_version`  (lines 52–53)

```
def ufo_version() -> str
```

**Purpose**: Looks up the installed version of the UFO package. The lockfile uses this as an anchor so the extension pins are tied to a particular UFO version.

**Data flow**: It takes no direct input. It asks Python’s package metadata for the version of the installed package named "ufo". It returns that version as a string.

**Call relations**: ExtensionStore._write calls this when creating a new lockfile and there is no existing UFO version to preserve. In that moment, this function supplies the version label that gets written alongside the extension pins.

*Call graph*: called by 1 (_write); 1 external calls (version).


##### `pin_for`  (lines 56–63)

```
def pin_for(name: str) -> ExtensionPin
```

**Purpose**: Builds the exact lockfile pin for an extension that is installed in the current Python environment. It prevents the store from pinning a catalog name that is not actually available to load.

**Data flow**: It receives an extension name. It asks the extension loader what extensions have been discovered in the current environment, looks up the requested name, and fails with a clear error if it is missing. If found, it takes the extension’s manifest version and computes a digest, then returns an ExtensionPin containing the name, version, and digest.

**Call relations**: ExtensionStore.install calls this after confirming the requested name is allowed by the catalog. pin_for hands install the concrete pin that will be written to the lockfile, using discovery and digest logic from the extension loader.

*Call graph*: called by 1 (install); 3 external calls (__init__, discovered, extension_digest).


##### `ExtensionStore.search`  (lines 73–84)

```
def search(self, query: str) -> tuple[StoreListing, ...]
```

**Purpose**: Searches the catalog by name and reports which matching extensions are already installed according to the lockfile. It is what lets a user list possible extensions without changing anything.

**Data flow**: It takes a search string. It first reads the current pins from the lockfile, then walks through the catalog entries whose names contain the query text. For each match, it creates a StoreListing with the catalog name, version, disabled flag, and whether that name is already pinned. It returns all listings as a tuple.

**Call relations**: This method calls ExtensionStore._pins to learn the current installed set, then combines that with catalog data. It does not write anything; it simply prepares search results for higher-level command code to show to the user.

*Call graph*: calls 1 internal fn (_pins); 1 external calls (__init__).


##### `ExtensionStore.install`  (lines 86–96)

```
def install(self, name: str) -> ExtensionPin
```

**Purpose**: Installs an extension from the catalog by adding or replacing its pin in the lockfile. It enforces the store rules: the extension must be in the catalog, not disabled, and actually installed in the environment.

**Data flow**: It receives an extension name. It searches the catalog for that exact name. If the name is absent, it raises an error; if the catalog entry is disabled, it raises an error explaining that only bundling may pin it. Otherwise it asks pin_for to create the exact pin, removes any older pin for the same name from the current pins, writes the updated pin list, and returns the new pin.

**Call relations**: This is the main write path for adding extensions. It calls pin_for to prove and describe the installed extension, ExtensionStore._pins to preserve all other existing pins, and ExtensionStore._write to save the new lockfile contents.

*Call graph*: calls 3 internal fn (_pins, _write, pin_for).


##### `ExtensionStore.remove`  (lines 98–102)

```
def remove(self, name: str) -> None
```

**Purpose**: Removes an installed extension from the lockfile. This stops the loader from treating that extension as pinned for future runs.

**Data flow**: It receives an extension name. It reads the current lockfile pins and checks whether any pin has that name. If not, it raises an error because there is nothing to remove. If present, it filters that pin out and writes the remaining pins back to the lockfile.

**Call relations**: This is the counterpart to ExtensionStore.install. It calls ExtensionStore._pins to read the current state, then ExtensionStore._write to save the state after the requested extension has been removed.

*Call graph*: calls 2 internal fn (_pins, _write).


##### `ExtensionStore._pins`  (lines 104–105)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: Reads the current list of pinned extensions from the lockfile, or returns an empty list if the lockfile does not exist yet. It gives the other methods a single simple way to ask “what is installed right now?”

**Data flow**: It uses the store’s lockfile path. If the file exists, it reads and parses the lockfile and returns its extensions. If the file does not exist, it returns an empty tuple.

**Call relations**: Search, install, and remove all call this before deciding what to show or change. It delegates the actual lockfile parsing to the loader’s read_lockfile function, keeping file format details outside the higher-level store actions.

*Call graph*: called by 3 (install, remove, search); 1 external calls (read_lockfile).


##### `ExtensionStore._write`  (lines 107–111)

```
def _write(self, pins: tuple[ExtensionPin, ...]) -> None
```

**Purpose**: Writes a complete lockfile with the given extension pins. It preserves the existing UFO version anchor when possible, and uses the currently installed UFO version when creating a new lockfile.

**Data flow**: It receives the full tuple of pins that should be saved. It checks whether the lockfile already exists. If it does, it reads the existing UFO version from that file; if not, it calls ufo_version to get the current installed package version. It then builds a Lockfile object with that version and the provided pins, and writes it to disk.

**Call relations**: Install and remove call this after they have decided the new set of pins. This method is the final save step: it gathers the version anchor, creates the lockfile data object, and hands it to the loader’s write_lockfile function for disk output.

*Call graph*: calls 1 internal fn (ufo_version); called by 2 (install, remove); 3 external calls (__init__, read_lockfile, write_lockfile).


### `core/src/ufo/harness/sandbox/client_binary.py`

`util` · `startup or sandbox image preparation`

A sandbox needs a real `ufo` executable inside it, and the local carrier may also need to run that same command as a subprocess. This file is the shared “where do we get the binary?” lookup point, so different parts of the system do not invent their own paths or quietly use the wrong file.

It checks for the binary in a careful order. First, it looks at the `UFO_CLIENT_BINARY` environment variable. An environment variable is a setting passed in from the outside, often by a continuous integration job or deployment script. If that setting names a real file, this file trusts it and returns that path. If it names something missing, it fails immediately with a clear message.

If no override is given, it looks in the Rust client project’s normal build output folders, first for a release build and then for a debug build. If the caller asks for a specific Rust target triple, meaning a platform such as Linux on a certain CPU type, it searches under that target’s build folder. This matters because the machine building the sandbox image may not be the same kind of machine that will run inside the sandbox.

Finally, for host-only use, it checks whether `ufo` is already installed on the command path. If none of these work, it raises an error that explains the exact cargo build command to run. The important rule is: this file finds a binary; it never starts a slow build in the middle of another task.

#### Function details

##### `client_binary`  (lines 32–59)

```
def client_binary(target: str | None=None) -> Path
```

**Purpose**: Finds the `ufo` executable that should be used, either for the current machine or for a requested target platform. Someone uses it when they need a trustworthy path to an already-built client binary, such as before copying it into a sandbox or running it locally.

**Data flow**: It takes an optional `target` string, which names the platform the binary must run on. It first reads the `UFO_CLIENT_BINARY` environment setting; if that names a real file, it returns that path. If not, it searches the client build folders for release or debug versions, using the target-specific folder when a target is supplied. If no target is supplied, it also asks the operating system whether `ufo` is installed on the normal command path. If every search fails, it raises a `RuntimeError` with instructions for building the missing binary.

**Call relations**: This function is the single lookup step used by higher-level sandbox and carrier code when they need the client program. Inside its own work, it uses `pathlib.Path` to build and check file paths, and `shutil.which` to ask the operating system whether a host-installed `ufo` command exists. It hands back a path when the search succeeds, or stops the flow with a clear error when no usable binary can be found.

*Call graph*: 2 external calls (Path, which).


### Assistant product packs
These packs select the local, billing-enabled, evaluation-oriented, and hosted shapes of the assistant product.

### `packs/assistant_billing/ufo_pack_assistant_billing.py`

`config` · `startup / pack selection`

Most local assistant setups do not include billing, because sending real usage data to a billing vendor is risky and unnecessary during everyday development. But one important user path, the hosted onboarding action called “Set up billing,” needs the billing service to be present. Without this pack, that path could not be proven end to end on a developer machine.

This file creates a middle-ground pack named `assistant_billing`. It starts with everything from the normal assistant pack, then adds the `metronome` extension. Metronome is the external billing/usage platform used by the system. Think of this file like a custom lunch order: “give me the standard assistant meal, plus the billing side dish.”

Because this pack can send usage and seat information to Metronome, it is deliberately opt-in rather than the default. A developer selects it by setting the pack name to `assistant_billing`, typically through the local Docker Compose environment. To use it safely, the surrounding configuration should point to sandbox or test credentials, not production billing keys.

#### Function details

##### `pack`  (lines 24–33)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the pack definition for the local assistant-with-billing setup. Someone would use this when they want the normal assistant features plus Metronome billing support in a local environment.

**Data flow**: It reads the fixed pack name, version, the assistant pack’s existing extensions, and the assistant pack’s skill names. It adds `metronome` to the extension list, turns each assistant skill path into a `SkillSpec`, and returns a `Pack` object that describes the complete runnable bundle.

**Call relations**: When the pack system asks this module for its pack, this function assembles the answer. It hands each skill path to `SkillSpec` so the skill can be described in the expected format, then hands the name, version, extensions, and skills to `Pack` so the rest of the system can load this assistant-plus-billing bundle.

*Call graph*: 2 external calls (__init__, __init__).


### `packs/assistant_dev/ufo_pack_assistant.py`

`config` · `startup/config load`

A “pack” is like a pre-packed toolbox. Instead of asking an operator to enable memory, web research, browser tools, document tools, app modules, model providers, connectors, and debugging tools one by one, this file gives that whole collection one name: “assistant”. When the system loads this pack, it reads the list of extension names and activates exactly those pieces.

The file is mostly declarative, meaning it states facts rather than doing complex work. It names the pack, gives it a version, lists all included extensions, and points to a local skills folder. The extensions cover many assistant abilities: durable memory, search and research, task and objective tracking, browser automation, coding support, document generation, connectors to outside services, feature flags, model providers, and several built-in member apps.

The important behavior is in the `pack` function. It builds and returns a `Pack` object, which is the standard manifest object the rest of the system knows how to read. It also wraps the configured skill folder, currently `first-run`, as a `SkillSpec`, so the pack can include that skill alongside its extensions. Without this file, the assistant deployment would not have a single simple pack name that reliably turns on this intended set of local capabilities.

#### Function details

##### `pack`  (lines 72–78)

```
def pack() -> Pack
```

**Purpose**: Creates the manifest object for the “assistant” pack. The system uses this when it needs to know the pack’s name, version, included extensions, and included skill folders.

**Data flow**: It starts with constants defined in this file: the pack name, version, extension list, skills directory, and skill names. It turns each skill name into a `SkillSpec`, which is a small description of where that skill lives on disk, then places those together with the extension list into a `Pack` object. The result is a complete pack description returned to the loader; it does not directly start the extensions itself.

**Call relations**: When the pack loader asks this module for its pack definition, `pack` is the function that answers. It hands the skill paths to `SkillSpec` so they are represented in the expected manifest format, then hands the final name, version, extensions, and skills to `Pack` so the wider system can activate the bundle consistently.

*Call graph*: 2 external calls (__init__, __init__).


### `packs/assistant_eval/ufo_pack_assistant_eval.py`

`config` · `startup/config load`

This file is a small pack definition for evaluation runs. A pack is like a menu of extensions the system is allowed to load. For normal product use, the assistant pack may include connectors for real external broker services such as Composio or Pipedream. In an evaluation setup, those services are not available because there are no real API keys, and listing fake or unusable tools can confuse the agent and waste conversation turns. So this file deliberately filters those real broker extensions out.

After removing those live broker connectors, it adds two evaluation-focused extensions: `eval_env`, which represents the deterministic evaluation workplace tools, and `docker`, which lets the sandbox run a conversation’s `/workspace` as a real bind-mounted directory. In plain terms, this makes evaluation runs behave more like a real working folder inside a container instead of pretending through rewritten command paths.

The important idea is separation. These fake or evaluation-only providers should not appear in the normal product pack, because registry entries can show up even when the user has not granted access. By putting them in a separate `assistant_eval` pack, evaluation deployments can opt into them explicitly without affecting real deployments.

#### Function details

##### `pack`  (lines 26–27)

```
def pack() -> Pack
```

**Purpose**: This function builds and returns the pack description for the evaluation assistant setup. The system uses it to learn the pack’s name, version, and the exact set of extensions it should load.

**Data flow**: It takes no caller-provided input. It reads the constants defined in this file: the pack name, version, and the prepared extension list that starts from the normal assistant extensions, removes real broker connectors, and adds evaluation-specific ones. It then creates and returns a `Pack` object containing that information.

**Call relations**: When the pack registry or loader asks this module what it provides, this function is the handoff point. It calls `Pack.__init__` to package the name, version, and extension list into the standard manifest object that the rest of the system can consume.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_hosted/ufo_pack_assistant_hosted.py`

`config` · `config load`

This file is a compact configuration recipe for a hosted assistant deployment. A “pack” is a bundle of capabilities the system can activate together, like choosing a trim package for a car: it decides which tools, integrations, model providers, storage backends, and built-in skills are available.

The file names the pack, gives it a version, and lists many extensions to enable. These include customer-facing surfaces like chat, Slack, iMessage, browser tools, document generation, scheduled tasks, and the member web portal. It also chooses hosted infrastructure pieces, such as Turbopuffer for memory search, Redis for live updates, E2B for code sandboxes, Browserbase for hosted browser sessions, and external model providers like Bedrock and OpenRouter.

It also adds a special prompt section that tells the assistant how to answer questions about the ufo product itself. When a paying customer asks about signup, Slack installation, billing, seats, limits, or missing features, the assistant should first use the shipped `customer-onboarding-help` skill instead of guessing or relying on a customer workspace’s own memory.

Finally, the pack combines hosted-only skills with the standard assistant skills from the base assistant pack. Without this file, the system would not have a single clear definition of what “hosted assistant” means or which managed services and product-support behavior should be enabled.

#### Function details

##### `pack`  (lines 99–112)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the complete `assistant_hosted` pack definition. Other parts of the system use this to learn which extensions, skills, and prompt instructions should be active for the hosted assistant.

**Data flow**: It starts with constants defined in this file: the pack name, version, enabled extension names, the local skills folder, and the product-support prompt section. It creates skill entries for the hosted-only skills, then adds skill entries from the base assistant pack. It packages all of that into a `Pack` object, which is the final description the rest of the system can load.

**Call relations**: When the pack system asks this module for its pack, `pack` assembles the answer. It hands each skill path to `SkillSpec` so the system knows where to find that skill on disk, then hands the full list of extensions, skills, and prompt sections to `Pack` so the hosted assistant can be activated as one coherent bundle.

*Call graph*: 2 external calls (__init__, __init__).


### Evaluation and sample packs
These packs provide predefined capability bundles for DSQA, GDPVal, and a minimal sample pack that exercises pack activation end to end.

### `packs/dsqa_eval/ufo_pack_dsqa_eval.py`

`config` · `startup or pack discovery`

This file is a small menu of capability bundles for DSQA evaluation. DSQA likely means a document or dataset question-answering workflow, where the system may need to index information, create embeddings, search the web, or use a browser. Instead of making every caller remember the exact list of extensions needed for each mode, this file gives each bundle a clear name.

There are three levels. The core pack includes the basic building blocks: a default index, OpenAI embeddings, and OpenRouter model access. The search pack builds on that by adding Perplexity and research-related extensions, so the system can look beyond its local indexed knowledge. The browser pack builds on the search pack again by adding browser and Chrome sandbox support, which is useful when the workflow needs to open and interact with web pages.

The file works like a set of labeled toolboxes. Each function returns a `Pack` object with a name, version, and extension list. If this file were missing, other parts of the system would have to duplicate these extension lists or guess which tools belong together, making DSQA evaluation setup more fragile and inconsistent.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the smallest DSQA evaluation pack. Someone would use this when they only need the basic indexing, embedding, and model-access pieces, without web search or browser tools.

**Data flow**: It starts with the fixed core name, version, and core extension list defined near the top of the file. It passes those values into `Pack`, which turns them into a pack object. The result is returned to the caller as a ready-to-register bundle.

**Call relations**: When something asks for the core DSQA pack, this function builds it by calling `Pack.__init__`. It does not call any other project logic; its job is simply to hand back the correctly named bundle with the core extensions attached.

*Call graph*: 1 external calls (__init__).


##### `search_pack`  (lines 17–18)

```
def search_pack() -> Pack
```

**Purpose**: Creates a DSQA evaluation pack that includes search and research tools in addition to the basic core tools. Someone would use this when the evaluation needs outside information lookup, not just local indexing and model access.

**Data flow**: It reads the search pack name, shared version, and search extension list from the file constants. That list includes the base extensions plus search-related additions. It gives those values to `Pack`, and returns the finished pack object.

**Call relations**: When the system or a pack loader wants the search-capable DSQA setup, this function is the small factory that produces it. Its one handoff is to `Pack.__init__`, which receives the name, version, and extensions and creates the actual pack object.

*Call graph*: 1 external calls (__init__).


##### `browser_pack`  (lines 21–22)

```
def browser_pack() -> Pack
```

**Purpose**: Creates the most capable DSQA evaluation pack in this file, including browser automation tools on top of search and core tools. Someone would use this when the workflow needs to visit or interact with web pages during evaluation.

**Data flow**: It takes the browser pack name, shared version, and browser extension list from the module constants. The browser list contains the search extensions plus browser and sandboxed Chrome support. It passes everything into `Pack`, then returns the resulting pack object.

**Call relations**: When a caller needs the full browser-enabled DSQA pack, this function packages that choice in one place. It calls `Pack.__init__` to create the object, then hands that pack back to whatever part of the system is collecting or registering available packs.

*Call graph*: 1 external calls (__init__).


### `packs/gdpval_eval/ufo_pack_gdpval_eval.py`

`config` · `config load`

A pack is a named bundle of extensions, where an extension is an add-on capability the UFO system can load, such as document tools, a browser, or a model provider. This file is like a menu with four preset meals: core, documents, research, and full. Each preset has the same version number and a clear name, so other parts of the system can request the right bundle consistently.

The core pack includes the basic pieces needed for indexing, embeddings, and model access. The documents pack starts with that core and adds tools for working with documents, a REPL-style interactive tool, and coding support. The research pack also starts with the core but adds web and research tools, including browser and sandboxed Chrome support. The full pack combines everything.

Without this file, callers would have to manually rebuild these extension combinations each time, which would be easy to get wrong. Centralizing the names and extension lists keeps GDPVal evaluation setups predictable and easier to update.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the smallest GDPVal evaluation pack. Someone would use this when they only need the base indexing, embedding, and model-access capabilities.

**Data flow**: It takes no input from the caller. It reads the file’s shared constants for the core pack name, version, and base extension list, then builds and returns a Pack object containing those values.

**Call relations**: When another part of the system wants the basic GDPVal setup, it calls this function. The function immediately hands the chosen name, version, and extensions to Pack.__init__ so the SDK can create the actual pack object.

*Call graph*: 1 external calls (__init__).


##### `documents_pack`  (lines 17–22)

```
def documents_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack aimed at document-heavy work. It is useful when the evaluation needs the base tools plus document, interactive, and coding-related capabilities.

**Data flow**: It takes no caller input. It combines the shared base extension list with the document-specific extension list, then returns a Pack object with the documents pack name, the shared version, and the combined extensions.

**Call relations**: A caller chooses this function when it needs document tools in addition to the core setup. The function prepares the combined list and passes it to Pack.__init__, which turns that configuration into a usable pack.

*Call graph*: 1 external calls (__init__).


##### `research_pack`  (lines 25–30)

```
def research_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack aimed at research and web-based work. It is useful when the evaluation needs tools such as research search, browsing, and a sandboxed browser environment.

**Data flow**: It receives no input. It joins the base extensions with the research-specific extensions, then returns a Pack object named for the research preset and marked with the shared version.

**Call relations**: Other code calls this when it wants the research-focused setup. This function does the simple assembly step, then delegates creation of the Pack object to Pack.__init__.

*Call graph*: 1 external calls (__init__).


##### `full_pack`  (lines 33–38)

```
def full_pack() -> Pack
```

**Purpose**: Creates the most complete GDPVal evaluation pack. It includes the base tools, document tools, and research tools together.

**Data flow**: It takes no arguments. It reads all three extension groups from this file, combines them in order, and returns a Pack object with the full pack name and shared version.

**Call relations**: A caller uses this when it wants every GDPVal evaluation capability available from this file. The function gathers all extension names and hands them to Pack.__init__, which produces the final pack object.

*Call graph*: 1 external calls (__init__).


### `packs/sample_pack/ufo_pack_sample.py`

`config` · `pack discovery and onboarding`

This is a conformance sample: a simple, installed pack whose job is to exercise the same public pack interface that real users rely on. Think of it like a test plug-in that is still a real plug-in, not a pretend one. If the pack-loading seam breaks, this file helps reveal that because it uses only the public `ufo.sdk` surface.

The file names the pack, gives it a version, points to a bundled extension called `sample`, and points to a skill folder on disk. It also defines an onboarding step, which is a setup action that runs when the pack is brought into use. That step writes `{"pack_onboarded": True}` into the pack’s scoped store. The store is durable project storage, so the conformance check can later read the value back through the same public path that normal code would use.

The main entry point is `pack()`. It builds and returns a `Pack` object describing everything this pack contributes: the bundled extension, the skill, and the onboarding step. Without this file, the sample pack would not advertise its contents, and the system would lose an important real-world check that pack discovery, skill registration, onboarding, and storage all still work together.

#### Function details

##### `_setup`  (lines 25–26)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: This is the onboarding action for the sample pack. It records, in the pack’s durable store, that the pack setup has run.

**Data flow**: It receives an `ExtensionContext`, which is the runtime object that gives the pack access to its scoped services. It uses the context’s store to save the key `pack:onboarded` with the value `{"pack_onboarded": True}`. Nothing is returned; the lasting result is the stored record.

**Call relations**: This function is handed to an `OnboardingStep` by `pack()`. Later, when the system runs the pack’s onboarding step, this function is called so the conformance test can confirm that onboarding wrote to real storage rather than to a fake log.


##### `pack`  (lines 29–36)

```
def pack() -> Pack
```

**Purpose**: This is the pack entry point. It tells the UFO pack loader what this sample pack is called, what version it is, which extension it bundles, which skill it adds, and which setup step to run.

**Data flow**: It reads the constants defined in the file, including the pack name, version, bundled extension name, skill folder path, onboarding step name, and setup function. It wraps the skill path in a `SkillSpec`, wraps `_setup` in an `OnboardingStep`, and returns a `Pack` object containing the complete description.

**Call relations**: The pack-loading system calls this function when it discovers the installed sample pack. Inside, it creates the `SkillSpec`, `OnboardingStep`, and `Pack` objects that describe the pack’s contributions, then hands that finished `Pack` back to the loader so the extension, skill, and onboarding step can be activated.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Sandbox image validation
Sandbox deployment scripts build the agent runtime image and verify that its outbound proxy path is safe before use.

### `sandbox/build_template.py`

`entrypoint` · `build and deploy time`

This script is the build tool for UFO's execution sandbox: the prepared Linux environment where agent work happens. Without it, new sandboxes might be missing basic tools like Python, Node, Chromium, LibreOffice, PDF utilities, the compiled `ufo` command, or the bundled system skills the agent expects. That would cause failures later, during real agent runs, in ways that are much harder to diagnose.

The file treats the sandbox like a packed travel kit. It starts from a base image, installs operating-system packages, Python packages, and Node packages, bakes in the UFO client binary, adds shared helper modules, unpacks the system skills bundle, sets environment variables, and records a fingerprint of the exact recipe used. That fingerprint is a digest: a short hash that changes when important inputs change.

It supports several modes. With no arguments, it stages the needed artifacts, builds E2B templates for each size tier, publishes them, and boots each one to verify the tools are really present. With `--check`, it does not publish; it boots the live templates and compares their baked digest to the current source recipe. With `--dockerfile`, it prints the equivalent Dockerfile. With `--build-docker`, it builds the local Docker image. The important idea is that E2B and Docker share one definition, so cloud and local sandboxes stay aligned.

#### Function details

##### `template_name`  (lines 259–260)

```
def template_name(size: str) -> str
```

**Purpose**: Creates the published E2B template name for a sandbox size, such as small, medium, or large. This gives every size tier its own named template.

**Data flow**: It receives a size label as text, attaches it to the common sandbox template prefix, and returns the full template name. It does not read or change anything else.

**Call relations**: The command-line flow uses this when checking or publishing each size tier. It turns the size being processed into the external name that E2B expects.

*Call graph*: called by 1 (main).


##### `client_definition`  (lines 263–289)

```
def client_definition() -> dict[str, str]
```

**Purpose**: Describes the compiled `ufo` client in a stable way for the build fingerprint. It hashes the client source files instead of the finished binary, because compiled binaries may differ slightly between machines even when built from the same code.

**Data flow**: It reads the important Rust client project files and source directories, feeds their paths and bytes into a SHA-256 hash, and returns a small dictionary with the binary name, target platform, and source hash. Nothing is written to disk.

**Call relations**: The build digest calculation calls this so the sandbox fingerprint changes when the client source changes. It hands that client identity back to the digest builder, which includes it in the overall recipe hash.

*Call graph*: called by 1 (build_definition_digest); 1 external calls (sha256).


##### `stage_client_binary`  (lines 292–303)

```
def stage_client_binary() -> Path
```

**Purpose**: Copies the already-built `ufo` client binary into a known build-context folder so Docker or E2B can copy it into the image. This avoids building Rust inside the sandbox image itself, which would be slow and heavy.

**Data flow**: It asks the client build helper where the right compiled binary is, creates the staging directory if needed, copies the binary there, marks it executable, and returns the staged path. The visible change is a fresh executable file under `sandbox/artifacts`.

**Call relations**: The Docker-image build path and the normal publish path call this before building an image. Later, the shared layer recipe copies this staged file into `/usr/local/bin` inside the sandbox.

*Call graph*: called by 2 (build_docker_image, main); 2 external calls (copyfile, client_binary).


##### `system_skill_bundle`  (lines 307–322)

```
def system_skill_bundle() -> SystemSkillBundle
```

**Purpose**: Collects all built-in system skills and packages them into one bundle object. A skill is a prepared capability, described by a `SKILL.md` file, that the agent can use inside the sandbox.

**Data flow**: It searches the main project, extensions, and packs for skill definition files, chooses the top-level skill folders, discovers the skills inside them, and creates a `SystemSkillBundle`. Because it is cached, repeated calls reuse the same bundle rather than rebuilding it.

**Call relations**: The staging step calls this to write the skill archive, and the digest step calls it to include the bundle identity in the build fingerprint. It relies on the skill discovery code and then hands a complete bundle to the image-building flow.

*Call graph*: calls 1 internal fn (from_skills); called by 2 (build_definition_digest, stage_system_skills); 1 external calls (discover_skills).


##### `stage_system_skills`  (lines 325–328)

```
def stage_system_skills() -> Path
```

**Purpose**: Writes the bundled system skills into the build context as a zip file. This gives the image build a single file to copy and unpack inside the sandbox.

**Data flow**: It creates the artifact directory if needed, gets the archive bytes from `system_skill_bundle`, writes them to `system-skills.zip`, and returns that path. The main effect is a staged zip file on disk.

**Call relations**: The Docker build path and normal publish path call this before image construction. The shared layer recipe later copies that zip into the image and unpacks it into the sandbox's system skills directory.

*Call graph*: calls 1 internal fn (system_skill_bundle); called by 2 (build_docker_image, main).


##### `build_definition_digest`  (lines 331–373)

```
def build_definition_digest(sizing: Sizing | None) -> str
```

**Purpose**: Creates the fingerprint for a sandbox build recipe. This is the drift detector: if the source recipe changes but the live template was not republished, the digest will not match.

**Data flow**: It receives either a size setting or `None` for Docker, gathers the base image/template, package lists, environment variables, start and readiness commands, runtime directory settings, client source identity, system skill digest, and helper module hashes. It serializes that information in a consistent order, hashes it, and returns a `sha256:...` string.

**Call relations**: The E2B template builder and Dockerfile builder call this before applying layers so the digest can be baked into the image. The check mode also calls it to compare the current source recipe with the digest stored in a live template.

*Call graph*: calls 2 internal fn (client_definition, system_skill_bundle); called by 3 (e2b_template, main, pod_dockerfile); 2 external calls (sha256, dumps).


##### `apply_layers`  (lines 376–421)

```
def apply_layers(builder: TemplateBuilder, digest: str) -> TemplateFinal
```

**Purpose**: Adds the shared sandbox contents to a template builder. This is the central recipe that keeps the E2B template and Docker image in sync.

**Data flow**: It receives a template builder and a build digest. It switches to the build user, installs system tools, GitHub CLI, Node, Python packages, Node packages, and Playwright's browser; creates runtime directories; writes the digest into the image; sets environment variables; copies and unpacks system skills; copies the `ufo` binary and helper modules; switches back to the runtime user; and returns the finalized template with its start and readiness commands set.

**Call relations**: Both `e2b_template` and `pod_dockerfile` call this after choosing their different base. It hands all detailed image-building instructions to the E2B SDK builder, which later turns them into either a published template or a Dockerfile.

*Call graph*: called by 2 (e2b_template, pod_dockerfile); 5 external calls (copy, run_cmd, set_envs, set_start_cmd, set_user).


##### `e2b_template`  (lines 424–426)

```
def e2b_template(size: str) -> TemplateFinal
```

**Purpose**: Builds the E2B version of the sandbox definition for one size tier. E2B is the cloud sandbox service used to run isolated agent environments.

**Data flow**: It receives a size name, starts an E2B template builder from the E2B base template, computes the digest for that size's CPU and memory allocation, applies the shared layers, and returns the final template definition. It does not publish by itself.

**Call relations**: The main publish flow calls this for each sandbox size before asking E2B to build it. It delegates the common image contents to `apply_layers` and the drift fingerprint to `build_definition_digest`.

*Call graph*: calls 2 internal fn (apply_layers, build_definition_digest); called by 1 (main); 1 external calls (Template).


##### `pod_dockerfile`  (lines 429–431)

```
def pod_dockerfile() -> str
```

**Purpose**: Produces the Dockerfile for the local Docker-carrier version of the sandbox. This lets the same sandbox recipe be built without an E2B account.

**Data flow**: It starts a template builder from the public Docker base image, computes a digest without E2B sizing information, applies the shared layers, converts the result into Dockerfile text, and returns that text.

**Call relations**: The command-line `--dockerfile` mode prints this result directly. The Docker build mode calls it and sends the Dockerfile text into `docker build`.

*Call graph*: calls 2 internal fn (apply_layers, build_definition_digest); called by 2 (build_docker_image, main); 2 external calls (Template, to_dockerfile).


##### `build_docker_image`  (lines 434–447)

```
def build_docker_image() -> None
```

**Purpose**: Builds the local Docker sandbox image from the shared recipe. This is used when the project wants a Docker-run sandbox instead of an E2B cloud template.

**Data flow**: It stages the client binary and system skills, renders the Dockerfile text, runs `docker build` with the repository root as the build context, and tags the image with the expected name. If Docker fails, it stops the script with an error; if it succeeds, it prints the image tag.

**Call relations**: The main command-line flow calls this for `--build-docker`. It first prepares the files that `apply_layers` expects to copy, then hands the rendered Dockerfile to the local Docker daemon.

*Call graph*: calls 3 internal fn (pod_dockerfile, stage_client_binary, stage_system_skills); called by 1 (main); 1 external calls (run).


##### `verify_published_template`  (lines 450–465)

```
def verify_published_template(name: str) -> None
```

**Purpose**: Boots a freshly published E2B template and checks that the required tools are really installed. This prevents a broken image from being treated as successfully published.

**Data flow**: It receives a template reference, creates a temporary sandbox from it, runs the readiness command inside that sandbox, always shuts the sandbox down afterward, and raises an error if the command fails or reports a nonzero exit code. It returns nothing on success.

**Call relations**: After the main publish flow builds each E2B template, it calls this as a publish gate. It uses the same readiness command that is baked into the template, so the final check matches what real sandbox startup depends on.

*Call graph*: called by 1 (main); 1 external calls (create).


##### `check_published_template`  (lines 468–488)

```
def check_published_template(name: str, expected: str) -> None
```

**Purpose**: Checks whether a live E2B template still matches the current source recipe, without publishing anything. This is the safe CI gate for detecting stale sandbox images.

**Data flow**: It receives a template name and the digest expected from current source. It boots a temporary sandbox, reads the digest file baked into that image, kills the sandbox, and compares the live value with the expected value. If the digest is missing or different, it raises an error explaining that the template must be republished.

**Call relations**: The main command-line flow calls this in `--check` mode for each size tier. It depends on `build_definition_digest` having computed the current expected value and on earlier builds having written that value into the image.

*Call graph*: called by 1 (main); 1 external calls (create).


##### `main`  (lines 491–534)

```
def main() -> None
```

**Purpose**: Acts as the command-line control center for this build script. It decides whether to print a Dockerfile, build a Docker image, check existing E2B templates, or publish new E2B templates.

**Data flow**: It reads command-line arguments, chooses one mode, and then runs the needed steps. In Dockerfile mode it writes Dockerfile text to standard output. In Docker-build mode it builds the local image. In check mode it compares each live E2B size template against the current digest. In default publish mode it stages artifacts, builds each sized E2B template, verifies it by booting it, and prints the published references.

**Call relations**: This is the script's entry point when run from the shell. It coordinates the helper functions: naming templates, staging artifacts, building definitions, checking drift, publishing through the E2B SDK, and verifying the result.

*Call graph*: calls 9 internal fn (build_definition_digest, build_docker_image, check_published_template, e2b_template, pod_dockerfile, stage_client_binary, stage_system_skills, template_name, verify_published_template); 2 external calls (ArgumentParser, build).


### `sandbox/proxy_gate.py`

`entrypoint` · `deployment validation`

This file is a small command-line safety check for the off-cluster sandbox network path. The project runs code inside E2B sandboxes, and those sandboxes must send outbound HTTPS traffic through a controlled proxy. For that to work, the sandbox also needs to trust the proxy's certificate authority, which is like adding a trusted stamp-maker so encrypted web traffic can be inspected or routed safely.

The script takes a public proxy URL, reads the proxy certificate and sandbox template settings from environment variables, then creates a temporary sandbox. Inside that sandbox it writes the certificate, installs it as a trusted certificate, and runs a probe command. The probe tries to open Anthropic's messages API through the proxy using an intentionally invalid run token. A working proxy should reject that request with HTTP 403, meaning the connection reached the proxy and TLS worked, but the credentials were not accepted. That is the expected success signal.

If the sandbox is still not ready, the probe may report a pending connection error, so the script waits and retries for several minutes. Any other result is treated as a failure. Whether the test passes or fails, the temporary sandbox is killed at the end so it does not keep running.

#### Function details

##### `ProxyTlsGate.run`  (lines 69–118)

```
def run(self) -> None
```

**Purpose**: Runs the actual proxy readiness test inside a fresh sandbox. It proves that the sandbox trusts the provided certificate and can reach the HTTPS proxy far enough to receive the expected 403 rejection.

**Data flow**: It starts with three stored values: the public proxy URL, the certificate text, and the sandbox template name. It checks that the proxy URL is HTTPS, builds a proxy address containing a deliberately invalid token and the known proxy password, then creates a sandbox. It writes the certificate into the sandbox, runs the certificate install command, and repeatedly runs a small Python web request through the proxy. If the probe prints 403, the function prints a success message and returns. If it times out or sees an unexpected status, it raises an error. In all cases, it kills the sandbox before leaving.

**Call relations**: This is called by main after command-line and environment settings have been collected. It relies on the sandbox service to create the temporary machine, on URL parsing to understand the proxy address, on shell quoting to safely build the probe command, and on time checks and sleeps to retry while the proxy is still warming up.

*Call graph*: 5 external calls (create, join, monotonic, sleep, urlsplit).


##### `main`  (lines 121–132)

```
def main() -> None
```

**Purpose**: Acts as the command-line entry point for the proxy gate. It gathers the needed inputs, chooses the sandbox template, and starts the gate check.

**Data flow**: It reads the --proxy-url command-line argument, then reads the certificate and template list from environment variables. If either required environment value is missing, it stops with a clear error. It turns the template list into usable sandbox template names, picks the first configured sandbox size, creates a ProxyTlsGate object with those inputs, and calls its run method. The result is either a completed gate check or an exception that signals deployment failure.

**Call relations**: This function is invoked when the file is run as a script. It prepares the information that ProxyTlsGate.run needs, using the argument parser for user input and the template helper to translate environment configuration into the exact E2B sandbox template to launch.

*Call graph*: 3 external calls (__init__, ArgumentParser, sandbox_templates).

## 📊 State Registers Touched

- `reg-deployment-config` — The merged deployment settings that tell the system what product, services, addresses, databases, sandboxes, and safety defaults to use.
- `reg-extension-catalog` — The installed extension and pack catalog that says which extra tools, routes, agents, skills, jobs, and backends are available.
- `reg-egress-network-policy` — The outbound network permission state that decides which external hosts, proxies, and secret injections are allowed for a workspace or agent.
- `reg-skill-store` — The saved and selected skills that can be provisioned by packs, loaded into agent sandboxes, or created by users inside a workspace.
- `reg-sandbox-runtime-cache` — Built sandbox client/runtime image and reusable sandbox cache artifacts used when launching isolated execution environments.
