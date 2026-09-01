# Operator Entry Points, Packaging, and Process Launch  `stage-1`

This stage is the system’s front door. It covers the tools people or deployment scripts use before the main server and workers settle into normal work. The main entry point is ufoctl, defined in cli.py. It lets an operator create and maintain a workspace: run setup, migrate the database, open the web portal, manage billing and credentials, repair conversations, load demo data, and package the app. The small __init__.py file simply tells Python that ufo is an importable package.

For deployment, bundle.py gathers the exact ingredients needed to run UFO elsewhere, like packing a travel kit with the right configuration, lockfile, runtime wheel, and sandbox client. product.py reads database facts and turns them into product funnel measurements, such as whether a workspace has members, tools, chats, or payments.

The sandbox scripts are pre-flight checks. build_template.py keeps the local Docker sandbox image and the E2B cloud template built from the same recipe. proxy_gate.py verifies that sandbox web traffic is being intercepted safely. The sample skill probe is a tiny “does this run?” test.

## Files in this stage

### Operator CLI Shell
The package marker and ufoctl command provide the human-facing entry point for setup, inspection, repair, and operational workflows.

### `core/src/ufo/cli.py`

`entrypoint` · `CLI command invocation; startup for dotenv loading, then one-off admin/setup/runtime operations`

This file is like the control panel for a UFO installation. Without it, a developer or operator would have to know many internal modules and database details just to start the system, add secrets, run migrations, open the portal, or fix a stuck turn. The file gathers those jobs into clear terminal commands.

At startup, the CLI loads a nearby `.env` file so local secrets are available, but it refuses unsafe bare provider-key names that other tools might accidentally read. The `init` command creates a default config if needed, writes development secrets, prepares the database, onboards the first workspace and owner, and stores a long-lived local CLI token. `serve`, `portal`, and `ingress` start or connect to the running web and sandbox pieces.

The rest of the file is a set of operator tools. Some read or update money controls, such as spend caps and prepaid balance. Others inspect audit records, OAuth grants, stored credential slots, and durable turn steps. Extension commands search, install, and remove add-ons. Bundle commands package a deployable artifact. Seed commands write sample content for UI review.

Most commands follow the same pattern: load configuration, open the right database scope, do one careful operation, print a human-readable result, and always close database resources afterward.

#### Function details

##### `_ufoctl_dir`  (lines 127–129)

```
def _ufoctl_dir() -> Path
```

**Purpose**: Finds the private directory where this machine stores `ufoctl` state, such as the local login token. It lets tests or deployments override the location with an environment variable.

**Data flow**: It reads the `UFOCTL_DIR` environment variable. If it is set, that path is used; otherwise it builds a default path under the user’s home directory called `.ufoctl`. It returns that path without creating it.

**Call relations**: `init` uses this path to write the CLI token after setup. `portal` uses the same path later to read that token and sign the browser in.

*Call graph*: called by 2 (init, portal); 2 external calls (Path, home).


##### `_dotenv_path`  (lines 132–133)

```
def _dotenv_path() -> Path
```

**Purpose**: Locates the `.env` file that belongs next to the UFO config file. This keeps local secrets tied to the same project configuration.

**Data flow**: It asks the config system for the config file path, takes that file’s parent directory, and appends `.env`. The result is a path where secrets may be read or written.

**Call relations**: Startup loading, secret generation, missing-key reporting, and `init` status messages all call this so they agree on the same `.env` location.

*Call graph*: called by 4 (_load_dotenv, _missing_deploy_keys, _write_dev_secrets, init); 1 external calls (config_path).


##### `_dotenv_pairs`  (lines 136–170)

```
def _dotenv_pairs(text: str) -> list[tuple[str, str]]
```

**Purpose**: Reads simple `.env` text and turns it into key/value pairs. It supports normal one-line secrets and quoted multi-line secrets, such as private keys.

**Data flow**: It receives raw text, skips blank lines and comments, strips an optional `export`, removes matching surrounding quotes, and collects each `NAME=value` entry. It returns a list of pairs, or raises an error if a quoted value never closes.

**Call relations**: `_load_dotenv` uses it before putting values into the process environment. `_write_dev_secrets` and `_missing_deploy_keys` use it to understand what secrets are already present.

*Call graph*: called by 3 (_load_dotenv, _missing_deploy_keys, _write_dev_secrets).


##### `_load_dotenv`  (lines 173–189)

```
def _load_dotenv() -> None
```

**Purpose**: Loads local secrets from `.env` into the current command process. It also protects users from accidentally exposing bare provider API keys to every tool that reads `.env`.

**Data flow**: It finds the `.env` file, reads and parses it if present, checks for reserved names like `OPENAI_API_KEY`, then copies accepted values into environment variables. If a reserved name is found, it stops the command with a readable error.

**Call relations**: The top-level CLI group calls this before any subcommand runs, so every command sees the same local secret environment.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (main); 1 external calls (ClickException).


##### `main`  (lines 193–195)

```
def main() -> None
```

**Purpose**: Defines the root `ufoctl` command group. It is the entry point that all subcommands hang from.

**Data flow**: When a user runs any `ufoctl` command, this function runs first and loads `.env` values into the process. It does not return user data; it prepares the environment for the chosen subcommand.

**Call relations**: Click, the command-line framework, calls this group function before dispatching to commands such as `init`, `serve`, `portal`, or `balance`.

*Call graph*: calls 1 internal fn (_load_dotenv).


##### `_one_address`  (lines 198–203)

```
def _one_address(_ctx: click.Context, _param: click.Parameter, value: str) -> str
```

**Purpose**: Validates that an email option really looks like a single local address with a domain. It catches a bad owner email before database setup tries to store it.

**Data flow**: It receives the option value from Click, checks whether it has a valid email domain shape, and returns the same value if valid. If not, it raises a command-line parameter error.

**Call relations**: Click uses it as the callback for `init --email`, so onboarding only starts with a usable owner address.

*Call graph*: 2 external calls (BadParameter, email_domain).


##### `init`  (lines 220–260)

```
def init(email: str, model: str, reasoning: ReasoningEffort, member_model_provider: str | None) -> None
```

**Purpose**: Performs first-time setup for a local or hosted UFO workspace. It writes default config, creates needed secrets, migrates the database, onboards the owner and default agent, and stores a local CLI token.

**Data flow**: It receives command options for owner email, model, reasoning level, and optional provider key storage. It writes files, reads config and environment secrets, creates databases if needed, runs onboarding, mints a bearer token, writes that token with private file permissions, and prints next-step messages.

**Call relations**: This is the main setup command under `main`. It delegates small jobs to `_write_dev_secrets`, `_create_postgres_system_database`, `_onboard`, `_ufoctl_dir`, and `_missing_deploy_keys`, turning their results into a complete setup flow.

*Call graph*: calls 6 internal fn (_create_postgres_system_database, _dotenv_path, _missing_deploy_keys, _onboard, _ufoctl_dir, _write_dev_secrets); 7 external calls (run, ClickException, echo, config_path, load_config, apply_migrations, mint_token).


##### `_missing_deploy_keys`  (lines 263–279)

```
def _missing_deploy_keys(config: Config) -> tuple[str, ...]
```

**Purpose**: Finds extension-required provider keys that are not available yet. It warns the operator early without blocking a zero-config server from starting.

**Data flow**: It reads active extension manifests to learn which deployment keys are declared. It then checks both `.env` and the current environment for either the `UFO_`-prefixed name or the bare name, and returns the missing names in their recommended `UFO_...` form.

**Call relations**: `init` calls this at the end of setup so the user can add optional keys before features that need them are used.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 1 external calls (load_manifests).


##### `_write_dev_secrets`  (lines 282–304)

```
def _write_dev_secrets(config: Config) -> tuple[str, ...]
```

**Purpose**: Creates local development secrets needed for a smooth first run. It avoids overwriting anything the user already supplied.

**Data flow**: It mints a credential-encryption key, an artifact token secret, and the CLI bearer-token secret. It reads existing `.env` and environment variables, appends only missing names to `.env`, copies those new values into the current process, and returns the names it added.

**Call relations**: `init` calls this before onboarding so token minting and credential storage have the secrets they need.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 2 external calls (generate_key, token_urlsafe).


##### `_onboard`  (lines 307–361)

```
async def _onboard(config: Config, email: str, model: str, reasoning: ReasoningEffort, member_model_provider: str | None) -> Onboarded
```

**Purpose**: Creates the first workspace, owner member, default agent, and extension onboarding state. It also optionally stores a model API key for the initial member.

**Data flow**: It initializes the database, builds an onboarding object from config, email, model, reasoning choice, credentials, and extension manifests, then creates the core records. If a member provider was requested, it reads the matching environment key and stores it encrypted in the member’s slot. Finally it runs extension onboarding steps and returns the created workspace/member information.

**Call relations**: `init` calls this after migrations. It hands detailed creation work to the onboarding subsystem and always disposes the database when finished.

*Call graph*: called by 1 (init); 9 external calls (__init__, __init__, Fernet, dispose_db, init_db, load_manifests, deploy_env, member_slot, items).


##### `_create_postgres_system_database`  (lines 364–375)

```
async def _create_postgres_system_database(config: Config) -> None
```

**Purpose**: Creates the extra PostgreSQL system database if it does not already exist. This is needed for deployments that use PostgreSQL instead of local SQLite.

**Data flow**: It derives a normal PostgreSQL connection string from config, connects to the application database, checks whether the system database name exists, creates it if missing, and closes the connection.

**Call relations**: `init` calls this only when the configured database URL starts with PostgreSQL, before migrations and onboarding need the system database.

*Call graph*: called by 1 (init); 1 external calls (connect).


##### `migrate`  (lines 379–396)

```
def migrate() -> None
```

**Purpose**: Brings the database schema up to date. It applies core migrations and any active extension migrations.

**Data flow**: It loads config, chooses an owner database URL from `UFO_OWNER_DSN` when present or the configured database otherwise, runs migrations, and prints confirmation.

**Call relations**: Operators run this after setup or extension changes. It delegates the actual schema changes to the database migration module.

*Call graph*: 3 external calls (echo, load_config, apply_migrations).


##### `_one_slug`  (lines 399–402)

```
def _one_slug(_ctx: click.Context, _param: click.Parameter, value: str) -> str
```

**Purpose**: Checks that a new migration name is a safe snake_case slug. This keeps migration filenames predictable and valid.

**Data flow**: It receives a command argument, compares it with the allowed pattern, and returns it unchanged if valid. Otherwise it raises a command-line parameter error.

**Call relations**: Click uses it for the `new-migration` command before that command writes a migration file.

*Call graph*: 1 external calls (BadParameter).


##### `new_migration`  (lines 407–424)

```
def new_migration(slug: str) -> None
```

**Purpose**: Creates a new core database migration file from a template. It gives the migration a timestamp revision and records it as the current head.

**Data flow**: It reads the current core migration head, creates a timestamp, writes a new migration file with that timestamp and the provided slug, then updates the `HEAD` file to point at the new revision.

**Call relations**: Developers call this command when changing the core schema. It relies on `_one_slug` for safe input and on migration helpers to know the current head.

*Call graph*: 4 external calls (echo, now, core_migration_head, contained_file).


##### `serve`  (lines 428–437)

```
def serve() -> None
```

**Purpose**: Starts the main UFO server process. Before starting, it prints the portal URL if the active pack provides a browser surface.

**Data flow**: It loads config and extension manifests, asks which surface should be the home page, prints a helpful URL when one exists, then hands control to the server runner.

**Call relations**: This command is the local runtime launcher. It uses `_serve_base` so the printed URL matches the configured public or local host.

*Call graph*: calls 1 internal fn (_serve_base); 5 external calls (echo, load_config, load_manifests, home_surface, run).


##### `portal`  (lines 441–459)

```
def portal() -> None
```

**Purpose**: Opens the browser portal and signs it in using this machine’s saved CLI token. It removes the need to copy and paste a token manually.

**Data flow**: It loads config, finds the home surface, reads the token written by `init`, checks that the server responds, then starts a one-request local browser handoff that posts the token to the portal. It prints the opened URL when done.

**Call relations**: This command depends on `init` having written a token and `serve` already answering. It uses `_serve_base`, `_ufoctl_dir`, and `BrowserHandoff` to complete the sign-in path.

*Call graph*: calls 2 internal fn (_serve_base, _ufoctl_dir); 7 external calls (__init__, ClickException, echo, get, load_config, load_manifests, home_surface).


##### `_serve_base`  (lines 462–469)

```
def _serve_base(config: Config) -> str
```

**Purpose**: Chooses the base URL users should use for the web server. It prefers the configured public URL because browser cookies and absolute links must agree on the host.

**Data flow**: It reads the loaded config. If `connect.public_base_url` exists, it returns that; otherwise it builds a local URL from the configured serve host and port.

**Call relations**: `serve` uses it when printing where the portal lives. `portal` uses it when checking the server and building the target portal URL.

*Call graph*: called by 2 (portal, serve).


##### `BrowserHandoff.open`  (lines 484–492)

```
def open(self) -> None
```

**Purpose**: Opens a temporary local web page that safely transfers the CLI token into the browser session. The token goes in a form body, not in the URL.

**Data flow**: It creates an unguessable path, starts a local HTTP server on `127.0.0.1` with a random port, opens the browser to that path, and serves exactly until the page has been delivered. If the browser cannot be opened automatically, it prints the local URL.

**Call relations**: `portal` constructs a `BrowserHandoff` and calls this after confirming the real portal is reachable. This method creates the responder class and drives the one-request listener.

*Call graph*: calls 1 internal fn (_responder); 5 external calls (echo, HTTPServer, token_urlsafe, Event, open).


##### `BrowserHandoff._responder`  (lines 494–511)

```
def _responder(self, path: str, delivered: threading.Event) -> type[BaseHTTPRequestHandler]
```

**Purpose**: Builds the tiny HTTP request handler used for the browser handoff. It binds one secret path to one generated HTML page.

**Data flow**: It receives the allowed path and a delivery event, prebuilds the HTML page, and returns a request-handler class. That class will serve the page only for the matching path.

**Call relations**: `BrowserHandoff.open` calls this when starting its temporary local server. The returned handler uses `_page` to get the form-posting HTML.

*Call graph*: calls 1 internal fn (_page); called by 1 (open).


##### `BrowserHandoff._responder.do_GET`  (lines 498–507)

```
def do_GET(self) -> None
```

**Purpose**: Serves the handoff page to the browser when the random path is requested. Wrong paths get a normal 404 response.

**Data flow**: It reads the incoming request path. If it matches, it sends HTML headers, writes the page bytes, and marks the handoff as delivered; otherwise it sends an error.

**Call relations**: The temporary HTTP server created by `BrowserHandoff.open` calls this for browser GET requests during portal sign-in.


##### `BrowserHandoff._responder.log_message`  (lines 509–509)

```
def log_message(self, *args: object) -> None
```

**Purpose**: Silences the temporary handoff server’s access logs. This keeps token handoff noise out of the terminal.

**Data flow**: It receives normal logging arguments from the HTTP server and intentionally does nothing. Nothing is returned and nothing is printed.

**Call relations**: The built-in HTTP server calls this whenever it would normally log a request handled by the handoff responder.


##### `BrowserHandoff._page`  (lines 513–520)

```
def _page(self) -> str
```

**Purpose**: Creates the HTML page that posts the saved token to the portal. It includes a button fallback and auto-submits with JavaScript.

**Data flow**: It reads the handoff’s portal URL and token, HTML-escapes both for safety, and returns a complete small HTML document containing a POST form.

**Call relations**: `BrowserHandoff._responder` calls this once while preparing the temporary handler used by `BrowserHandoff.open`.

*Call graph*: called by 1 (_responder); 1 external calls (escape).


##### `ingress`  (lines 524–526)

```
def ingress() -> None
```

**Purpose**: Starts the sandbox ingress service. This is the reverse proxy that lets approved traffic reach sandbox ports.

**Data flow**: It takes no command arguments and simply hands control to the sandbox ingress runner. The runner owns the long-lived network service work.

**Call relations**: This is a top-level CLI command under `main`; it is the command-line entry to the sandbox ingress subsystem.

*Call graph*: 1 external calls (run).


##### `spend_cap`  (lines 530–531)

```
def spend_cap() -> None
```

**Purpose**: Defines the command group for workspace spend caps. Spend caps limit how much model spending may happen over a time window.

**Data flow**: It does not read or write data itself. It groups subcommands that set and list cap records.

**Call relations**: Click uses this as the parent for `spend-cap set` and `spend-cap list`.


##### `spend_cap_set`  (lines 542–560)

```
def spend_cap_set(scope: str, subject_id: str, window_seconds: int, limit_micro_usd: int, on_breach: str) -> None
```

**Purpose**: Creates or updates a spend cap for a workspace, member, or agent. This gives operators a guardrail before turns are admitted or charged.

**Data flow**: It validates that subject IDs are present only when needed, converts the subject ID to a UUID when supplied, loads config, writes the cap through `_write_spend_cap`, and prints the cap amount in dollars.

**Call relations**: Users call this under the `spend-cap` group. It performs command validation and delegates database changes to `_write_spend_cap`.

*Call graph*: calls 1 internal fn (_write_spend_cap); 5 external calls (run, ClickException, echo, load_config, UUID).


##### `spend_cap_list`  (lines 564–574)

```
def spend_cap_list() -> None
```

**Purpose**: Prints all spend caps currently set for the workspace. It gives operators a quick view of active spending limits.

**Data flow**: It loads config, reads cap rows through `_read_spend_caps`, and prints either `no spend caps set` or one line per cap with scope, subject, dollar limit, window, and breach behavior.

**Call relations**: This is the read side of the `spend-cap` command group. It delegates database reading to `_read_spend_caps`.

*Call graph*: calls 1 internal fn (_read_spend_caps); 3 external calls (run, echo, load_config).


##### `_write_spend_cap`  (lines 577–631)

```
async def _write_spend_cap(config: Config, scope: str, subject: UUID | None, window_seconds: int, limit_micro_usd: int, on_breach: str) -> UUID
```

**Purpose**: Stores a spend cap in the database, updating an existing matching cap when one already exists. This avoids duplicate caps for the same scope, subject, and time window.

**Data flow**: It initializes the database, finds the workspace, checks for an existing cap with the same identity, and either updates its limit and breach action or inserts a new row with a fresh UUID. It returns the cap ID and closes the database connection.

**Call relations**: `spend_cap_set` calls this after validating command input. It uses the workspace transaction helper so the write happens inside the current workspace boundary.

*Call graph*: called by 1 (spend_cap_set); 7 external calls (insert, select, update, dispose_db, init_db, workspace_tx, uuid4).


##### `_read_spend_caps`  (lines 634–660)

```
async def _read_spend_caps(config: Config) -> list[tuple[UUID, str, UUID | None, int, int, str]]
```

**Purpose**: Reads all spend cap records for the workspace. It returns compact data for display rather than database rows.

**Data flow**: It initializes the database, finds the workspace, selects cap fields ordered by scope, converts rows into tuples, and disposes the database.

**Call relations**: `spend_cap_list` calls this and turns its tuples into terminal output.

*Call graph*: called by 1 (spend_cap_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `balance`  (lines 664–665)

```
def balance() -> None
```

**Purpose**: Defines the command group for prepaid workspace balance. Balance controls whether work has enough paid headroom to start.

**Data flow**: It does no direct work itself. It groups commands for showing, crediting, and reserving balance.

**Call relations**: Click uses this as the parent for `balance show`, `balance credit`, and `balance reserve`.


##### `balance_show`  (lines 670–685)

```
def balance_show(workspace_id: str) -> None
```

**Purpose**: Shows the current prepaid balance and related totals. It helps an operator see remaining funds, reserve headroom, lifetime grants, charges, and last purchase time.

**Data flow**: It loads config, asks `_read_balance` for the selected workspace balance, and prints either `no balance` or formatted dollar amounts.

**Call relations**: This command is the display layer for balance reads. `_read_balance` does the database work inside the right workspace scope.

*Call graph*: calls 1 internal fn (_read_balance); 3 external calls (run, echo, load_config).


##### `balance_credit`  (lines 693–706)

```
def balance_credit(granted_micro_usd: int, charged_micro_usd: int, reference: str, workspace_id: str) -> None
```

**Purpose**: Adds money or correction amounts to a workspace balance, once per reference key. The reference makes the command safe to retry without double-crediting.

**Data flow**: It validates that the granted amount is not zero, loads config, calls `_credit_balance` with the amounts and reference, and prints whether a new credit was applied or had already been recorded.

**Call relations**: This command is the operator-facing wrapper around the billing balance `credit` function, reached through `_credit_balance`.

*Call graph*: calls 1 internal fn (_credit_balance); 4 external calls (run, ClickException, echo, load_config).


##### `balance_reserve`  (lines 712–720)

```
def balance_reserve(micro_usd: int, workspace_id: str) -> None
```

**Purpose**: Sets the minimum balance headroom required before a turn may begin. This is a safety buffer against starting work without enough funds.

**Data flow**: It rejects negative values, loads config, calls `_set_reserve`, and prints the new reserve when successful. If no balance exists yet, it tells the operator to credit first.

**Call relations**: This command delegates the database update to `_set_reserve`, which works inside `_balance_scope`.

*Call graph*: calls 1 internal fn (_set_reserve); 4 external calls (run, ClickException, echo, load_config).


##### `_target_workspace`  (lines 723–745)

```
async def _target_workspace(named: str) -> UUID
```

**Purpose**: Chooses which workspace an admin command should act on. If no workspace is named, it only picks automatically when there is exactly one.

**Data flow**: It reads across workspaces through an owner transaction. If a workspace ID was supplied, it verifies that it exists and returns it; otherwise it lists workspaces and returns the only one, or raises a clear error for none or many.

**Call relations**: `_balance_scope` and `_seed_target` call this before doing workspace-specific work. It prevents multi-workspace deployments from accidentally changing the wrong workspace.

*Call graph*: called by 2 (_balance_scope, _seed_target); 4 external calls (ClickException, select, owner_tx, UUID).


##### `_balance_scope`  (lines 749–766)

```
async def _balance_scope(config: Config, named: str) -> AsyncIterator[tuple[AsyncConnection, UUID]]
```

**Purpose**: Opens a safe database context for balance commands against one workspace. It handles both single-workspace local setups and hosted deployments with an owner database.

**Data flow**: It initializes the app database, optionally initializes the owner database, resolves the target workspace, pins that workspace in context, opens a workspace transaction, yields the connection and workspace ID, and disposes database resources afterward.

**Call relations**: `_read_balance`, `_credit_balance`, and `_set_reserve` all use this shared setup so balance operations agree on workspace selection and cleanup.

*Call graph*: calls 1 internal fn (_target_workspace); called by 3 (_credit_balance, _read_balance, _set_reserve); 5 external calls (dispose_db, init_db, init_owner_db, workspace_tx, ws).


##### `_read_balance`  (lines 769–771)

```
async def _read_balance(config: Config, named: str) -> Balance | None
```

**Purpose**: Reads the balance record for one chosen workspace. It returns the billing object used by the display command.

**Data flow**: It enters `_balance_scope` to get a database connection and workspace ID, calls the billing balance reader, and returns either a balance object or `None`.

**Call relations**: `balance_show` calls this to get the data it prints.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_show); 1 external calls (read_balance).


##### `_credit_balance`  (lines 774–780)

```
async def _credit_balance(config: Config, named: str, granted_micro_usd: int, charged_micro_usd: int, reference: str) -> bool
```

**Purpose**: Applies a balance credit or correction for one chosen workspace. It returns whether this reference created a new credit.

**Data flow**: It enters `_balance_scope`, then passes the connection, workspace ID, granted amount, charged amount, and reference to the billing credit function. The result is a boolean telling the caller whether anything new was written.

**Call relations**: `balance_credit` calls this after command validation and turns the boolean into a user-facing message.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_credit); 1 external calls (credit).


##### `_set_reserve`  (lines 783–785)

```
async def _set_reserve(config: Config, named: str, reserve_micro_usd: int) -> bool
```

**Purpose**: Updates the reserve amount for one chosen workspace. The reserve is the required spending cushion before a turn can start.

**Data flow**: It enters `_balance_scope`, calls the billing reserve setter with the connection, workspace ID, and amount, and returns whether a balance row existed to update.

**Call relations**: `balance_reserve` calls this and reports success or the need to credit the balance first.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_reserve); 1 external calls (set_reserve).


##### `flags`  (lines 792–797)

```
def flags() -> None
```

**Purpose**: Defines the command group for changing feature flag values outside a full deploy. Feature flags let operators turn declared features on or off.

**Data flow**: It does no direct data work. It groups flag subcommands and documents what the group is for.

**Call relations**: Click uses this as the parent for `flags set`.


##### `flags_set`  (lines 803–825)

```
def flags_set(key: str, on: bool) -> None
```

**Purpose**: Sets one declared feature flag on or off for the configured flag backend. It refuses unknown flags so dashboards do not show settings that no active code reads.

**Data flow**: It loads config, checks that a flag backend is configured, loads active extension manifests, verifies the flag key is declared, imports the backend admin module, asks it to serve the flag value, and prints the result.

**Call relations**: This command is the CLI bridge to whichever flag service backend the deployment selected. Extension manifests define the allowed flag keys.

*Call graph*: 5 external calls (ClickException, echo, import_module, load_config, load_manifests).


##### `spend`  (lines 833–856)

```
def spend(window_seconds: int) -> None
```

**Purpose**: Prints a spending report for a recent time window. It breaks total cost down by dimensions such as member, agent, origin, and price version.

**Data flow**: It loads config, reads a `SpendReport` through `_read_spend`, converts micro-dollars into dollars, and prints a formatted summary and sections.

**Call relations**: This top-level command delegates all accounting queries to `_read_spend` and focuses on making the report readable.

*Call graph*: calls 1 internal fn (_read_spend); 3 external calls (run, echo, load_config).


##### `_read_spend`  (lines 859–866)

```
async def _read_spend(config: Config, window_seconds: int) -> SpendReport
```

**Purpose**: Builds the spend rollup for the current workspace and time window. It is the database-reading half of the `spend` command.

**Data flow**: It initializes the database, finds the workspace ID, creates a spend rollup reader for that workspace, reads the requested window, returns the report, and disposes the database.

**Call relations**: `spend` calls this and then prints the returned report.

*Call graph*: called by 1 (spend); 5 external calls (__init__, select, dispose_db, init_db, workspace_tx).


##### `transcript_reads`  (lines 874–887)

```
def transcript_reads(limit: int) -> None
```

**Purpose**: Lists recorded admin disclosures for reading another member’s private transcript. This gives operators an audit view of sensitive transcript access.

**Data flow**: It validates that the limit is positive, loads config, reads audit entries through `_read_transcript_accesses`, and prints either no records or a newest-first list with reader, subject, conversation, and time.

**Call relations**: This top-level command is the display wrapper around `_read_transcript_accesses`.

*Call graph*: calls 1 internal fn (_read_transcript_accesses); 4 external calls (run, ClickException, echo, load_config).


##### `_read_transcript_accesses`  (lines 890–924)

```
async def _read_transcript_accesses(config: Config, limit: int) -> list[tuple[str, str, UUID, datetime]]
```

**Purpose**: Reads transcript-access audit records from the database. It joins member records so the command can show emails instead of only IDs.

**Data flow**: It initializes the database, aliases the member table for reader and subject, selects recent transcript access rows for the workspace, converts them to tuples, and disposes the database.

**Call relations**: `transcript_reads` calls this and formats the returned audit tuples for the terminal.

*Call graph*: called by 1 (transcript_reads); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `grants`  (lines 928–940)

```
def grants() -> None
```

**Purpose**: Lists OAuth accounts that have been granted to agents. OAuth is the web authorization flow used when a user connects an external account.

**Data flow**: It loads config, gets grant summaries through `_read_grants`, and prints either `no grants` or one line per grant with agent, provider, account, sharing mode, and date.

**Call relations**: This command is the operator-facing readout for account grants. `_read_grants` gathers the data.

*Call graph*: calls 1 internal fn (_read_grants); 3 external calls (run, echo, load_config).


##### `_read_grants`  (lines 943–950)

```
async def _read_grants(config: Config) -> tuple[GrantSummary, ...]
```

**Purpose**: Reads grant summaries for the current workspace. It first finds the workspace, then asks the access-grants subsystem for human-friendly summaries.

**Data flow**: It initializes the database, reads the workspace ID in a workspace transaction, calls the grant summary helper, returns the summaries, and disposes the database.

**Call relations**: `grants` calls this and prints each returned summary.

*Call graph*: called by 1 (grants); 5 external calls (select, dispose_db, init_db, workspace_tx, workspace_grant_summaries).


##### `credential`  (lines 954–956)

```
def credential() -> None
```

**Purpose**: Defines the command group for encrypted bring-your-own-key credential slots. These are secret values declared by extensions.

**Data flow**: It does not read or write secrets itself. It groups commands for setting and listing credential slot status.

**Call relations**: Click uses this as the parent for `credential set` and `credential list`.


##### `credential_set`  (lines 961–984)

```
def credential_set(slot: str) -> None
```

**Purpose**: Stores one secret value in an allowed credential slot. It avoids exposing the secret in command history by reading from a hidden prompt or standard input.

**Data flow**: It loads config, checks that the slot is declared, checks that the slot may be filled by an operator/member, reads the encryption key from the environment, reads the secret value, writes it through `_write_credential`, and prints a confirmation without showing the value.

**Call relations**: This command uses `_declared_slots` and `_fillable_slots` for safety checks, then delegates encrypted storage to `_write_credential`.

*Call graph*: calls 3 internal fn (_declared_slots, _fillable_slots, _write_credential); 5 external calls (run, ClickException, echo, prompt, load_config).


##### `credential_list`  (lines 988–998)

```
def credential_list() -> None
```

**Purpose**: Shows which declared credential slots are set or unset, without revealing any secret values. This helps operators know what configuration is missing.

**Data flow**: It loads config, reads declared slots from extension manifests, reads stored slot names from the database, and prints each slot with its owning extension and set/unset status.

**Call relations**: This command combines `_declared_slots` and `_read_stored_slots` to produce a safe inventory.

*Call graph*: calls 2 internal fn (_declared_slots, _read_stored_slots); 3 external calls (run, echo, load_config).


##### `_declared_slots`  (lines 1001–1006)

```
def _declared_slots(config: Config) -> dict[str, str]
```

**Purpose**: Collects the credential slots declared by active extensions. It returns which extension owns each slot.

**Data flow**: It loads extension manifests for the configured pack and builds a dictionary from slot name to manifest name. If manifest loading fails, it turns that into a command-line error.

**Call relations**: `credential_set` uses this to reject unknown slots. `credential_list` uses it to know what slots should be displayed.

*Call graph*: called by 2 (credential_list, credential_set); 2 external calls (ClickException, load_manifests).


##### `_fillable_slots`  (lines 1009–1018)

```
def _fillable_slots(config: Config) -> frozenset[str]
```

**Purpose**: Collects only the credential slots that are meant to be filled by a person. Some slots are written by the deployment itself and should not accept typed values.

**Data flow**: It loads extension manifests, filters credential declarations to those marked `member_filled`, and returns their names as a set. Manifest errors become command-line errors.

**Call relations**: `credential_set` calls this after confirming the slot exists, so it can reject slots that should never be manually entered.

*Call graph*: called by 1 (credential_set); 2 external calls (ClickException, load_manifests).


##### `_write_credential`  (lines 1021–1028)

```
async def _write_credential(config: Config, key: str, slot: str, value: str) -> None
```

**Purpose**: Encrypts and stores a credential value for the workspace. Encryption keeps the secret protected at rest in the database.

**Data flow**: It initializes the database, finds the workspace ID, builds a credential store with the supplied Fernet encryption key, writes the slot value, and disposes the database.

**Call relations**: `credential_set` calls this after collecting and validating the secret value.

*Call graph*: called by 1 (credential_set); 6 external calls (__init__, Fernet, select, dispose_db, init_db, workspace_tx).


##### `_read_stored_slots`  (lines 1031–1045)

```
async def _read_stored_slots(config: Config) -> frozenset[str]
```

**Purpose**: Reads the names of credential slots that currently have stored values. It does not read or return the secret values themselves.

**Data flow**: It initializes the database, finds the workspace ID, selects slot names from the credential table for that workspace, returns them as a set, and disposes the database.

**Call relations**: `credential_list` calls this to mark declared slots as set or unset.

*Call graph*: called by 1 (credential_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `ext`  (lines 1049–1050)

```
def ext() -> None
```

**Purpose**: Defines the command group for extension store operations. Extensions add capabilities to a UFO pack.

**Data flow**: It does not perform store work itself. It groups commands for searching, installing, and removing extensions.

**Call relations**: Click uses this as the parent for `ext search`, `ext install`, and `ext remove`.


##### `_store`  (lines 1053–1056)

```
def _store(config: Config) -> ExtensionStore
```

**Purpose**: Builds the extension store object for the current configuration. It refuses extension commands when no store is configured.

**Data flow**: It reads the extension store location from config, reads the catalog, finds the lockfile path, and returns an `ExtensionStore` connected to both. If no store is configured, it raises a command error.

**Call relations**: All extension subcommands call this before searching or changing extension pins.

*Call graph*: called by 3 (ext_install, ext_remove, ext_search); 4 external calls (__init__, ClickException, lockfile_path, read_catalog).


##### `ext_search`  (lines 1061–1074)

```
def ext_search(query: str) -> None
```

**Purpose**: Searches the extension catalog and shows what is available. It marks whether each result is already installed, available, or bundle-only.

**Data flow**: It loads config, builds the store, runs the search with the query string, and prints either no matches or one formatted line per listing.

**Call relations**: This command is the read-only extension discovery path and relies on `_store` for catalog access.

*Call graph*: calls 1 internal fn (_store); 2 external calls (echo, load_config).


##### `ext_install`  (lines 1079–1085)

```
def ext_install(name: str) -> None
```

**Purpose**: Pins an extension from the store into the lockfile. The next server start will load the pinned extension.

**Data flow**: It loads config, builds the store, asks it to install the named extension, catches store errors as command errors, and prints the installed name, version, and digest.

**Call relations**: This command uses `_store` to update the deployment’s extension lockfile.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `ext_remove`  (lines 1090–1096)

```
def ext_remove(name: str) -> None
```

**Purpose**: Removes an extension pin from the lockfile. The next server start will stop loading that extension.

**Data flow**: It loads config, builds the store, asks it to remove the named extension, reports store errors clearly, and prints confirmation.

**Call relations**: This command uses `_store` for lockfile access, mirroring the install path in reverse.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `_ufo_project_dir`  (lines 1102–1113)

```
def _ufo_project_dir() -> Path
```

**Purpose**: Finds the source project directory needed to build the UFO wheel for a bundle. It searches upward from this file instead of trusting the current shell directory.

**Data flow**: It walks parent directories looking for a `pyproject.toml` whose project name is `ufo`. It returns that directory, or raises a command error if the installed package has no source project around it.

**Call relations**: `bundle` calls this before running the wheel build command.

*Call graph*: called by 1 (bundle); 3 external calls (ClickException, Path, loads).


##### `bundle`  (lines 1125–1155)

```
def bundle(out: Path, client_binary: Path) -> None
```

**Purpose**: Creates a runnable bundle of the current deployment. The bundle includes the built UFO wheel, client binary, config, extension catalog information, and locked extension pins.

**Data flow**: It loads config, optionally reads the extension catalog, runs `uv build` to produce a wheel, verifies the wheel exists, builds the bundle artifact, and prints the output path and pinned extensions.

**Call relations**: This top-level command delegates source discovery to `_ufo_project_dir` and bundle assembly to the `Bundle` class.

*Call graph*: calls 1 internal fn (_ufo_project_dir); 8 external calls (__init__, ClickException, echo, run, wheel_name, config_path, load_config, read_catalog).


##### `turn`  (lines 1159–1160)

```
def turn() -> None
```

**Purpose**: Defines the command group for actions on a single turn. A turn is one unit of agent work in a conversation.

**Data flow**: It does not act on data by itself. It groups commands that cancel a turn or print its recorded steps.

**Call relations**: Click uses this as the parent for `turn cancel` and `turn steps`.


##### `turn_cancel`  (lines 1166–1176)

```
def turn_cancel(turn_id: str, workspace_id: str) -> None
```

**Purpose**: Cancels one turn if it has not already reached a final state. This gives operators a way to stop stuck or repeatedly recovering work.

**Data flow**: It converts the turn ID to a UUID, loads config, calls `_cancel_turn`, and prints whether the turn was cancelled or was already terminal.

**Call relations**: This command is the user-facing wrapper around `_cancel_turn`, which performs workspace resolution and durable cancellation.

*Call graph*: calls 1 internal fn (_cancel_turn); 4 external calls (run, echo, load_config, UUID).


##### `_cancel_turn`  (lines 1179–1213)

```
async def _cancel_turn(config: Config, turn_id: UUID, named_workspace: str) -> bool
```

**Purpose**: Finds the workspace for a turn and asks the durable workflow system to cancel it. It also verifies the turn exists when a workspace is named.

**Data flow**: It initializes the database, resolves the workspace either from the option or by looking up the turn through the owner database, creates a replay-safe durability client, pins the workspace context, optionally verifies the turn, calls the turn cancellation helper, returns whether cancellation happened, and disposes the database.

**Call relations**: `turn_cancel` calls this. It bridges command input, database lookup, workspace context, and the runtime cancellation subsystem.

*Call graph*: called by 1 (turn_cancel); 11 external calls (ClickException, select, dispose_db, init_db, init_owner_db, owner_tx, workspace_tx, replay_safe_client, cancel_one_turn, ws (+1 more)).


##### `turn_steps`  (lines 1219–1227)

```
def turn_steps(turn_id: str, workspace_id: str) -> None
```

**Purpose**: Prints the durable step log for one turn. This helps operators see what actually happened even if the conversation transcript no longer shows every detail.

**Data flow**: It converts the turn ID to a UUID, loads config, and calls `_print_turn_steps`. It prints nothing itself beyond what the helper prints.

**Call relations**: This command is the entry point for inspecting recorded turn execution. `_print_turn_steps` does the database and durability reads.

*Call graph*: calls 1 internal fn (_print_turn_steps); 3 external calls (run, load_config, UUID).


##### `_print_turn_steps`  (lines 1230–1262)

```
async def _print_turn_steps(config: Config, turn_id: UUID, named_workspace: str) -> None
```

**Purpose**: Resolves a turn’s workspace, reads its durable step history, and prints it. It handles both named-workspace and owner-database lookup modes.

**Data flow**: It initializes databases, resolves the workspace, creates a replay-safe durability client, reads the turn’s running attempt if present, asks `DurableTurnSteps` for recorded steps, sends them to `_echo_turn_steps`, and disposes the database.

**Call relations**: `turn_steps` calls this. It hands formatting to `_echo_turn_steps` after gathering the raw step records.

*Call graph*: calls 1 internal fn (_echo_turn_steps); called by 1 (turn_steps); 11 external calls (__init__, ClickException, select, dispose_db, init_db, init_owner_db, owner_tx, workspace_tx, replay_safe_client, ws (+1 more)).


##### `_echo_turn_steps`  (lines 1265–1278)

```
def _echo_turn_steps(steps: tuple[TurnStep, ...]) -> None
```

**Purpose**: Formats and prints a turn’s recorded steps. It includes step timing and the messages captured inside each step.

**Data flow**: It receives a tuple of turn steps. If empty, it prints a simple message; otherwise it prints each step header and each message, using `_echo_block` for structured message blocks.

**Call relations**: `_print_turn_steps` calls this after reading durable step data. It delegates individual block formatting to `_echo_block`.

*Call graph*: calls 1 internal fn (_echo_block); called by 1 (_print_turn_steps); 1 external calls (echo).


##### `_echo_block`  (lines 1281–1291)

```
def _echo_block(block: object) -> str
```

**Purpose**: Turns a structured model message block into readable text for the terminal. It knows about text, tool calls, and tool results.

**Data flow**: It receives one block object, pattern-matches its type, and returns a string: plain text, a compact JSON tool call, a tool result summary, or the unknown type name.

**Call relations**: `_echo_turn_steps` calls this for non-string message content while printing turn histories.

*Call graph*: called by 1 (_echo_turn_steps); 1 external calls (dumps).


##### `seed`  (lines 1295–1296)

```
def seed() -> None
```

**Purpose**: Defines the command group for writing demonstration content into a workspace. Seed data helps humans review UI behavior with known examples.

**Data flow**: It does no direct work. It groups seed commands such as the kitchen-sink conversation writer.

**Call relations**: Click uses this as the parent for `seed kitchen-sink`.


##### `seed_kitchen_sink`  (lines 1301–1310)

```
def seed_kitchen_sink(workspace_id: str) -> None
```

**Purpose**: Writes a demonstration conversation that contains many shapes the portal UI needs to render. It prints the URL fragment where reviewers can open it.

**Data flow**: It loads config, calls `_seed_kitchen_sink` with an optional workspace ID, receives the created conversation ID, and prints the portal path for that conversation.

**Call relations**: This command is the user-facing seed command. `_seed_kitchen_sink` prepares databases and blob storage, then writes the content.

*Call graph*: calls 1 internal fn (_seed_kitchen_sink); 3 external calls (run, echo, load_config).


##### `_seed_kitchen_sink`  (lines 1313–1324)

```
async def _seed_kitchen_sink(config: Config, named: str) -> UUID
```

**Purpose**: Sets up database and blob-store access for writing the kitchen-sink demo conversation. Blob storage is where larger attached content can live.

**Data flow**: It initializes the app database and optional owner database, builds a workspace blob store from config, calls `_seed_target`, returns the created conversation ID, and disposes the database.

**Call relations**: `seed_kitchen_sink` calls this. It delegates actual workspace/member/agent lookup and content writing to `_seed_target`.

*Call graph*: calls 1 internal fn (_seed_target); called by 1 (seed_kitchen_sink); 5 external calls (__init__, blob_store_for, dispose_db, init_db, init_owner_db).


##### `_seed_target`  (lines 1327–1358)

```
async def _seed_target(blob: WorkspaceBlobStore, named: str) -> UUID
```

**Purpose**: Finds the target workspace, main agent, and first member, then writes the kitchen-sink conversation. It refuses to seed if no member exists.

**Data flow**: It resolves the workspace, pins workspace context, reads the main agent and earliest member from the database, and passes those IDs plus email and blob store to `KitchenSink.write`. It returns the new conversation ID.

**Call relations**: `_seed_kitchen_sink` calls this after setup. It uses `_target_workspace` for safe workspace selection and the onboarding seed helper for the actual demo content.

*Call graph*: calls 1 internal fn (_target_workspace); called by 1 (_seed_kitchen_sink); 5 external calls (__init__, ClickException, select, workspace_tx, ws).


### `core/src/ufo/__init__.py`

`other` · `import/package discovery`

In Python, a folder usually needs an `__init__.py` file to be treated as a package: a named collection of related code that can be imported elsewhere. This file is empty, which means it does not set up configuration, expose shortcuts, or run any startup code. Its job is structural rather than behavioral. Think of it like a label on a drawer: it does not contain instructions, but it tells Python, tools, and readers that the `ufo` directory is meant to be used as one coherent package. Without this file, some import styles or tooling may not recognize the directory in the intended way, especially in environments that expect traditional Python packages.


### Deployment and Fleet Census
These modules prepare deployable runtime bundles and report product-funnel status across workspaces.

### `core/src/ufo/bundle.py`

`orchestration` · `bundle creation`

This file supports the `ufoctl bundle` command. Its job is to turn the current UFO setup into something portable and repeatable, like packing a working kitchen into a sealed meal kit: the recipe, ingredients, and tools are all fixed so the result is the same wherever it is opened.

The bundle directory contains a Dockerfile, a copied configuration file, a generated lockfile, and the sandbox client binary. The lockfile is especially important. It records which extensions should be active and stores a digest, which is a content fingerprint, for each one. That means the bundled runtime can later check that it is loading the exact extension code that was bundled, not just something with the same name.

The `Bundle` class is the main worker. First it decides which extensions must be pinned. It starts from the existing lockfile if one exists, otherwise from currently discovered installed extensions. If an extension catalog is available, it also adds entries marked as disabled in the store because those are meant to be installed only at bundle time, not dynamically at runtime. Then it reads the built wheel file, extracts the files for each extension, computes their digests, and writes the final bundle files.

The generated Dockerfile installs the UFO wheel, copies in the fixed config and lockfile, installs the sandbox client binary, and starts `ufoctl serve` by default.

#### Function details

##### `wheel_name`  (lines 35–37)

```
def wheel_name() -> str
```

**Purpose**: Builds the expected filename for the UFO Python wheel that will be copied into the Docker image. A wheel is a packaged Python distribution file, similar to an installable zip file.

**Data flow**: It reads the current UFO version from `ufo_version()` → formats that version into a standard wheel filename like `ufo-<version>-py3-none-any.whl` → returns that filename as text.

**Call relations**: The Dockerfile generator calls this when it writes the `COPY` and `pip install` lines, so the generated image recipe points at the exact wheel name the bundle expects.

*Call graph*: called by 1 (_dockerfile); 1 external calls (ufo_version).


##### `Bundle.build`  (lines 61–81)

```
def build(self) -> BundleResult
```

**Purpose**: Creates the complete bundle directory. It writes the pinned config, generated lockfile, sandbox client binary, and Dockerfile, then returns a summary of what it produced.

**Data flow**: It starts with the `Bundle` object's paths: source config, output folder, built wheel, sandbox client, and optional extension catalog → asks `_pins` to decide and fingerprint the extension set → creates the output folder → copies the config and client binary → writes a new lockfile containing the UFO version and extension pins → writes the Dockerfile text from `_dockerfile` → returns a `BundleResult` with paths to all produced files and the pins used.

**Call relations**: This is the main action a higher-level bundle command would call. It delegates the careful extension selection and digest work to `_pins`, delegates image recipe text to `_dockerfile`, and packages the results into `BundleResult` for the caller to report or use.

*Call graph*: calls 2 internal fn (_dockerfile, _pins); 3 external calls (__init__, __init__, ufo_version).


##### `Bundle._pins`  (lines 83–127)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: Decides which extensions belong in the bundle and records a content fingerprint for each one. This is what makes the bundle repeatable instead of depending loosely on whatever happens to be installed later.

**Data flow**: It looks at installed extensions discovered in the current environment and checks whether an existing lockfile is present → if a lockfile exists, it starts from the names already locked; otherwise it starts from all discovered extensions → if a catalog is available, it adds catalog entries marked disabled, because those are bundle-only additions → for each chosen extension name, it finds the installed extension metadata, opens the built wheel, gathers that extension's package files while skipping cache and compiled bytecode files, computes a digest from those bytes, and creates an `ExtensionPin` with name, version, and digest → returns all pins as an immutable tuple. If a named extension is not installed or its files are missing from the wheel, it raises an error instead of making an incomplete bundle.

**Call relations**: Called by `Bundle.build` before any lockfile is written. It relies on the extension loader to discover installed extensions, read any existing lockfile, and compute digests, and it uses the wheel file as the source of truth for the bytes that will actually be installed inside the image.

*Call graph*: called by 1 (build); 7 external calls (__init__, Path, discovered, extension_content_digest, lockfile_path, read_lockfile, ZipFile).


##### `Bundle._dockerfile`  (lines 129–145)

```
def _dockerfile(self) -> str
```

**Purpose**: Writes the Dockerfile recipe for the bundled Docker image. The recipe tells Docker how to install UFO, copy in the fixed runtime files, and start the server.

**Data flow**: It uses fixed bundle names, the base Python image name, and the wheel filename from `wheel_name()` → assembles Dockerfile lines that set the working directory, set environment variables for the config and lockfile, install the wheel with `pip`, copy the config and lockfile, install the sandbox client binary, and set the default command → returns the full Dockerfile as one text string.

**Call relations**: Called by `Bundle.build` when it is time to write the Dockerfile into the output directory. It calls `wheel_name` so the image recipe matches the wheel naming convention used by the rest of the bundle process.

*Call graph*: calls 1 internal fn (wheel_name); called by 1 (build).


### `core/src/ufo/product.py`

`domain_logic` · `scheduled metrics tick`

This file exists so the product dashboard can answer a simple question: how far has each workspace gotten? Instead of saving a new event every time something happens, it re-reads the current database records on a regular schedule. That means if the team later changes what “active” or “adopted” means, the next run can recalculate the answer from the underlying facts.

The main idea is a census. For the workspace currently being processed, the code asks yes-or-no questions: does it have a seated member, a connector grant, an invited member, a user-created app, a member chat, recent activity, or a paid purchase? Each answer becomes a metric value of 1 or 0. Added across all workspaces, those values become funnel totals.

It also counts what the workspace has attached, such as installed surfaces, proven addresses, credential slots, connector providers, and provisioned apps. These are emitted with labels like “kind” and “name,” so the metrics can say not just “this workspace connected something,” but “it connected Slack” or “it has this kind of surface.”

A key safety detail is that every database query explicitly filters by workspace ID. The surrounding system also binds execution to a workspace, but this file does not rely on that alone, because counting another workspace’s rows here would silently inflate product metrics.

#### Function details

##### `product_census`  (lines 50–152)

```
async def product_census() -> None
```

**Purpose**: Counts the current workspace’s product funnel stages and attached integrations, then emits those counts as metrics. It is used when the system wants a fresh snapshot of product adoption without relying on stored tracking events.

**Data flow**: It starts with the workspace ID from the current workspace context and the current time. It builds database questions for funnel stages, such as “has any member been seated?” and “has there been a member chat in the last day?” It also builds one combined query for attached items, such as surfaces, credentials, connectors, and apps. It opens a workspace database transaction, runs those queries, then turns the results into emitted metrics: one metric per funnel stage and one metric per attached item. The database is only read; the visible output is metrics sent to the observability system.

**Call relations**: This function is the whole census flow for the file. It uses SQL-building helpers from SQLAlchemy to create the database questions, reads through `ufo.db.workspace_tx`, gets the current workspace through `ws_current`, and sends the final numbers to `emit_metric`. It is meant to be called by the scheduled product census job, so each workspace contributes its own small set of measurements during each tick.

*Call graph*: 10 external calls (now, timedelta, and_, exists, literal, select, union_all, workspace_tx, emit_metric, ws_current).


### Extension Probe
The sample skill probe gives deployment checks a minimal executable extension target.

### `extensions/sample/skills/sample_skill/probe.py`

`entrypoint` · `startup or health-check probing`

This file is like a small “is the light on?” check for the sample skill. It does not define any classes or functions, and it does not do any real skill work. Its only action is to print the text `sample-skill-probe-ok`.

That matters because probes are often used by tools, tests, or setup checks to make sure a component is present and runnable. If an outside process runs this file and sees the expected message, it knows the sample skill’s probe script was found and Python could execute it. If the file were missing, broken, or unable to run, that check would fail.

One important detail is that the print happens immediately when the file is executed, because the statement sits at the top level of the file. There is no separate function to call. This keeps the probe deliberately simple: run the file, look for the message, and move on.


### Sandbox Validation
The sandbox scripts verify that execution images and HTTPS proxy enforcement are ready before normal workloads run.

### `sandbox/build_template.py`

`entrypoint` · `build/deploy time`

This file is the build recipe and command-line tool for UFO's sandbox environment. The sandbox is the isolated computer where agent-written code runs, so it needs the right command-line tools, Python and Node packages, browser support, document tools, the compiled `ufo` client, and the system skill bundle already installed. Without this file, different sandbox backends could end up with different tools, or a published cloud template could be stale without anyone noticing.

The file defines one shared set of layers, like a packing list for a prepared workbench. That packing list is applied either to an E2B base template for cloud sandboxes or to a Docker base image for local/container use. It also records a build digest, which is a fingerprint of the recipe and important inputs. Later, the script can boot a live template and compare that fingerprint to the current source to catch drift.

There are several modes. With no arguments, it stages build artifacts, builds E2B templates for each size tier, publishes them, and verifies they can actually run the expected tools. `--check` only compares live templates against the current recipe. `--dockerfile` prints the Dockerfile. `--build-docker` builds the Docker image locally.

#### Function details

##### `template_name`  (lines 259–260)

```
def template_name(size: str) -> str
```

**Purpose**: Creates the published E2B template name for a sandbox size, such as small, medium, or large. This keeps the naming pattern in one place.

**Data flow**: It receives a size name as text, attaches it to the fixed UFO sandbox template prefix, and returns the full template name. It does not read or change anything else.

**Call relations**: The main command flow calls this when it needs to check, build, publish, or report a specific size tier. The result is then used as the name passed to E2B-related build and verification steps.

*Call graph*: called by 1 (main).


##### `client_definition`  (lines 263–289)

```
def client_definition() -> dict[str, str]
```

**Purpose**: Describes the compiled `ufo` client that will be baked into the sandbox, in a way that can be included in the build fingerprint. It hashes the client's source files rather than the finished binary, because compiled binaries may differ byte-for-byte between machines even when built from the same code.

**Data flow**: It reads the important client crate files and source directories, feeds their names and bytes into a SHA-256 hash, and returns a small dictionary containing the binary name, build target, and source hash. It does not build the client itself.

**Call relations**: The build fingerprint step calls this while deciding whether the sandbox definition has changed. It relies on the external hashing function, then hands its result back to `build_definition_digest` as one part of the overall recipe fingerprint.

*Call graph*: called by 1 (build_definition_digest); 1 external calls (sha256).


##### `stage_client_binary`  (lines 292–303)

```
def stage_client_binary() -> Path
```

**Purpose**: Copies the already-built `ufo` client binary into a predictable place inside the repository so the sandbox image build can include it. This makes missing or stale build artifacts fail clearly during image construction.

**Data flow**: It asks the client build helper for the path to a Linux-compatible client binary, creates the sandbox artifact directory if needed, copies the binary there, marks it executable, and returns the staged path.

**Call relations**: `main` calls this before publishing E2B templates, and `build_docker_image` calls it before running Docker. It delegates the question of where the compiled client comes from to `ufo.harness.sandbox.client_binary.client_binary`, then prepares the file for later `COPY` instructions emitted by `apply_layers`.

*Call graph*: called by 2 (build_docker_image, main); 2 external calls (copyfile, client_binary).


##### `system_skill_bundle`  (lines 307–324)

```
def system_skill_bundle() -> SystemSkillBundle
```

**Purpose**: Collects all built-in system skills and packages them as one bundle for the sandbox image. A skill is a reusable capability the agent can call on, such as document or media handling.

**Data flow**: It searches the repository for `SKILL.md` files in the core, extensions, and packs areas, filters out anything under `node_modules`, finds the top-level skill directories, discovers the skills in them, and returns a `SystemSkillBundle`. Because it is cached, repeated calls reuse the same bundle during one run.

**Call relations**: `stage_system_skills` calls this to get the archive bytes that will be copied into the image. `build_definition_digest` also calls it so the build fingerprint changes when the system skill contents change. It hands discovered skills to `SystemSkillBundle.from_skills` to create the final packaged bundle.

*Call graph*: calls 1 internal fn (from_skills); called by 2 (build_definition_digest, stage_system_skills); 1 external calls (discover_skills).


##### `stage_system_skills`  (lines 327–330)

```
def stage_system_skills() -> Path
```

**Purpose**: Writes the packaged system skills to the build-artifacts directory so the image build can copy them in. This gives both the E2B and Docker builds the same skill bundle.

**Data flow**: It creates the artifact directory if needed, asks `system_skill_bundle` for the zip archive bytes, writes those bytes to `system-skills.zip`, and returns the archive path.

**Call relations**: `main` calls this before publishing E2B templates, and `build_docker_image` calls it before building Docker. The staged zip is later referenced by `apply_layers`, which copies and unpacks it inside the sandbox image.

*Call graph*: calls 1 internal fn (system_skill_bundle); called by 2 (build_docker_image, main).


##### `build_definition_digest`  (lines 333–375)

```
def build_definition_digest(sizing: Sizing | None) -> str
```

**Purpose**: Creates a single fingerprint for the sandbox build recipe. This is the drift detector: if tools, environment variables, client source, system skills, copied modules, or E2B sizing change, the fingerprint changes too.

**Data flow**: It receives either a sandbox size setting or `None` for Docker. It gathers the base image/template, users, startup and readiness commands, package lists, environment settings, runtime directory permissions, client source fingerprint, skill bundle digest, module content hashes, and optional CPU/memory sizing. It serializes that information in a stable order, hashes it, and returns a `sha256:` digest string.

**Call relations**: `e2b_template` and `pod_dockerfile` call this before applying layers, so the digest can be baked into the image. `main` also calls it in check mode to compare source truth with the live E2B template. It calls `client_definition` and `system_skill_bundle` to include those moving parts in the fingerprint.

*Call graph*: calls 2 internal fn (client_definition, system_skill_bundle); called by 3 (e2b_template, main, pod_dockerfile); 2 external calls (sha256, dumps).


##### `apply_layers`  (lines 378–423)

```
def apply_layers(builder: TemplateBuilder, digest: str) -> TemplateFinal
```

**Purpose**: Applies the shared sandbox build recipe to a template builder. This is the central place where the image gets its tools, environment, client binary, system skills, permissions, startup command, and readiness check.

**Data flow**: It receives a template builder and the build digest to bake in. It switches to the build user, runs installation commands for system packages, GitHub CLI, Node, Python packages, npm packages, and Playwright Chromium, creates important directories, writes the digest file, sets environment variables, copies in and unpacks system skills, copies in the `ufo` client and helper modules, switches back to the runtime user, and returns the finalized template definition.

**Call relations**: Both `e2b_template` and `pod_dockerfile` call this, which is how the E2B and Docker outputs stay in sync. It talks to the E2B SDK's template builder methods for running commands, copying files, setting environment variables, setting the active user, and defining the start command.

*Call graph*: called by 2 (e2b_template, pod_dockerfile); 5 external calls (copy, run_cmd, set_envs, set_start_cmd, set_user).


##### `e2b_template`  (lines 426–428)

```
def e2b_template(size: str) -> TemplateFinal
```

**Purpose**: Builds the E2B version of the sandbox definition for one size tier. E2B is the cloud sandbox provider, and its CPU and memory size are fixed at template build time.

**Data flow**: It receives a size name, creates a template builder rooted at the repository and based on the E2B base template, computes the digest for that size's CPU and memory, applies the shared layers, and returns the final template definition.

**Call relations**: `main` calls this during the normal publish flow before asking the E2B SDK to build the template. It calls `build_definition_digest` for the size-specific fingerprint and `apply_layers` for the shared image contents.

*Call graph*: calls 2 internal fn (apply_layers, build_definition_digest); called by 1 (main); 1 external calls (Template).


##### `pod_dockerfile`  (lines 431–433)

```
def pod_dockerfile() -> str
```

**Purpose**: Produces the Dockerfile for the Docker-based sandbox image using the same recipe as the E2B template. This lets Docker deployments build the matching image without needing an E2B account.

**Data flow**: It creates a template builder from the Docker base image, computes a build digest with no E2B sizing, applies the shared layers, converts the resulting template definition into Dockerfile text, and returns that text.

**Call relations**: `main` calls this when the user asks to print the Dockerfile. `build_docker_image` calls it when it needs to feed a Dockerfile into `docker build`. It shares the core build logic through `apply_layers` and `build_definition_digest`.

*Call graph*: calls 2 internal fn (apply_layers, build_definition_digest); called by 2 (build_docker_image, main); 2 external calls (Template, to_dockerfile).


##### `build_docker_image`  (lines 436–449)

```
def build_docker_image() -> None
```

**Purpose**: Builds the local Docker sandbox image from the shared sandbox definition. It is the Docker-only build path and does not require publishing anything to E2B.

**Data flow**: It first stages the compiled client binary and system skill archive. Then it renders the Dockerfile text, sends that text to `docker build` through standard input, tags the resulting image with the configured tag, and prints the tag on success. If Docker returns an error code, it exits with a clear failure message.

**Call relations**: `main` calls this when `--build-docker` is used. It calls `stage_client_binary` and `stage_system_skills` to prepare files that the Docker build will copy, calls `pod_dockerfile` to get the Dockerfile, and hands the actual image build to the external Docker command through `subprocess.run`.

*Call graph*: calls 3 internal fn (pod_dockerfile, stage_client_binary, stage_system_skills); called by 1 (main); 1 external calls (run).


##### `verify_published_template`  (lines 452–467)

```
def verify_published_template(name: str) -> None
```

**Purpose**: Checks that a newly published E2B template can actually start and has the expected tools installed. This prevents a broken sandbox image from being reported as successfully published.

**Data flow**: It receives a template reference, starts a temporary sandbox from it, runs the same readiness command that is baked into the image, always kills the sandbox afterward, and raises an error if the command fails or returns a nonzero exit code. If everything passes, it returns nothing.

**Call relations**: `main` calls this immediately after building each E2B template in the publish flow. It uses `Sandbox.create` to boot the published template and then runs the readiness probe inside that sandbox before the build is accepted.

*Call graph*: called by 1 (main); 1 external calls (create).


##### `check_published_template`  (lines 470–490)

```
def check_published_template(name: str, expected: str) -> None
```

**Purpose**: Checks whether a live E2B template matches the current source-defined build recipe, without publishing anything. This is a safety gate for continuous integration: it fails when someone changed the recipe but has not republished the template.

**Data flow**: It receives a template name and the digest expected from current source. It starts a temporary sandbox, reads the digest file baked into that live template, kills the sandbox, and compares the live value to the expected value. If the digest is missing or different, it raises an error explaining that the template must be republished.

**Call relations**: `main` calls this for every sandbox size when `--check` is used. It relies on `Sandbox.create` to inspect the already-published template, while `main` supplies the expected digest from `build_definition_digest`.

*Call graph*: called by 1 (main); 1 external calls (create).


##### `main`  (lines 493–536)

```
def main() -> None
```

**Purpose**: Provides the command-line interface for building, checking, printing, or locally building the sandbox image. It is the traffic controller that chooses the right workflow from the user's flags.

**Data flow**: It reads command-line arguments. If `--dockerfile` is set, it prints the Dockerfile. If `--build-docker` is set, it builds the Docker image. If `--check` is set, it computes expected digests and checks each live E2B template. With no special flag, it stages artifacts, builds each E2B size template, verifies each published result, and prints the published references.

**Call relations**: This is called when the script is run directly. It coordinates the helper functions: `pod_dockerfile` for Dockerfile output, `build_docker_image` for local Docker builds, `check_published_template` for drift checks, `stage_client_binary` and `stage_system_skills` for build inputs, `e2b_template` for template definitions, `template_name` for naming, and `verify_published_template` for the publish gate.

*Call graph*: calls 9 internal fn (build_definition_digest, build_docker_image, check_published_template, e2b_template, pod_dockerfile, stage_client_binary, stage_system_skills, template_name, verify_published_template); 2 external calls (ArgumentParser, build).


### `sandbox/proxy_gate.py`

`entrypoint` · `deployment/startup validation`

This script acts like a gate before trusting the off-cluster sandbox’s outbound HTTPS path. In plain terms, it asks: “If code inside a sandbox tries to reach the outside internet through our proxy, does TLS work correctly, and does the proxy enforce access rules?” Without this check, a broken certificate setup or proxy route could go unnoticed until real sandbox work starts failing or bypassing controls.

The script receives a public HTTPS proxy URL, reads the proxy certificate from an environment variable, and chooses an E2B sandbox template from another environment variable. It then creates a temporary sandbox with enough time to install the certificate and wait for the proxy to become ready.

Inside that sandbox, it writes the certificate to a staging path and runs the certificate-install command as root. Then it repeatedly runs a small Python probe through the proxy. The probe tries to open Anthropic’s API endpoint. A normal success is not expected here. The script uses an intentionally invalid run token, so the correct answer from the proxy is HTTP 403, meaning “the proxy was reached, TLS worked, but access was denied.” That is the desired proof.

If the probe reports a temporary network-level problem, the script waits and tries again until the readiness timeout expires. If it sees anything other than the expected 403, it fails. It always kills the temporary sandbox at the end, like cleaning up a rented test room after inspection.

#### Function details

##### `ProxyTlsGate.run`  (lines 69–118)

```
def run(self) -> None
```

**Purpose**: Runs the actual proxy TLS gate check. It creates a temporary sandbox, installs the certificate authority certificate there, sends a test HTTPS request through the proxy, and only succeeds if the proxy returns the expected 403 denial.

**Data flow**: It starts with the gate’s stored proxy URL, certificate text, and sandbox template. It checks that the proxy URL is HTTPS, builds a proxy address using an intentionally invalid token and the known proxy password, and turns a small Python network probe into a shell-safe command. It then creates a sandbox, writes and installs the certificate inside it, repeatedly runs the probe, and reads the probe’s printed status. If the status is exactly 403, the function prints a success message and returns. If the status is still a temporary pending network error, it waits and retries until the deadline. Any unexpected result becomes a RuntimeError, and the sandbox is killed no matter how the run ends.

**Call relations**: This is called by main after command-line arguments and environment settings have been collected. Inside the flow it uses urllib.parse.urlsplit to understand the proxy URL, shlex.join to build a safe command string, Sandbox.create to start the throwaway sandbox, and time.monotonic plus sleep to enforce the retry window. It hands no value back to main; success is simply returning without an exception, and failure is raising an error.

*Call graph*: 5 external calls (create, join, monotonic, sleep, urlsplit).


##### `main`  (lines 121–132)

```
def main() -> None
```

**Purpose**: Provides the command-line entry point for the proxy gate. It gathers the proxy URL and required environment settings, chooses the sandbox template, and starts the gate check.

**Data flow**: It reads the --proxy-url command-line argument, then reads the certificate authority certificate and E2B template list from environment variables. If either required environment value is missing, it stops with a clear RuntimeError. It converts the template configuration into sandbox templates, selects the first configured sandbox size, creates a ProxyTlsGate object with the proxy URL, certificate, and template, and calls its run method. The result is either a completed validation or an exception explaining why the gate failed.

**Call relations**: This function is invoked when the file is run as a script. It uses argparse.ArgumentParser to read the user-provided proxy URL and sandbox_templates to turn the template environment value into usable template names. Once setup is complete, it hands control to ProxyTlsGate.run, which performs the real sandbox and proxy test.

*Call graph*: 3 external calls (__init__, ArgumentParser, sandbox_templates).

## 📊 State Registers Touched

- `reg-effective-config` — The merged settings that tell the whole system how it should run in this deployment.
- `reg-durable-database` — The main long-term database where shared business and runtime records are stored.
- `reg-workspace-member-agent-state` — The saved list of workspaces, people, memberships, seats, and agents.
- `reg-extension-install-store` — The saved record of which extensions are installed, removed, or holding extension-specific data.
- `reg-auth-identity-sessions` — The current proof of who a person, operator, shared-link visitor, or external service caller is.
- `reg-credential-connections` — The encrypted accounts, secrets, connection grants, and credential fulfillments that let agents use outside services safely.
- `reg-egress-policy-proxy` — The network allowlist and proxy state that decide which outside hosts sandboxed work may contact.
- `reg-billing-spend-ledger` — The shared accounting state for spend caps, usage charges, prepaid balances, BYOK billing, and ledger exports.
- `reg-sandbox-runtime` — The durable sandbox and browser workspace handles where agent commands, files, web browsing, and hosted previews run safely.
- `reg-schema-migration-version` — The Alembic/database schema version state that records which migrations have been applied and gates safe startup against the expected database shape.
- `reg-sandbox-template-build-cache` — The local Docker image and E2B template build/version state that sandbox launch code relies on to create compatible runtimes.
- `reg-extension-catalog-cache` — The extension app-store/catalog metadata and update availability state used when discovering, installing, removing, or bundling extensions.
