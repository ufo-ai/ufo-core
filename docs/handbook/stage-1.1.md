# Server and CLI command entry points  `stage-1.1`

This stage is the front door of the system. It covers the commands a person runs in a terminal to start or manage a UFO workspace, such as initializing a project, running the server, checking status, or performing administrative tasks. These commands happen at the beginning of a workflow, before the deeper server machinery takes over, but they can also be used later for inspection and maintenance.

The main piece here is `core/src/ufo/cli.py`. It defines `ufoctl`, the command-line tool. A command-line tool is a text-based control panel: instead of clicking buttons, the user types commands. This file reads what the user asked for, gathers options and configuration, prepares the process state, and then sends the request to the right part of the system. For example, a “run” command hands off to server setup, while an “init” command helps create or prepare a workspace. In this way, `ufoctl` acts like a receptionist, translating human instructions into the internal actions the system needs to perform.

## Files in this stage

### Server and CLI command entry points
### `core/src/ufo/cli.py`

`entrypoint` · `command invocation, startup, and operator/admin tasks`

`ufoctl` is the project’s toolbox. Without it, a newcomer would have to create config files, generate secrets, prepare the database, start services, install extensions, and inspect billing or turn state by hand. This file gathers those jobs into clear terminal commands.

At startup, the command group loads a local `.env` file so development secrets are available. The `init` command then creates a default config if needed, writes safe development secrets, applies database migrations, creates the first workspace and owner, and stores a CLI login token. `serve`, `portal`, and `ingress` start or open the runtime surfaces: the web portal, backend services, and sandbox entry point.

The rest of the file is an operator console. It can set spending caps, credit prepaid balances, read spend reports, inspect transcript-read disclosures, list OAuth grants, store encrypted “bring your own key” credentials, manage extension pins, build a deployable bundle, cancel stuck turns, print durable turn steps, and seed demo data.

A recurring pattern is: load config, open the right database scope, do one focused action, print a readable result, and always close database resources. The `BrowserHandoff` helper is a small secure bridge from terminal login to browser login: it serves one temporary local page that posts the CLI token to the portal without putting the token in the URL.

#### Function details

##### `_ufoctl_dir`  (lines 127–129)

```
def _ufoctl_dir() -> Path
```

**Purpose**: Finds the private local directory where `ufoctl` stores machine-specific files, such as the CLI token. It lets users override the location with an environment variable.

**Data flow**: It reads the `UFOCTL_DIR` environment variable. If it is set, that path is used; otherwise it returns `~/.ufoctl` under the current user’s home directory.

**Call relations**: `init` calls this when saving the newly minted CLI token. `portal` calls it later to read that token back before opening the browser.

*Call graph*: called by 2 (init, portal); 2 external calls (Path, home).


##### `_dotenv_path`  (lines 132–133)

```
def _dotenv_path() -> Path
```

**Purpose**: Locates the `.env` file that sits beside the main UFO config file. This keeps local secrets next to local configuration.

**Data flow**: It asks the config system for the config path, takes that file’s parent folder, and returns the `.env` path inside it.

**Call relations**: The environment-loading and secret-writing helpers use this shared path so they all agree on where local secrets live. `init` also mentions this path in messages to the user.

*Call graph*: called by 4 (_load_dotenv, _missing_deploy_keys, _write_dev_secrets, init); 1 external calls (config_path).


##### `_dotenv_pairs`  (lines 136–170)

```
def _dotenv_pairs(text: str) -> list[tuple[str, str]]
```

**Purpose**: Parses simple `.env` text into name/value pairs. It exists because the CLI needs to inspect and merge secrets carefully, not just blindly import them.

**Data flow**: It receives raw text, skips blank lines and comments, accepts `KEY=VALUE` lines, strips an optional `export`, handles quoted values including multi-line secrets, and returns a list of pairs. If a quote is never closed, it raises an error.

**Call relations**: `_load_dotenv` uses it before putting values into the process environment. `_write_dev_secrets` and `_missing_deploy_keys` use it to see what secrets are already present.

*Call graph*: called by 3 (_load_dotenv, _missing_deploy_keys, _write_dev_secrets).


##### `_load_dotenv`  (lines 173–189)

```
def _load_dotenv() -> None
```

**Purpose**: Loads local `.env` secrets into the running CLI process. It also refuses unsafe bare provider key names that could accidentally be picked up by unrelated tools.

**Data flow**: It finds the `.env` file, parses it, checks for reserved names like `OPENAI_API_KEY`, and then writes accepted values into `os.environ`. If a reserved name appears, it stops with a readable CLI error.

**Call relations**: `main` calls this before any subcommand runs, so every command sees the same local secret environment.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (main); 1 external calls (ClickException).


##### `main`  (lines 193–195)

```
def main() -> None
```

**Purpose**: Defines the top-level `ufoctl` command group. It is the doorway through which all subcommands in this file are reached.

**Data flow**: When Click, the command-line framework, invokes it, it loads `.env` values into the process. It does not return user data; it prepares the environment for the selected command.

**Call relations**: Click uses this as the parent command. All decorated commands such as `init`, `serve`, `portal`, `balance`, and `turn` hang under it.

*Call graph*: calls 1 internal fn (_load_dotenv).


##### `_one_address`  (lines 198–203)

```
def _one_address(_ctx: click.Context, _param: click.Parameter, value: str) -> str
```

**Purpose**: Validates that an email option looks like one local address with a domain. It catches a typo early, before database setup tries to store it.

**Data flow**: It receives the command-line value, asks the seat/email helper whether it has a domain, and returns the value if valid. If not, it raises a Click parameter error.

**Call relations**: Click uses this as the validation callback for `init --email`, so bad owner addresses are rejected at the command-line boundary.

*Call graph*: 2 external calls (BadParameter, email_domain).


##### `init`  (lines 220–260)

```
def init(email: str, model: str, reasoning: ReasoningEffort, member_model_provider: str | None) -> None
```

**Purpose**: Sets up a usable UFO workspace on this machine. It writes default config, prepares secrets and schema, creates the owner/workspace/agent, and stores a CLI token.

**Data flow**: It receives owner email, model choices, reasoning level, and an optional model provider. It creates or reads config, writes missing development secrets, prepares PostgreSQL if needed, applies migrations, runs onboarding, mints a bearer token, saves it under the local `ufoctl` directory, and prints next-step warnings for missing deploy keys.

**Call relations**: This is the first command most users run. It delegates setup details to `_write_dev_secrets`, `_create_postgres_system_database`, `_onboard`, `_missing_deploy_keys`, and `_ufoctl_dir`.

*Call graph*: calls 6 internal fn (_create_postgres_system_database, _dotenv_path, _missing_deploy_keys, _onboard, _ufoctl_dir, _write_dev_secrets); 7 external calls (run, ClickException, echo, config_path, load_config, apply_migrations, mint_token).


##### `_missing_deploy_keys`  (lines 263–279)

```
def _missing_deploy_keys(config: Config) -> tuple[str, ...]
```

**Purpose**: Reports extension-required provider keys that are not currently available. It warns instead of blocking so a local server can still start without optional features.

**Data flow**: It reads active extension manifests, gathers declared deploy key names, compares them with names found in `.env` and the current environment, and returns missing names in their `UFO_`-prefixed form.

**Call relations**: `init` calls this at the end to tell the developer what to add before using features that need outside API keys.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 1 external calls (load_manifests).


##### `_write_dev_secrets`  (lines 282–304)

```
def _write_dev_secrets(config: Config) -> tuple[str, ...]
```

**Purpose**: Creates local development secrets needed for a zero-config `serve`. It avoids overwriting anything the user already supplied.

**Data flow**: It mints an encryption key and token-signing secrets, reads the existing `.env`, skips names already present in `.env` or the environment, appends only missing values, loads those new values into `os.environ`, and returns the names it added.

**Call relations**: `init` calls this before onboarding so token minting and credential encryption have the secrets they need.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 2 external calls (generate_key, token_urlsafe).


##### `_onboard`  (lines 307–361)

```
async def _onboard(config: Config, email: str, model: str, reasoning: ReasoningEffort, member_model_provider: str | None) -> Onboarded
```

**Purpose**: Creates the first workspace, owner member, default agent, and extension onboarding state. It is the database-backed heart of `ufoctl init`.

**Data flow**: It opens the application database, optionally builds an encrypted credential store, prepares onboarding with config and extension manifests, optionally reads a member model API key from the environment, creates the core records, stores that key if requested, runs extension onboarding steps, returns the onboarding result, and closes the database.

**Call relations**: `init` calls this after config, secrets, and migrations are ready. It hands most record creation to the `Onboarding` object and uses `CredentialStore` when secrets must be saved.

*Call graph*: called by 1 (init); 9 external calls (__init__, __init__, Fernet, dispose_db, init_db, load_manifests, deploy_env, member_slot, items).


##### `_create_postgres_system_database`  (lines 364–375)

```
async def _create_postgres_system_database(config: Config) -> None
```

**Purpose**: Creates the separate PostgreSQL system database if it does not already exist. This helps local or hosted PostgreSQL setups bootstrap themselves.

**Data flow**: It derives a normal PostgreSQL connection string from config, connects to the app database, checks whether the named system database exists, creates it if missing, and closes the connection.

**Call relations**: `init` calls this only when the configured app database is PostgreSQL, before migrations and onboarding need that system database.

*Call graph*: called by 1 (init); 1 external calls (connect).


##### `migrate`  (lines 379–396)

```
def migrate() -> None
```

**Purpose**: Applies database migrations so the schema matches the current code and active extensions. This is needed after installs or upgrades that add or change tables.

**Data flow**: It loads config, optionally uses the owner database connection string from the environment, normalizes that string for async database access, applies migrations, and prints confirmation.

**Call relations**: Operators run this directly, and `init` performs the same migration step during first setup. It delegates actual schema work to `apply_migrations`.

*Call graph*: 3 external calls (echo, load_config, apply_migrations).


##### `_one_slug`  (lines 399–402)

```
def _one_slug(_ctx: click.Context, _param: click.Parameter, value: str) -> str
```

**Purpose**: Checks that a new migration name is safe snake_case text. This keeps generated migration filenames predictable.

**Data flow**: It receives a slug string, tests it against the migration-name pattern, returns it if valid, or raises a Click parameter error if not.

**Call relations**: Click uses it to validate the `new-migration` argument before `new_migration` writes files.

*Call graph*: 1 external calls (BadParameter).


##### `new_migration`  (lines 407–424)

```
def new_migration(slug: str) -> None
```

**Purpose**: Creates a new core database migration file. It gives the file a timestamp revision and points it at the current migration head.

**Data flow**: It reads the current core migration head, creates a UTC timestamp, writes a migration template into the versions directory, updates the `HEAD` marker file, and prints what was created.

**Call relations**: Developers run this when changing the core database schema. It relies on containment helpers so generated files stay inside the migration directory.

*Call graph*: 4 external calls (echo, now, core_migration_head, contained_file).


##### `serve`  (lines 428–437)

```
def serve() -> None
```

**Purpose**: Starts the UFO runtime services for the configured pack. It also tells the user where the browser portal will be if the pack provides one.

**Data flow**: It loads config, loads extension manifests, asks which surface is the home portal, prints the portal URL when available, and then hands control to the server runner.

**Call relations**: This is the main local runtime command. It uses `_serve_base` to print the same base address that `portal` will later open.

*Call graph*: calls 1 internal fn (_serve_base); 5 external calls (echo, load_config, load_manifests, home_surface, run).


##### `portal`  (lines 441–459)

```
def portal() -> None
```

**Purpose**: Opens the web portal in the user’s browser and signs it in using the saved CLI token. It saves the user from copying tokens by hand.

**Data flow**: It loads config and manifests, finds the home surface, reads the token created by `init`, checks that `serve` is reachable, then starts a `BrowserHandoff` to pass the token to the browser. It prints the opened URL.

**Call relations**: Users run this after `serve`. It depends on `_ufoctl_dir` for the token path, `_serve_base` for the server address, and `BrowserHandoff` for secure browser sign-in.

*Call graph*: calls 2 internal fn (_serve_base, _ufoctl_dir); 7 external calls (__init__, ClickException, echo, get, load_config, load_manifests, home_surface).


##### `_serve_base`  (lines 462–469)

```
def _serve_base(config: Config) -> str
```

**Purpose**: Chooses the base URL that browser-facing commands should use. It prefers the configured public URL because cookies and absolute links must match the same host.

**Data flow**: It reads the config. If `connect.public_base_url` is set, it returns that; otherwise it builds a local URL from the configured serve host and port.

**Call relations**: `serve` uses it when printing the portal link. `portal` uses it when checking and opening the browser session.

*Call graph*: called by 2 (portal, serve).


##### `BrowserHandoff.open`  (lines 484–492)

```
def open(self) -> None
```

**Purpose**: Starts a one-use local web page that transfers the CLI token into the browser portal session. It keeps the token out of URLs and closes after delivery.

**Data flow**: It creates a random path, starts a temporary HTTP server bound to loopback only, opens that URL in the default browser, and serves requests until the token page has been delivered.

**Call relations**: `portal` creates a `BrowserHandoff` and calls this. This method builds the request handler with `_responder`, then relies on the browser to load the temporary page.

*Call graph*: calls 1 internal fn (_responder); 5 external calls (echo, HTTPServer, token_urlsafe, Event, open).


##### `BrowserHandoff._responder`  (lines 494–511)

```
def _responder(self, path: str, delivered: threading.Event) -> type[BaseHTTPRequestHandler]
```

**Purpose**: Builds the temporary HTTP request handler used by the browser handoff. The handler only serves the secret page at one random path.

**Data flow**: It receives the allowed path and a delivery event, renders the HTML page once, and returns a handler class. That class sends the page on the right path, sends 404 for other paths, and marks delivery complete.

**Call relations**: `BrowserHandoff.open` calls this before starting the local HTTP server. The returned handler uses `_page` for the actual form HTML.

*Call graph*: calls 1 internal fn (_page); called by 1 (open).


##### `BrowserHandoff._responder.do_GET`  (lines 498–507)

```
def do_GET(self) -> None
```

**Purpose**: Responds to the browser’s GET request during token handoff. It is the one place the temporary page is sent.

**Data flow**: It reads the requested path. If it is not the random handoff path, it sends a 404 error; if it matches, it sends the HTML page, writes it to the response, and marks the token as delivered.

**Call relations**: The local HTTP server created by `BrowserHandoff.open` calls this when the browser loads the handoff URL.


##### `BrowserHandoff._responder.log_message`  (lines 509–509)

```
def log_message(self, *args: object) -> None
```

**Purpose**: Suppresses the default HTTP server request logs. This keeps the terminal output clean during browser handoff.

**Data flow**: It receives log arguments from the HTTP server and intentionally does nothing.

**Call relations**: The temporary handler class uses this whenever the built-in HTTP server would normally print an access log.


##### `BrowserHandoff._page`  (lines 513–520)

```
def _page(self) -> str
```

**Purpose**: Renders the small HTML page that posts the token to the portal. It is like a self-submitting sign-in form.

**Data flow**: It reads the handoff’s portal URL and token, HTML-escapes both for safety, and returns a page containing a hidden token input and JavaScript that submits the form automatically.

**Call relations**: `BrowserHandoff._responder` calls this while preparing the one-use HTTP handler.

*Call graph*: called by 1 (_responder); 1 external calls (escape).


##### `ingress`  (lines 524–526)

```
def ingress() -> None
```

**Purpose**: Runs the sandbox ingress service, which is a protected doorway into sandbox ports. This is needed when conversations expose sandbox services through a controlled proxy.

**Data flow**: It takes no command arguments and simply hands control to the sandbox ingress runner.

**Call relations**: Click exposes it as `ufoctl ingress`. The real network proxy behavior lives in `ufo.harness.sandbox.ingress_serve.run`.

*Call graph*: 1 external calls (run).


##### `spend_cap`  (lines 530–531)

```
def spend_cap() -> None
```

**Purpose**: Defines the command group for viewing and changing spend caps. Spend caps limit how much can be spent over a time window.

**Data flow**: It receives no data itself; it groups subcommands under `ufoctl spend-cap`.

**Call relations**: Click uses it as the parent for `spend_cap_set` and `spend_cap_list`.


##### `spend_cap_set`  (lines 542–560)

```
def spend_cap_set(scope: str, subject_id: str, window_seconds: int, limit_micro_usd: int, on_breach: str) -> None
```

**Purpose**: Creates or updates a spending cap for a workspace, member, or agent. This gives operators a simple safety limit on model spend.

**Data flow**: It receives scope, optional subject id, time window, limit in micro-dollars, and breach behavior. It validates which scopes need a subject, converts the subject to a UUID when present, writes the cap, and prints the human-readable dollar limit.

**Call relations**: This command delegates database work to `_write_spend_cap` and uses Click errors for invalid command combinations.

*Call graph*: calls 1 internal fn (_write_spend_cap); 5 external calls (run, ClickException, echo, load_config, UUID).


##### `spend_cap_list`  (lines 564–574)

```
def spend_cap_list() -> None
```

**Purpose**: Prints the spend caps currently set for the workspace. It gives operators a quick view of active limits.

**Data flow**: It loads config, reads caps from the database, prints “no spend caps set” if empty, or formats each cap with scope, subject, dollar limit, window, and breach behavior.

**Call relations**: This command calls `_read_spend_caps`, then turns database rows into terminal output.

*Call graph*: calls 1 internal fn (_read_spend_caps); 3 external calls (run, echo, load_config).


##### `_write_spend_cap`  (lines 577–631)

```
async def _write_spend_cap(config: Config, scope: str, subject: UUID | None, window_seconds: int, limit_micro_usd: int, on_breach: str) -> UUID
```

**Purpose**: Writes one spend cap record, updating an existing matching cap instead of creating a duplicate. Matching is based on workspace, scope, subject, and window.

**Data flow**: It opens the database, finds the current workspace, searches for an existing cap with the same target and window, updates its limit if found, or inserts a new cap with a fresh UUID if not. It returns the cap id and closes the database.

**Call relations**: `spend_cap_set` calls this after validating command input. It performs the low-level table reads and writes inside a workspace transaction.

*Call graph*: called by 1 (spend_cap_set); 7 external calls (insert, select, update, dispose_db, init_db, workspace_tx, uuid4).


##### `_read_spend_caps`  (lines 634–660)

```
async def _read_spend_caps(config: Config) -> list[tuple[UUID, str, UUID | None, int, int, str]]
```

**Purpose**: Reads all spend caps for the current workspace. It supplies the data that the list command prints.

**Data flow**: It opens the database, finds the workspace id, selects cap fields ordered by scope, converts rows into plain tuples, returns them, and closes the database.

**Call relations**: `spend_cap_list` calls this and handles user-facing formatting.

*Call graph*: called by 1 (spend_cap_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `balance`  (lines 664–665)

```
def balance() -> None
```

**Purpose**: Defines the command group for prepaid workspace balance operations. Balance controls how much paid model work can begin.

**Data flow**: It receives no data itself; it groups balance subcommands.

**Call relations**: Click uses it as the parent for `balance_show`, `balance_credit`, and `balance_reserve`.


##### `balance_show`  (lines 670–685)

```
def balance_show(workspace_id: str) -> None
```

**Purpose**: Shows the current prepaid balance, reserve amount, total credits, total charges, and last purchase time. Operators use it to understand remaining budget.

**Data flow**: It receives an optional workspace id, loads config, reads the balance, prints “no balance” if missing, or prints dollar-formatted totals.

**Call relations**: It calls `_read_balance`, which opens the correct workspace scope and asks the billing layer for the stored balance.

*Call graph*: calls 1 internal fn (_read_balance); 3 external calls (run, echo, load_config).


##### `balance_credit`  (lines 693–706)

```
def balance_credit(granted_micro_usd: int, charged_micro_usd: int, reference: str, workspace_id: str) -> None
```

**Purpose**: Adds a credit to a workspace balance once per reference key. The reference makes the operation safe to retry without double-crediting.

**Data flow**: It receives granted amount, charged amount, reference, and optional workspace id. It rejects zero granted amount, calls the balance credit helper, then prints whether a new credit was added or the reference had already been used.

**Call relations**: It delegates the database and billing rules to `_credit_balance`.

*Call graph*: calls 1 internal fn (_credit_balance); 4 external calls (run, ClickException, echo, load_config).


##### `balance_reserve`  (lines 712–720)

```
def balance_reserve(micro_usd: int, workspace_id: str) -> None
```

**Purpose**: Sets the minimum headroom required before a turn may begin. This prevents work from starting when the account is too close to empty.

**Data flow**: It receives a reserve amount and optional workspace id, rejects negative values, writes the reserve, prints success if a balance exists, or raises an error if there is no balance yet.

**Call relations**: It calls `_set_reserve`, which uses the shared balance scope and billing helper.

*Call graph*: calls 1 internal fn (_set_reserve); 4 external calls (run, ClickException, echo, load_config).


##### `_target_workspace`  (lines 723–745)

```
async def _target_workspace(named: str) -> UUID
```

**Purpose**: Chooses which workspace an admin command should affect. If the user does not name one, it only guesses when there is exactly one workspace.

**Data flow**: It reads through the owner database. If a workspace id was provided, it verifies it exists and returns it. If none was provided, it lists workspaces and either returns the only one or raises a clear error for zero or many.

**Call relations**: `_balance_scope` and `_seed_target` use this to avoid accidentally acting on the wrong workspace in multi-workspace deployments.

*Call graph*: called by 2 (_balance_scope, _seed_target); 4 external calls (ClickException, select, owner_tx, UUID).


##### `_balance_scope`  (lines 749–766)

```
async def _balance_scope(config: Config, named: str) -> AsyncIterator[tuple[AsyncConnection, UUID]]
```

**Purpose**: Opens a database transaction for the one workspace targeted by a balance command. It makes sure hosted deployments use the correct owner and workspace context.

**Data flow**: It initializes the app database, initializes the owner database when available, resolves the workspace id, pins that workspace in context, yields a workspace database connection and id, then closes database resources afterward.

**Call relations**: `_read_balance`, `_credit_balance`, and `_set_reserve` all use this shared setup so balance operations behave consistently.

*Call graph*: calls 1 internal fn (_target_workspace); called by 3 (_credit_balance, _read_balance, _set_reserve); 5 external calls (dispose_db, init_db, init_owner_db, workspace_tx, ws).


##### `_read_balance`  (lines 769–771)

```
async def _read_balance(config: Config, named: str) -> Balance | None
```

**Purpose**: Reads the prepaid balance for a selected workspace. It is the small bridge between CLI code and the billing balance module.

**Data flow**: It opens `_balance_scope`, passes the connection and workspace id to `read_balance`, and returns either a `Balance` object or `None`.

**Call relations**: `balance_show` calls this, then formats the result for the terminal.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_show); 1 external calls (read_balance).


##### `_credit_balance`  (lines 774–780)

```
async def _credit_balance(config: Config, named: str, granted_micro_usd: int, charged_micro_usd: int, reference: str) -> bool
```

**Purpose**: Applies a one-time balance credit for a selected workspace. It returns whether the credit was newly applied.

**Data flow**: It opens `_balance_scope`, passes amounts and reference to the billing `credit` function, and returns that boolean result.

**Call relations**: `balance_credit` calls this after validating command input.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_credit); 1 external calls (credit).


##### `_set_reserve`  (lines 783–785)

```
async def _set_reserve(config: Config, named: str, reserve_micro_usd: int) -> bool
```

**Purpose**: Updates the reserve amount for a selected workspace balance. The reserve is the minimum required budget buffer.

**Data flow**: It opens `_balance_scope`, calls the billing `set_reserve` helper with the workspace id and amount, and returns whether the update succeeded.

**Call relations**: `balance_reserve` calls this and turns the boolean result into either success text or a Click error.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_reserve); 1 external calls (set_reserve).


##### `flags`  (lines 792–797)

```
def flags() -> None
```

**Purpose**: Defines the command group for changing feature flag output without redeploying code. A feature flag is a switch that lets code serve different behavior at runtime.

**Data flow**: It receives no data itself; it groups flag commands.

**Call relations**: Click uses it as the parent for `flags_set`.


##### `flags_set`  (lines 803–825)

```
def flags_set(key: str, on: bool) -> None
```

**Purpose**: Sets a feature flag on or off in the configured flag backend. It refuses unknown flags so dashboards do not show switches that no active code reads.

**Data flow**: It receives a flag key and desired on/off value, loads config, checks that a backend is configured, loads extension manifests, verifies the key is declared, imports the backend admin module, calls its `serve` method, and prints the new state.

**Call relations**: This command connects extension-declared flag definitions to the external flag service selected in config.

*Call graph*: 5 external calls (ClickException, echo, import_module, load_config, load_manifests).


##### `spend`  (lines 833–856)

```
def spend(window_seconds: int) -> None
```

**Purpose**: Prints a spending report for a recent time window. It helps operators see total cost and where it came from.

**Data flow**: It receives a window length in seconds, reads the spend rollup, converts micro-dollars to dollars, and prints totals by dimension, member, agent, origin, and price digest.

**Call relations**: It calls `_read_spend` for database-backed accounting and then formats the returned `SpendReport`.

*Call graph*: calls 1 internal fn (_read_spend); 3 external calls (run, echo, load_config).


##### `_read_spend`  (lines 859–866)

```
async def _read_spend(config: Config, window_seconds: int) -> SpendReport
```

**Purpose**: Reads the billing ledger rollup for the current workspace and time window. A rollup is a summarized view of many ledger entries.

**Data flow**: It opens the database, finds the workspace id, asks `SpendRollup` to read the report for the requested window, returns the report, and closes the database.

**Call relations**: `spend` calls this before printing the report.

*Call graph*: called by 1 (spend); 5 external calls (__init__, select, dispose_db, init_db, workspace_tx).


##### `transcript_reads`  (lines 874–887)

```
def transcript_reads(limit: int) -> None
```

**Purpose**: Lists recorded admin disclosures for reading another member’s private transcript. This supports auditability for sensitive access.

**Data flow**: It receives a limit, rejects values below one, reads recent transcript access records, prints a no-records message if empty, or prints reader, subject, conversation id, and time.

**Call relations**: It calls `_read_transcript_accesses`, then formats the audit records for an operator.

*Call graph*: calls 1 internal fn (_read_transcript_accesses); 4 external calls (run, ClickException, echo, load_config).


##### `_read_transcript_accesses`  (lines 890–924)

```
async def _read_transcript_accesses(config: Config, limit: int) -> list[tuple[str, str, UUID, datetime]]
```

**Purpose**: Reads recent transcript-access disclosure records from the database. It joins member records so the output can show email addresses instead of only ids.

**Data flow**: It opens the database, aliases the member table for reader and subject, selects recent transcript access rows for the workspace, limits the result, converts rows into tuples, and closes the database.

**Call relations**: `transcript_reads` calls this and prints the returned records.

*Call graph*: called by 1 (transcript_reads); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `grants`  (lines 928–940)

```
def grants() -> None
```

**Purpose**: Lists OAuth account grants available to agents. OAuth is the common web authorization flow where a user grants an app access to an external account.

**Data flow**: It loads config, reads grant summaries, prints “no grants” if empty, or prints each agent, provider, account id, sharing scope, and grant date.

**Call relations**: It calls `_read_grants`, which gets summaries from the runtime access layer.

*Call graph*: calls 1 internal fn (_read_grants); 3 external calls (run, echo, load_config).


##### `_read_grants`  (lines 943–950)

```
async def _read_grants(config: Config) -> tuple[GrantSummary, ...]
```

**Purpose**: Reads summarized OAuth grants for the current workspace. It supplies the data for the `grants` command.

**Data flow**: It opens the database, finds the workspace id, asks `workspace_grant_summaries` for grant summaries, returns them, and closes the database.

**Call relations**: `grants` calls this and turns the summaries into terminal output.

*Call graph*: called by 1 (grants); 5 external calls (select, dispose_db, init_db, workspace_tx, workspace_grant_summaries).


##### `credential`  (lines 954–956)

```
def credential() -> None
```

**Purpose**: Defines the command group for encrypted extension credentials. These are “bring your own key” secrets supplied by the workspace operator.

**Data flow**: It receives no data itself; it groups credential subcommands.

**Call relations**: Click uses it as the parent for `credential_set` and `credential_list`.


##### `credential_set`  (lines 961–979)

```
def credential_set(slot: str) -> None
```

**Purpose**: Stores a secret value for one declared credential slot. It avoids command-line arguments for the secret so it is less likely to appear in shell history or process lists.

**Data flow**: It receives the slot name, loads config, checks the slot is declared by an active extension, checks the encryption key is available, reads the value from a hidden prompt or standard input, rejects empty values, writes the encrypted credential, and prints confirmation.

**Call relations**: It uses `_declared_slots` to validate the slot and `_write_credential` to store the value.

*Call graph*: calls 2 internal fn (_declared_slots, _write_credential); 5 external calls (run, ClickException, echo, prompt, load_config).


##### `credential_list`  (lines 983–993)

```
def credential_list() -> None
```

**Purpose**: Shows declared credential slots and whether each one has a stored value. It never prints secret values.

**Data flow**: It loads config, reads declared slots, prints a no-slots message if none exist, reads stored slot names, and prints each declared slot with its extension and set/unset status.

**Call relations**: It uses `_declared_slots` for the expected slots and `_read_stored_slots` for what is already saved.

*Call graph*: calls 2 internal fn (_declared_slots, _read_stored_slots); 3 external calls (run, echo, load_config).


##### `_declared_slots`  (lines 996–1001)

```
def _declared_slots(config: Config) -> dict[str, str]
```

**Purpose**: Builds the list of credential slots declared by active extensions. This lets the CLI reject unknown secret names.

**Data flow**: It loads extension manifests for the pack and returns a mapping from slot name to extension name. If manifests cannot be loaded, it turns that into a readable CLI error.

**Call relations**: `credential_set` and `credential_list` call this before writing or displaying credential status.

*Call graph*: called by 2 (credential_list, credential_set); 2 external calls (ClickException, load_manifests).


##### `_write_credential`  (lines 1004–1011)

```
async def _write_credential(config: Config, key: str, slot: str, value: str) -> None
```

**Purpose**: Encrypts and stores one credential value for the current workspace. Encryption keeps stored provider keys from being plain text in the database.

**Data flow**: It opens the database, finds the workspace id, builds a `CredentialStore` using the provided Fernet encryption key, stores the slot value, and closes the database.

**Call relations**: `credential_set` calls this after reading the secret from the user.

*Call graph*: called by 1 (credential_set); 6 external calls (__init__, Fernet, select, dispose_db, init_db, workspace_tx).


##### `_read_stored_slots`  (lines 1014–1028)

```
async def _read_stored_slots(config: Config) -> frozenset[str]
```

**Purpose**: Reads which credential slots currently have stored values. It reads only slot names, not the secret contents.

**Data flow**: It opens the database, finds the workspace id, selects credential slot names for that workspace, returns them as a frozen set, and closes the database.

**Call relations**: `credential_list` calls this to mark declared slots as set or unset.

*Call graph*: called by 1 (credential_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `ext`  (lines 1032–1033)

```
def ext() -> None
```

**Purpose**: Defines the command group for extension store operations. Extensions are add-ons that can be searched, installed, and removed from the deploy lockfile.

**Data flow**: It receives no data itself; it groups extension commands.

**Call relations**: Click uses it as the parent for `ext_search`, `ext_install`, and `ext_remove`.


##### `_store`  (lines 1036–1039)

```
def _store(config: Config) -> ExtensionStore
```

**Purpose**: Constructs the extension store object for the current config. It refuses extension commands when no store is configured.

**Data flow**: It reads the configured store path or URL, reads the catalog, finds the lockfile path, and returns an `ExtensionStore`. If no store is enabled, it raises a Click error.

**Call relations**: All three extension commands call this before searching, installing, or removing pins.

*Call graph*: called by 3 (ext_install, ext_remove, ext_search); 4 external calls (__init__, ClickException, lockfile_path, read_catalog).


##### `ext_search`  (lines 1044–1057)

```
def ext_search(query: str) -> None
```

**Purpose**: Searches the configured extension catalog and shows matching extensions. It also tells the user whether each match is installed, available, or bundle-only.

**Data flow**: It receives a query string, loads config, builds the store, searches it, prints a no-match message if empty, or prints each listing with version and state.

**Call relations**: It calls `_store` for access to the extension catalog.

*Call graph*: calls 1 internal fn (_store); 2 external calls (echo, load_config).


##### `ext_install`  (lines 1062–1068)

```
def ext_install(name: str) -> None
```

**Purpose**: Pins an extension from the store into the deploy lockfile. The next server run can then load that extension.

**Data flow**: It receives an extension name, loads config, builds the store, asks it to install the name, catches store errors as CLI errors, and prints the pinned version and digest.

**Call relations**: It uses `_store` to reach the catalog and lockfile, while the store object performs the actual pinning.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `ext_remove`  (lines 1073–1079)

```
def ext_remove(name: str) -> None
```

**Purpose**: Removes an extension pin from the deploy lockfile. The next server run will stop loading it.

**Data flow**: It receives an extension name, loads config, builds the store, asks it to remove the name, converts errors into readable CLI errors, and prints confirmation.

**Call relations**: It uses `_store` for lockfile access and delegates the lockfile edit to the extension store.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `_ufo_project_dir`  (lines 1085–1096)

```
def _ufo_project_dir() -> Path
```

**Purpose**: Finds the source project directory needed to build the UFO Python wheel. A wheel is a packaged Python distribution used in the deploy bundle.

**Data flow**: It walks upward from this file, looks for a `pyproject.toml` whose project name is `ufo`, returns that directory if found, and raises a CLI error if running from an install that lacks source files.

**Call relations**: `bundle` calls this before invoking `uv build`, so the build works no matter what the current terminal directory is.

*Call graph*: called by 1 (bundle); 3 external calls (ClickException, Path, loads).


##### `bundle`  (lines 1108–1138)

```
def bundle(out: Path, client_binary: Path) -> None
```

**Purpose**: Builds a runnable deployment bundle containing the UFO wheel, client binary, config, and extension pins. This freezes the current deploy setup into an artifact.

**Data flow**: It receives output directory and client binary path, loads config, reads the extension catalog if configured, builds the UFO wheel with `uv`, verifies the wheel exists, asks `Bundle` to assemble the artifact, and prints the result and pinned extensions.

**Call relations**: It uses `_ufo_project_dir` to locate source code, `wheel_name` to verify the build product, and the `Bundle` class for artifact assembly.

*Call graph*: calls 1 internal fn (_ufo_project_dir); 8 external calls (__init__, ClickException, echo, run, wheel_name, config_path, load_config, read_catalog).


##### `turn`  (lines 1142–1143)

```
def turn() -> None
```

**Purpose**: Defines the command group for acting on one conversation turn. A turn is one unit of agent work in a conversation.

**Data flow**: It receives no data itself; it groups turn subcommands.

**Call relations**: Click uses it as the parent for `turn_cancel` and `turn_steps`.


##### `turn_cancel`  (lines 1149–1159)

```
def turn_cancel(turn_id: str, workspace_id: str) -> None
```

**Purpose**: Cancels one turn that may be stuck or no longer finishable. It gives operators a safe escape hatch for work that members cannot end themselves.

**Data flow**: It receives a turn id and optional workspace id, loads config, converts the turn id to a UUID, calls the cancel helper, and prints whether cancellation happened or the turn was already terminal.

**Call relations**: It delegates the workflow and database details to `_cancel_turn`.

*Call graph*: calls 1 internal fn (_cancel_turn); 4 external calls (run, echo, load_config, UUID).


##### `_cancel_turn`  (lines 1162–1196)

```
async def _cancel_turn(config: Config, turn_id: UUID, named_workspace: str) -> bool
```

**Purpose**: Finds the workspace for a turn if needed and asks the durable workflow system to cancel it. Durable means the workflow records enough state to recover after failures.

**Data flow**: It opens the database, resolves the workspace from the owner database unless supplied, creates a replay-safe workflow client, pins the workspace context, optionally verifies the turn exists in that workspace, calls `cancel_one_turn`, returns whether a turn was cancelled, and closes database resources.

**Call relations**: `turn_cancel` calls this. It coordinates the database, workspace context, and durable cancellation helper.

*Call graph*: called by 1 (turn_cancel); 11 external calls (ClickException, select, dispose_db, init_db, init_owner_db, owner_tx, workspace_tx, replay_safe_client, cancel_one_turn, ws (+1 more)).


##### `turn_steps`  (lines 1202–1210)

```
def turn_steps(turn_id: str, workspace_id: str) -> None
```

**Purpose**: Prints the durable step log for one turn. This lets an operator see what the turn actually did, even if the normal transcript no longer shows every detail.

**Data flow**: It receives a turn id and optional workspace id, loads config, converts the id to a UUID, and calls the print helper.

**Call relations**: It is the CLI wrapper around `_print_turn_steps`.

*Call graph*: calls 1 internal fn (_print_turn_steps); 3 external calls (run, load_config, UUID).


##### `_print_turn_steps`  (lines 1213–1245)

```
async def _print_turn_steps(config: Config, turn_id: UUID, named_workspace: str) -> None
```

**Purpose**: Reads and prints the recorded execution steps for a turn. It can resolve the workspace automatically when an owner database is available.

**Data flow**: It opens databases, resolves the workspace, creates a replay-safe client, pins the workspace, reads the turn’s running attempt id, asks `DurableTurnSteps` for recorded steps, sends them to `_echo_turn_steps`, and closes resources.

**Call relations**: `turn_steps` calls this. It hands final formatting to `_echo_turn_steps`.

*Call graph*: calls 1 internal fn (_echo_turn_steps); called by 1 (turn_steps); 11 external calls (__init__, ClickException, select, dispose_db, init_db, init_owner_db, owner_tx, workspace_tx, replay_safe_client, ws (+1 more)).


##### `_echo_turn_steps`  (lines 1248–1261)

```
def _echo_turn_steps(steps: tuple[TurnStep, ...]) -> None
```

**Purpose**: Formats durable turn steps for terminal output. It includes step number, kind, function, duration, and recorded messages.

**Data flow**: It receives a tuple of steps. If empty, it prints a no-records message; otherwise it prints each step and each message, using `_echo_block` for structured message blocks.

**Call relations**: `_print_turn_steps` calls this after reading the durable step log.

*Call graph*: calls 1 internal fn (_echo_block); called by 1 (_print_turn_steps); 1 external calls (echo).


##### `_echo_block`  (lines 1264–1274)

```
def _echo_block(block: object) -> str
```

**Purpose**: Turns structured message blocks into readable one-line text. It knows how to describe text, tool calls, and tool results.

**Data flow**: It receives an unknown block object, pattern-matches known block types, returns text for text blocks, JSON-formatted arguments for tool calls, result text for tool results, or the type name for unknown blocks.

**Call relations**: `_echo_turn_steps` calls this when a recorded message contains structured content instead of plain text.

*Call graph*: called by 1 (_echo_turn_steps); 1 external calls (dumps).


##### `seed`  (lines 1278–1279)

```
def seed() -> None
```

**Purpose**: Defines the command group for writing demonstration content. Seed data helps designers and developers test portal behavior with realistic records.

**Data flow**: It receives no data itself; it groups seed subcommands.

**Call relations**: Click uses it as the parent for `seed_kitchen_sink`.


##### `seed_kitchen_sink`  (lines 1284–1293)

```
def seed_kitchen_sink(workspace_id: str) -> None
```

**Purpose**: Writes a demonstration conversation containing many portal display shapes. It is useful for visual review because each run replaces the previous demo with consistent content.

**Data flow**: It receives an optional workspace id, loads config, writes the seed conversation, and prints the portal route where it can be viewed.

**Call relations**: It delegates setup and writing to `_seed_kitchen_sink`.

*Call graph*: calls 1 internal fn (_seed_kitchen_sink); 3 external calls (run, echo, load_config).


##### `_seed_kitchen_sink`  (lines 1296–1307)

```
async def _seed_kitchen_sink(config: Config, named: str) -> UUID
```

**Purpose**: Prepares database and blob storage access for the kitchen-sink seed. Blob storage is where larger attached content can be saved outside normal database rows.

**Data flow**: It opens the app database, optionally opens the owner database, builds a workspace blob store from config, calls `_seed_target`, returns the conversation id, and closes database resources.

**Call relations**: `seed_kitchen_sink` calls this. It hands workspace-specific work to `_seed_target`.

*Call graph*: calls 1 internal fn (_seed_target); called by 1 (seed_kitchen_sink); 5 external calls (__init__, blob_store_for, dispose_db, init_db, init_owner_db).


##### `_seed_target`  (lines 1310–1341)

```
async def _seed_target(blob: WorkspaceBlobStore, named: str) -> UUID
```

**Purpose**: Finds the target workspace, main agent, and first member, then writes the kitchen-sink conversation. It ensures the demo content belongs to real workspace records.

**Data flow**: It resolves the workspace, pins workspace context, reads the main agent and earliest member, raises an error if no member exists, creates a `KitchenSink` writer with blob, workspace, agent, member, and email, writes the content, and returns the conversation id.

**Call relations**: `_seed_kitchen_sink` calls this after storage and database setup. It uses `_target_workspace` to choose the workspace safely.

*Call graph*: calls 1 internal fn (_target_workspace); called by 1 (_seed_kitchen_sink); 5 external calls (__init__, ClickException, select, workspace_tx, ws).
