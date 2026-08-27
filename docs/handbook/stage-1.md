# Operator Entrypoints and Command Dispatch  `stage-1`

This stage is the system’s front desk. It covers the commands a human operator or developer runs before the rest of UFO takes over. The main entrypoint is ufoctl, which reads the user’s command and sends it to the right job: create or run a workspace, inspect its state, sign in through a browser, manage billing or credentials, install extensions, run migrations, seed data, or cancel a stuck turn in an emergency.

Some commands prepare UFO for deployment. The bundle builder creates a complete folder for building a Docker image, including the UFO package, configuration, sandbox client, Dockerfile, and a lockfile that fixes which extensions will be used. Other commands protect the code-running sandbox. The sandbox template builder checks that the hosted E2B sandbox and the local Docker image are built from the same recipe. The proxy gate script then performs a safety test: it launches a fresh sandbox, installs the proxy certificate, and confirms secure web traffic fails only in the expected controlled way.

## Files in this stage

### Operator CLI and Bundling
Operator commands enter through the main UFO CLI and can hand off to deployment bundle creation for runnable workspace packaging.

### `core/src/ufo/cli.py`

`entrypoint` · `operator commands, startup, maintenance, local development`

This file turns many lower-level UFO services into plain terminal commands. Without it, a new developer would have to manually write config files, create secrets, run database migrations, onboard the first workspace, mint access tokens, and call internal database code by hand. It is like the control panel for the system: each button is a command, and behind each button it loads configuration, opens the right database connection, calls the proper subsystem, then prints a human-readable result.

At startup, the CLI loads a `.env` file next to `ufo.toml`, while refusing unsafe bare provider key names that could accidentally leak into other tools. The `init` command writes a default local config, creates development secrets, prepares the database, creates the first workspace and owner, and stores a long-lived CLI token on the machine. `serve` runs the application, while `portal` safely hands that CLI token to the browser through a one-time local web page instead of putting the token in a URL.

The rest of the file is operator tooling: migrations, extension search/install/remove, spend caps, prepaid balance, spend reports, transcript access audits, OAuth grants, encrypted bring-your-own-key credentials, bundle creation, turn cancellation, and demo data seeding. Most commands are thin wrappers around domain services, but they carefully choose the right workspace, enforce simple validation, and always clean up database connections.

#### Function details

##### `_ufoctl_dir`  (lines 116–118)

```
def _ufoctl_dir() -> Path
```

**Purpose**: Finds the private directory where this machine stores `ufoctl` data, such as the CLI login token. It lets tests or deployments override the location with an environment variable.

**Data flow**: It reads the `UFOCTL_DIR` environment variable. If it is set, it turns that value into a filesystem path; otherwise it uses the current user's home directory plus `.ufoctl`. It returns that path and does not create it.

**Call relations**: The `init` command calls this before writing the CLI token. The `portal` command calls it later to find that same token for browser sign-in.

*Call graph*: called by 2 (init, portal); 2 external calls (Path, home).


##### `_dotenv_path`  (lines 121–122)

```
def _dotenv_path() -> Path
```

**Purpose**: Locates the `.env` file that belongs to the current UFO configuration. This keeps secrets next to `ufo.toml` instead of scattering them around the filesystem.

**Data flow**: It asks the config system where `ufo.toml` lives, takes that file's parent directory, and returns a path named `.env` inside it.

**Call relations**: Environment loading, development secret creation, missing-key checks, and `init` all call this so they agree on one secret file location.

*Call graph*: called by 4 (_load_dotenv, _missing_deploy_keys, _write_dev_secrets, init); 1 external calls (config_path).


##### `_dotenv_pairs`  (lines 125–159)

```
def _dotenv_pairs(text: str) -> list[tuple[str, str]]
```

**Purpose**: Reads simple `.env` text and turns it into key-value pairs. It supports the limited format UFO expects, including quoted multi-line secrets such as private keys.

**Data flow**: It takes raw text, skips blank lines and comments, accepts lines shaped like `KEY=VALUE`, removes a leading `export`, strips matching quotes, and joins multi-line quoted values. It returns a list of `(name, value)` pairs or raises an error for an unclosed quote.

**Call relations**: `_load_dotenv` uses it before putting secrets into the process environment. `_write_dev_secrets` uses it to avoid overwriting existing names, and `_missing_deploy_keys` uses it to see which extension keys are already present.

*Call graph*: called by 3 (_load_dotenv, _missing_deploy_keys, _write_dev_secrets).


##### `_load_dotenv`  (lines 162–178)

```
def _load_dotenv() -> None
```

**Purpose**: Loads the project's `.env` file into the current command's environment. It also protects users from accidentally using broad provider key names that other tools may read.

**Data flow**: It finds the `.env` file, returns early if it does not exist, parses it into pairs, rejects reserved names such as `OPENAI_API_KEY`, then writes the remaining values into `os.environ`.

**Call relations**: The top-level `main` command group calls this before any subcommand runs, so all commands see the same project secrets. It relies on `_dotenv_path` and `_dotenv_pairs` for the file location and parsing.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (main); 1 external calls (ClickException).


##### `main`  (lines 182–184)

```
def main() -> None
```

**Purpose**: Defines the root `ufoctl` command. It is the shared doorway all subcommands pass through.

**Data flow**: When Click, the command-line framework, invokes it, it loads the `.env` file into the process. It returns no value; its effect is preparing the environment for the selected subcommand.

**Call relations**: All commands in this file are registered under this group. Its only direct handoff is to `_load_dotenv`, which prepares secrets and configuration-related environment variables before command-specific work begins.

*Call graph*: calls 1 internal fn (_load_dotenv).


##### `_one_address`  (lines 187–192)

```
def _one_address(_ctx: click.Context, _param: click.Parameter, value: str) -> str
```

**Purpose**: Validates that a supplied owner email looks like exactly one local address at a domain. This catches setup typos before database onboarding starts.

**Data flow**: It receives a command-line value, asks `email_domain` whether it has a valid email-domain shape, and either returns the original value or raises a readable command-line error.

**Call relations**: Click uses this as the callback for `init --email`. It runs before `init` creates the workspace owner.

*Call graph*: 2 external calls (BadParameter, email_domain).


##### `init`  (lines 204–239)

```
def init(email: str, model: str, reasoning: ReasoningEffort) -> None
```

**Purpose**: Sets up a new UFO workspace for the first time. It writes local config if needed, creates secrets, prepares the database, onboards the owner and default agent, and stores a CLI token.

**Data flow**: It receives the owner's email, model name, and reasoning setting from command-line options. It writes default config if missing, loads config, fills missing development secrets, creates a PostgreSQL system database when needed, applies migrations, runs onboarding, mints a bearer token, writes it to the private `ufoctl` directory, and prints next steps.

**Call relations**: This is the main first-run command. It calls helpers for secret files, Postgres database creation, onboarding, token storage, and missing extension key reporting.

*Call graph*: calls 6 internal fn (_create_postgres_system_database, _dotenv_path, _missing_deploy_keys, _onboard, _ufoctl_dir, _write_dev_secrets); 7 external calls (run, ClickException, echo, mint_token, config_path, load_config, apply_migrations).


##### `_missing_deploy_keys`  (lines 242–258)

```
def _missing_deploy_keys(config: Config) -> tuple[str, ...]
```

**Purpose**: Finds extension-declared provider keys that are not currently configured. It reports likely future problems without blocking a local server from starting.

**Data flow**: It reads extension manifests for the active pack, collects the provider keys they declare, reads names already present in `.env` and the live environment, and returns missing names in UFO-prefixed form.

**Call relations**: `init` calls this after setup so the user sees which optional provider secrets still need to be added. It uses `_dotenv_path`, `_dotenv_pairs`, and extension manifest loading.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 1 external calls (load_manifests).


##### `_write_dev_secrets`  (lines 261–283)

```
def _write_dev_secrets(config: Config) -> tuple[str, ...]
```

**Purpose**: Creates the local secrets needed for a zero-configuration development server. It does not overwrite secrets that already exist.

**Data flow**: It generates a credential encryption key and token-signing secrets, reads any existing `.env` file and environment variables, writes only missing names to `.env`, adds those new values to the running process, and returns the names it added.

**Call relations**: `init` calls this before onboarding and token minting. The secrets it creates are later used by credential storage, artifact delivery, CLI token minting, and server authentication.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 2 external calls (generate_key, token_urlsafe).


##### `_onboard`  (lines 286–306)

```
async def _onboard(config: Config, email: str, model: str, reasoning: ReasoningEffort) -> Onboarded
```

**Purpose**: Creates the first workspace, owner member, default agent, model settings, and extension onboarding data. It wraps that work in a database lifecycle so connections are opened and closed cleanly.

**Data flow**: It receives loaded config plus the owner email, model, and reasoning choice. It initializes the database layer, optionally builds an encrypted credential store from the configured key, creates an `Onboarding` object with installed manifests, runs core creation and extension steps, returns the onboarding result, and finally disposes database resources.

**Call relations**: `init` calls this after migrations are applied. It hands detailed setup work to the onboarding subsystem and extension manifests.

*Call graph*: called by 1 (init); 6 external calls (__init__, __init__, Fernet, dispose_db, init_db, load_manifests).


##### `_create_postgres_system_database`  (lines 309–320)

```
async def _create_postgres_system_database(config: Config) -> None
```

**Purpose**: Ensures the extra PostgreSQL database used for system-level durability exists. This avoids a later startup failure when the app expects that database.

**Data flow**: It converts the app database URL into an asyncpg connection string, extracts the system database name, connects to PostgreSQL, checks whether that database exists, creates it if missing, then closes the connection.

**Call relations**: `init` calls this only for PostgreSQL configurations before migrations and onboarding proceed.

*Call graph*: called by 1 (init); 1 external calls (connect).


##### `migrate`  (lines 324–341)

```
def migrate() -> None
```

**Purpose**: Applies database schema changes for the core app and active extensions. Operators run it after installing extensions or during deployments so the database tables match the code.

**Data flow**: It loads configuration, chooses either the owner database URL from the environment or the normal configured database URL, runs migrations for the active pack, and prints confirmation.

**Call relations**: This command delegates schema work to `apply_migrations`. It is separate from `init` so already-running deployments can be upgraded without re-onboarding.

*Call graph*: 3 external calls (echo, load_config, apply_migrations).


##### `_one_slug`  (lines 344–347)

```
def _one_slug(_ctx: click.Context, _param: click.Parameter, value: str) -> str
```

**Purpose**: Checks that a new migration name is in simple snake_case form. This keeps migration filenames predictable and safe.

**Data flow**: It receives a command-line slug, tests it against the migration naming pattern, returns it if valid, or raises a command-line validation error.

**Call relations**: Click uses this as the argument validator for `new_migration` before that command writes a file.

*Call graph*: 1 external calls (BadParameter).


##### `new_migration`  (lines 352–369)

```
def new_migration(slug: str) -> None
```

**Purpose**: Creates a new empty core database migration file with a timestamp revision. It also updates the core migration `HEAD` marker so branch conflicts are caught early.

**Data flow**: It receives a validated slug, reads the current migration head, creates a UTC timestamp revision, writes a migration template file inside the migration versions directory, updates the `HEAD` file, and prints the created revision.

**Call relations**: This developer command uses `core_migration_head` and `contained_file` to safely write files in the migration directory.

*Call graph*: 4 external calls (echo, now, core_migration_head, contained_file).


##### `serve`  (lines 373–382)

```
def serve() -> None
```

**Purpose**: Starts the UFO server stack for the configured pack. It also prints the portal URL when the pack exposes a browser surface.

**Data flow**: It loads config, loads extension manifests, asks which surface should be the home portal, prints a helpful browser URL if one exists, then calls the server runner.

**Call relations**: This command is the operator's way to enter the long-running app. It uses `_serve_base` to choose the visible base URL and hands actual serving to `ufo.serve.run`.

*Call graph*: calls 1 internal fn (_serve_base); 5 external calls (echo, load_config, load_manifests, home_surface, run).


##### `portal`  (lines 386–404)

```
def portal() -> None
```

**Purpose**: Opens the browser portal and signs the user in with this machine's stored CLI token. It avoids asking the user to copy and paste a token.

**Data flow**: It loads config and manifests, finds the home surface, reads the CLI token from the private `ufoctl` directory, checks that the server is reachable, starts a browser handoff, and prints the opened URL.

**Call relations**: This command depends on `init` having already written a token and `serve` already answering. It calls `_serve_base`, `_ufoctl_dir`, and `BrowserHandoff.open`.

*Call graph*: calls 2 internal fn (_serve_base, _ufoctl_dir); 7 external calls (__init__, ClickException, echo, get, load_config, load_manifests, home_surface).


##### `_serve_base`  (lines 407–414)

```
def _serve_base(config: Config) -> str
```

**Purpose**: Chooses the base web address that users and browser links should use. This matters because browser cookies are tied to hostnames.

**Data flow**: It reads the loaded config. If a public base URL is configured, it returns that; otherwise it builds a local URL from the configured serve host and port.

**Call relations**: `serve` uses this when printing a portal link. `portal` uses it to check reachability and form the browser destination.

*Call graph*: called by 2 (portal, serve).


##### `BrowserHandoff.open`  (lines 429–437)

```
def open(self) -> None
```

**Purpose**: Safely transfers the CLI bearer token into the user's browser session. It does this through a one-time local web page instead of exposing the token in the address bar.

**Data flow**: It creates an unguessable local path, starts a temporary HTTP server on loopback, opens the browser to that local page, and keeps serving requests until the page has been delivered once.

**Call relations**: `portal` creates a `BrowserHandoff` and calls this. This method uses `_responder` to build the local request handler and `_page` indirectly to generate the form.

*Call graph*: calls 1 internal fn (_responder); 5 external calls (echo, HTTPServer, token_urlsafe, Event, open).


##### `BrowserHandoff._responder`  (lines 439–456)

```
def _responder(self, path: str, delivered: threading.Event) -> type[BaseHTTPRequestHandler]
```

**Purpose**: Builds the tiny HTTP request handler used during browser handoff. The handler serves exactly one secret-bearing page at exactly one random path.

**Data flow**: It receives the allowed path and a threading event used as a delivery flag. It pre-builds the HTML page, defines a request handler class that serves that page only at the matching path, and returns that handler class.

**Call relations**: `BrowserHandoff.open` calls this before starting the local HTTP server. The returned handler calls `_page`'s output and signals when delivery is complete.

*Call graph*: calls 1 internal fn (_page); called by 1 (open).


##### `BrowserHandoff._responder.do_GET`  (lines 443–452)

```
def do_GET(self) -> None
```

**Purpose**: Serves the handoff page when the browser requests the correct one-time URL. It rejects all other paths.

**Data flow**: It receives an HTTP GET request through the handler object. If the path is wrong, it sends a 404 error; if correct, it sends HTML headers and the page body, then marks the delivery event as complete.

**Call relations**: The temporary HTTP server created by `BrowserHandoff.open` invokes this method for browser requests. Its success lets `open` stop listening.


##### `BrowserHandoff._responder.log_message`  (lines 454–454)

```
def log_message(self, *args: object) -> None
```

**Purpose**: Silences the temporary handoff server's default request logging. This avoids printing noisy local HTTP logs during sign-in.

**Data flow**: It accepts the usual logging arguments but intentionally does nothing and returns nothing.

**Call relations**: The built-in HTTP server calls this when it would normally log a request. It is part of the handler class returned by `_responder`.


##### `BrowserHandoff._page`  (lines 458–465)

```
def _page(self) -> str
```

**Purpose**: Creates the HTML page that submits the token to the portal sign-in endpoint. The token is placed in a form body, not in a URL.

**Data flow**: It reads the handoff object's portal URL and token, escapes them for safe HTML, and returns a small page with an auto-submitting POST form plus a fallback button.

**Call relations**: `BrowserHandoff._responder` calls this while preparing the one-time local response.

*Call graph*: called by 1 (_responder); 1 external calls (escape).


##### `ingress`  (lines 469–471)

```
def ingress() -> None
```

**Purpose**: Starts the sandbox ingress service, which is a guarded reverse proxy into sandbox ports. This lets outside traffic reach sandboxed work only through the approved gateway.

**Data flow**: It takes no command arguments and simply calls the sandbox ingress runner. The runner takes over the long-running service work.

**Call relations**: This command is a direct CLI entry to `ufo.sandbox.ingress_serve.run`.

*Call graph*: 1 external calls (run).


##### `spend_cap`  (lines 475–476)

```
def spend_cap() -> None
```

**Purpose**: Defines the `spend-cap` command group. Its subcommands let operators view and change spending limits.

**Data flow**: It receives no data and performs no work itself. It exists so Click can attach `set` and `list` subcommands underneath it.

**Call relations**: Click routes `ufoctl spend-cap set` to `spend_cap_set` and `ufoctl spend-cap list` to `spend_cap_list` through this group.


##### `spend_cap_set`  (lines 487–505)

```
def spend_cap_set(scope: str, subject_id: str, window_seconds: int, limit_micro_usd: int, on_breach: str) -> None
```

**Purpose**: Creates or updates a spend cap for a workspace, member, or agent. Spend caps limit cost over a time window and decide whether over-limit turns are parked or rejected.

**Data flow**: It reads scope, optional subject ID, window length, limit, and breach behavior from command-line options. It validates whether a subject is required, converts the subject ID to a UUID when present, writes the cap, and prints the resulting cap ID and dollar limit.

**Call relations**: This command calls `_write_spend_cap` for the database update. It is registered under the `spend_cap` group.

*Call graph*: calls 1 internal fn (_write_spend_cap); 5 external calls (run, ClickException, echo, load_config, UUID).


##### `spend_cap_list`  (lines 509–519)

```
def spend_cap_list() -> None
```

**Purpose**: Prints the spend caps currently configured for the workspace. This helps operators see the active cost guardrails.

**Data flow**: It loads config, reads caps from the database, prints a no-caps message if empty, or formats each cap with scope, subject, limit, window, and breach behavior.

**Call relations**: This command calls `_read_spend_caps` and is reached through the `spend_cap` command group.

*Call graph*: calls 1 internal fn (_read_spend_caps); 3 external calls (run, echo, load_config).


##### `_write_spend_cap`  (lines 522–576)

```
async def _write_spend_cap(config: Config, scope: str, subject: UUID | None, window_seconds: int, limit_micro_usd: int, on_breach: str) -> UUID
```

**Purpose**: Writes a spend cap row to the database, updating an existing matching cap instead of creating a duplicate. It is the database-side worker for `spend-cap set`.

**Data flow**: It initializes the database, finds the workspace ID, checks for an existing cap with the same workspace, scope, subject, and window, updates its limit and breach behavior if found, or inserts a new cap with a fresh UUID. It returns the cap ID and closes database resources.

**Call relations**: `spend_cap_set` calls this after command-line validation. It uses the workspace transaction helper to make the change inside the current workspace database context.

*Call graph*: called by 1 (spend_cap_set); 7 external calls (insert, select, update, dispose_db, init_db, workspace_tx, uuid4).


##### `_read_spend_caps`  (lines 579–605)

```
async def _read_spend_caps(config: Config) -> list[tuple[UUID, str, UUID | None, int, int, str]]
```

**Purpose**: Reads all spend caps for the current workspace. It returns compact data that the CLI can print.

**Data flow**: It initializes the database, finds the workspace ID, selects cap fields ordered by scope, converts rows into tuples, and disposes database resources.

**Call relations**: `spend_cap_list` calls this to get the records it formats for the terminal.

*Call graph*: called by 1 (spend_cap_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `balance`  (lines 609–610)

```
def balance() -> None
```

**Purpose**: Defines the `balance` command group. Its subcommands inspect and change prepaid workspace balance information.

**Data flow**: It receives no direct input and returns nothing. It exists to organize `show`, `credit`, and `reserve` commands.

**Call relations**: Click uses this group to route balance-related commands to `balance_show`, `balance_credit`, and `balance_reserve`.


##### `balance_show`  (lines 615–630)

```
def balance_show(workspace_id: str) -> None
```

**Purpose**: Shows the workspace's prepaid balance, reserve requirement, lifetime grants, charges, and last purchase time. This gives operators a quick billing status view.

**Data flow**: It loads config, asks `_read_balance` for the selected workspace balance, prints `no balance` if none exists, or formats the micro-dollar amounts as dollars.

**Call relations**: This command is under the `balance` group. It delegates database selection and reading to `_read_balance`.

*Call graph*: calls 1 internal fn (_read_balance); 3 external calls (run, echo, load_config).


##### `balance_credit`  (lines 638–651)

```
def balance_credit(granted_micro_usd: int, charged_micro_usd: int, reference: str, workspace_id: str) -> None
```

**Purpose**: Adds a credit to a workspace balance exactly once per reference key. This prevents repeated payment events from double-crediting the account.

**Data flow**: It reads granted amount, charged amount, reference, and optional workspace ID. It rejects a zero grant, loads config, calls `_credit_balance`, and prints either credited or already credited.

**Call relations**: This command is under the `balance` group and uses `_credit_balance`, which in turn uses the billing balance subsystem.

*Call graph*: calls 1 internal fn (_credit_balance); 4 external calls (run, ClickException, echo, load_config).


##### `balance_reserve`  (lines 657–665)

```
def balance_reserve(micro_usd: int, workspace_id: str) -> None
```

**Purpose**: Sets the minimum balance headroom required before a turn can begin. This is a guard against starting work when too little prepaid balance remains.

**Data flow**: It reads a micro-dollar reserve and optional workspace ID, rejects negative values, loads config, calls `_set_reserve`, prints the new reserve if successful, or raises an error if no balance exists yet.

**Call relations**: This command is under the `balance` group. It delegates the actual update to `_set_reserve`.

*Call graph*: calls 1 internal fn (_set_reserve); 4 external calls (run, ClickException, echo, load_config).


##### `_target_workspace`  (lines 668–690)

```
async def _target_workspace(named: str) -> UUID
```

**Purpose**: Chooses which workspace an operator command should act on. If the user does not name one, it only proceeds when the deployment has exactly one workspace.

**Data flow**: It reads across workspaces through the owner transaction. If a workspace ID was supplied, it validates that it exists and returns it. If none was supplied, it lists workspaces and returns the only one, or raises a clear error for zero or many.

**Call relations**: `_balance_scope` and `_seed_target` call this before doing workspace-specific work. It prevents accidental changes to the wrong workspace in hosted multi-workspace deployments.

*Call graph*: called by 2 (_balance_scope, _seed_target); 4 external calls (ClickException, select, owner_tx, UUID).


##### `_balance_scope`  (lines 694–711)

```
async def _balance_scope(config: Config, named: str) -> AsyncIterator[tuple[AsyncConnection, UUID]]
```

**Purpose**: Opens the correct database context for balance commands. It pins operations to one workspace while still allowing owner-level lookup first.

**Data flow**: It initializes the app database and, when available, the owner database. It resolves the target workspace, enters the workspace context, opens a workspace transaction, yields the connection and workspace ID to the caller, and finally disposes database resources.

**Call relations**: `_read_balance`, `_credit_balance`, and `_set_reserve` all use this shared context manager so they select workspaces consistently.

*Call graph*: calls 1 internal fn (_target_workspace); called by 3 (_credit_balance, _read_balance, _set_reserve); 5 external calls (dispose_db, init_db, init_owner_db, workspace_tx, ws).


##### `_read_balance`  (lines 714–716)

```
async def _read_balance(config: Config, named: str) -> Balance | None
```

**Purpose**: Reads a workspace balance record. It is the async helper behind `balance show`.

**Data flow**: It opens `_balance_scope`, receives a database connection and workspace ID, calls the billing balance reader, and returns either a balance object or `None`.

**Call relations**: `balance_show` calls this and then formats the returned balance for the terminal.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_show); 1 external calls (read_balance).


##### `_credit_balance`  (lines 719–725)

```
async def _credit_balance(config: Config, named: str, granted_micro_usd: int, charged_micro_usd: int, reference: str) -> bool
```

**Purpose**: Applies a balance credit or correction for a workspace. It preserves idempotency through the reference value handled by the billing subsystem.

**Data flow**: It opens `_balance_scope`, passes the connection, workspace ID, granted amount, charged amount, and reference to the billing credit function, and returns whether a new credit was applied.

**Call relations**: `balance_credit` calls this after validating command-line input.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_credit); 1 external calls (credit).


##### `_set_reserve`  (lines 728–730)

```
async def _set_reserve(config: Config, named: str, reserve_micro_usd: int) -> bool
```

**Purpose**: Updates the reserve amount for a workspace balance. The reserve is the required spending headroom before new work may start.

**Data flow**: It opens `_balance_scope`, passes the connection, workspace ID, and new reserve amount to the billing subsystem, and returns whether the update succeeded.

**Call relations**: `balance_reserve` calls this and turns the boolean result into either a confirmation or a command-line error.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_reserve); 1 external calls (set_reserve).


##### `spend`  (lines 738–761)

```
def spend(window_seconds: int) -> None
```

**Purpose**: Prints a spending report for a recent time window. It breaks costs down by dimension, member, agent, origin, and pricing version.

**Data flow**: It reads the window length option, loads config, calls `_read_spend`, converts micro-dollars to dollars, and prints the total plus several grouped breakdowns.

**Call relations**: This command delegates calculation to `SpendRollup` through `_read_spend`, then handles only terminal formatting.

*Call graph*: calls 1 internal fn (_read_spend); 3 external calls (run, echo, load_config).


##### `_read_spend`  (lines 764–771)

```
async def _read_spend(config: Config, window_seconds: int) -> SpendReport
```

**Purpose**: Reads the spend rollup for the current workspace and time window. It is the database worker behind the `spend` command.

**Data flow**: It initializes the database, opens a workspace transaction, finds the workspace ID, asks `SpendRollup` to read the report, returns that report, and disposes resources.

**Call relations**: `spend` calls this before printing the report.

*Call graph*: called by 1 (spend); 5 external calls (__init__, select, dispose_db, init_db, workspace_tx).


##### `transcript_reads`  (lines 779–792)

```
def transcript_reads(limit: int) -> None
```

**Purpose**: Lists recorded admin disclosures for reading another member's private transcript. This gives operators an audit view of sensitive transcript access.

**Data flow**: It reads a limit option, rejects limits below one, loads config, reads access records, prints a no-records message if empty, or formats each record with time, reader, subject, and conversation ID.

**Call relations**: This command calls `_read_transcript_accesses` for the database query and handles terminal output.

*Call graph*: calls 1 internal fn (_read_transcript_accesses); 4 external calls (run, ClickException, echo, load_config).


##### `_read_transcript_accesses`  (lines 795–829)

```
async def _read_transcript_accesses(config: Config, limit: int) -> list[tuple[str, str, UUID, datetime]]
```

**Purpose**: Fetches transcript access audit records from the database. It joins member records so the CLI can print email addresses instead of raw IDs.

**Data flow**: It initializes the database, aliases the member table for reader and subject, finds the workspace ID, selects recent transcript access rows ordered newest first, converts them into tuples, and disposes resources.

**Call relations**: `transcript_reads` calls this and then formats the returned audit entries.

*Call graph*: called by 1 (transcript_reads); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `grants`  (lines 833–845)

```
def grants() -> None
```

**Purpose**: Lists OAuth account grants available to agents. These are accounts connected in chat that agents can later use.

**Data flow**: It loads config, reads grant summaries, prints `no grants` if there are none, or formats each grant with agent, provider, account, sharing type, and date.

**Call relations**: This command calls `_read_grants`, which relies on the access grants subsystem for the actual summary.

*Call graph*: calls 1 internal fn (_read_grants); 3 external calls (run, echo, load_config).


##### `_read_grants`  (lines 848–855)

```
async def _read_grants(config: Config) -> tuple[GrantSummary, ...]
```

**Purpose**: Reads OAuth grant summaries for the current workspace. It is the async data fetcher for the `grants` command.

**Data flow**: It initializes the database, finds the workspace ID inside a workspace transaction, asks `workspace_grant_summaries` for summaries, returns them, and disposes resources.

**Call relations**: `grants` calls this before printing the results.

*Call graph*: called by 1 (grants); 5 external calls (select, workspace_grant_summaries, dispose_db, init_db, workspace_tx).


##### `credential`  (lines 859–861)

```
def credential() -> None
```

**Purpose**: Defines the `credential` command group. Its subcommands let operators fill and inspect encrypted credential slots declared by extensions.

**Data flow**: It takes no direct input and returns nothing. It serves as the parent for credential-related commands.

**Call relations**: Click routes `credential set` and `credential list` through this group.


##### `credential_set`  (lines 866–889)

```
def credential_set(slot: str) -> None
```

**Purpose**: Stores a secret value for one declared credential slot. It reads the secret from a hidden prompt or standard input, never from command-line arguments.

**Data flow**: It loads config, checks that the slot is declared and operator-fillable, checks that the encryption key is set, reads a non-empty secret value, writes it through `_write_credential`, and prints confirmation.

**Call relations**: This command uses `_declared_slots` and `_fillable_slots` for validation, then calls `_write_credential` to encrypt and store the value.

*Call graph*: calls 3 internal fn (_declared_slots, _fillable_slots, _write_credential); 5 external calls (run, ClickException, echo, prompt, load_config).


##### `credential_list`  (lines 893–903)

```
def credential_list() -> None
```

**Purpose**: Shows which declared credential slots are set or unset, without revealing any secret values. This helps operators see what still needs configuration.

**Data flow**: It loads config, reads declared slots, exits with a message if none exist, reads stored slot names from the database, and prints each slot with its owning extension and status.

**Call relations**: This command combines extension declaration data from `_declared_slots` with stored database data from `_read_stored_slots`.

*Call graph*: calls 2 internal fn (_declared_slots, _read_stored_slots); 3 external calls (run, echo, load_config).


##### `_declared_slots`  (lines 906–911)

```
def _declared_slots(config: Config) -> dict[str, str]
```

**Purpose**: Collects all credential slots declared by installed extension manifests. It maps each slot name to the extension that owns it.

**Data flow**: It loads manifests for the configured pack, turns their credential declarations into a dictionary, and converts manifest loading errors into command-line errors.

**Call relations**: `credential_set` uses this to reject unknown slots. `credential_list` uses it to know what should be displayed.

*Call graph*: called by 2 (credential_list, credential_set); 2 external calls (ClickException, load_manifests).


##### `_fillable_slots`  (lines 914–923)

```
def _fillable_slots(config: Config) -> frozenset[str]
```

**Purpose**: Finds the credential slots that a human operator or member is allowed to type in. Some slots are written by the deployment itself and should not be manually filled.

**Data flow**: It loads extension manifests, filters credential declarations to those marked `member_filled`, returns their names as a frozen set, and converts manifest errors into command-line errors.

**Call relations**: `credential_set` calls this after checking that the slot exists, so it can reject slots that should only be produced internally.

*Call graph*: called by 1 (credential_set); 2 external calls (ClickException, load_manifests).


##### `_write_credential`  (lines 926–933)

```
async def _write_credential(config: Config, key: str, slot: str, value: str) -> None
```

**Purpose**: Encrypts and stores one credential value for the current workspace. It is the database worker behind `credential set`.

**Data flow**: It initializes the database, finds the workspace ID, creates a `CredentialStore` using the supplied Fernet encryption key, stores the slot value, and disposes resources.

**Call relations**: `credential_set` calls this after reading and validating the secret.

*Call graph*: called by 1 (credential_set); 6 external calls (__init__, Fernet, select, dispose_db, init_db, workspace_tx).


##### `_read_stored_slots`  (lines 936–950)

```
async def _read_stored_slots(config: Config) -> frozenset[str]
```

**Purpose**: Reads which credential slots already have stored values for the current workspace. It never reads or returns the secret values themselves.

**Data flow**: It initializes the database, finds the workspace ID, selects credential slot names for that workspace, returns them as a frozen set, and disposes resources.

**Call relations**: `credential_list` calls this to decide whether each declared slot should be shown as set or unset.

*Call graph*: called by 1 (credential_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `ext`  (lines 954–955)

```
def ext() -> None
```

**Purpose**: Defines the `ext` command group for extension store operations. Extensions add capabilities to a UFO pack.

**Data flow**: It performs no direct work. It groups extension search, install, and remove commands under one CLI namespace.

**Call relations**: Click routes `ext search`, `ext install`, and `ext remove` through this group.


##### `_store`  (lines 958–961)

```
def _store(config: Config) -> ExtensionStore
```

**Purpose**: Builds an extension store object from configuration. It refuses to continue if the extension store is not enabled.

**Data flow**: It reads the extension store location from config, raises a command-line error if missing, reads the catalog, finds the lockfile path, and returns an `ExtensionStore` object.

**Call relations**: `ext_search`, `ext_install`, and `ext_remove` all call this before doing store work.

*Call graph*: called by 3 (ext_install, ext_remove, ext_search); 4 external calls (__init__, ClickException, lockfile_path, read_catalog).


##### `ext_search`  (lines 966–979)

```
def ext_search(query: str) -> None
```

**Purpose**: Searches the configured extension catalog and shows matching extensions. It marks whether each match is installed, available, or bundle-only.

**Data flow**: It loads config, builds the extension store, searches with the query string, prints a no-results message if empty, or prints each listing with name, version, and state.

**Call relations**: This command is under the `ext` group and delegates catalog access to `_store`.

*Call graph*: calls 1 internal fn (_store); 2 external calls (echo, load_config).


##### `ext_install`  (lines 984–990)

```
def ext_install(name: str) -> None
```

**Purpose**: Pins an extension from the store into the deploy's lockfile. The next server run will load that extension.

**Data flow**: It loads config, builds the extension store, asks it to install the named extension, converts store errors into command-line errors, and prints the pinned name, version, and digest.

**Call relations**: This command uses `_store` and is part of the `ext` command group.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `ext_remove`  (lines 995–1001)

```
def ext_remove(name: str) -> None
```

**Purpose**: Removes an extension pin from the lockfile. The next server run will stop loading that extension.

**Data flow**: It loads config, builds the extension store, asks it to remove the named extension, converts store errors into command-line errors, and prints confirmation.

**Call relations**: This command uses `_store` and is part of the `ext` command group.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `_ufo_project_dir`  (lines 1007–1018)

```
def _ufo_project_dir() -> Path
```

**Purpose**: Finds the source checkout for the `ufo` Python project so a bundle can build the correct wheel. It fails clearly if the CLI is running from a wheel-only install without source files.

**Data flow**: It walks upward from this file, looks for `pyproject.toml`, reads it, and returns the first ancestor whose project name is `ufo`. If none is found, it raises a command-line error.

**Call relations**: `bundle` calls this before running `uv build`.

*Call graph*: called by 1 (bundle); 3 external calls (ClickException, Path, loads).


##### `bundle`  (lines 1030–1052)

```
def bundle(out: Path, client_binary: Path) -> None
```

**Purpose**: Creates a runnable deployment bundle containing the app wheel, client binary, config, and extension lock information. This freezes a deploy into an artifact that can be shipped.

**Data flow**: It reads output directory and client binary options, loads config, optionally reads the extension catalog, builds a Python wheel with `uv`, verifies the expected wheel exists, calls `Bundle.build`, then prints bundle details and pinned extensions.

**Call relations**: This command calls `_ufo_project_dir`, `wheel_name`, and the `Bundle` builder to assemble the final artifact.

*Call graph*: calls 1 internal fn (_ufo_project_dir); 8 external calls (__init__, ClickException, echo, run, wheel_name, config_path, load_config, read_catalog).


##### `turn`  (lines 1056–1057)

```
def turn() -> None
```

**Purpose**: Defines the `turn` command group for acting on a single conversation turn. A turn is one unit of agent work in a conversation.

**Data flow**: It performs no direct work and returns nothing. It only provides a parent namespace for turn subcommands.

**Call relations**: Click routes `turn cancel` through this group to `turn_cancel`.


##### `turn_cancel`  (lines 1063–1073)

```
def turn_cancel(turn_id: str, workspace_id: str) -> None
```

**Purpose**: Cancels one turn that cannot be ended normally. This is an operator repair tool for stuck or repeatedly recovering work.

**Data flow**: It reads a turn ID and optional workspace ID, loads config, converts the turn ID to a UUID, calls `_cancel_turn`, and prints whether the turn was cancelled or already terminal.

**Call relations**: This command is under the `turn` group. It delegates durable workflow cancellation and database checks to `_cancel_turn`.

*Call graph*: calls 1 internal fn (_cancel_turn); 4 external calls (run, echo, load_config, UUID).


##### `_cancel_turn`  (lines 1076–1110)

```
async def _cancel_turn(config: Config, turn_id: UUID, named_workspace: str) -> bool
```

**Purpose**: Finds the workspace for a turn, checks that the turn exists when needed, and asks the durable workflow system to cancel it. It returns whether cancellation actually happened.

**Data flow**: It initializes the database, resolves the workspace from the option or owner database, creates a replay-safe durability client, enters the workspace context, optionally verifies the turn exists there, calls `cancel_one_turn`, returns a boolean, and disposes resources.

**Call relations**: `turn_cancel` calls this. It coordinates database lookup, workspace scoping, and the turn cancellation subsystem.

*Call graph*: called by 1 (turn_cancel); 11 external calls (ClickException, select, dispose_db, init_db, init_owner_db, owner_tx, workspace_tx, replay_safe_client, cancel_one_turn, ws (+1 more)).


##### `seed`  (lines 1114–1115)

```
def seed() -> None
```

**Purpose**: Defines the `seed` command group for writing demonstration content. Seed data helps designers and developers inspect UI states.

**Data flow**: It performs no direct work and returns nothing. It exists as the parent for seed subcommands.

**Call relations**: Click routes `seed kitchen-sink` through this group to `seed_kitchen_sink`.


##### `seed_kitchen_sink`  (lines 1120–1129)

```
def seed_kitchen_sink(workspace_id: str) -> None
```

**Purpose**: Writes a demonstration conversation containing many shapes the portal UI must display. It prints the portal path where the seeded conversation can be opened.

**Data flow**: It reads an optional workspace ID, loads config, calls `_seed_kitchen_sink`, receives a conversation ID, and prints a URL fragment pointing to that conversation.

**Call relations**: This command is under the `seed` group and delegates database and blob setup to `_seed_kitchen_sink`.

*Call graph*: calls 1 internal fn (_seed_kitchen_sink); 3 external calls (run, echo, load_config).


##### `_seed_kitchen_sink`  (lines 1132–1143)

```
async def _seed_kitchen_sink(config: Config, named: str) -> UUID
```

**Purpose**: Sets up the database and blob storage needed to write the kitchen-sink demo conversation. It safely resolves the target workspace before seeding.

**Data flow**: It initializes the app database and owner database if available, creates a workspace blob store from configured blob storage, calls `_seed_target`, returns the conversation ID, and disposes resources.

**Call relations**: `seed_kitchen_sink` calls this. It hands the final workspace-specific write to `_seed_target`.

*Call graph*: calls 1 internal fn (_seed_target); called by 1 (seed_kitchen_sink); 5 external calls (__init__, blob_store_for, dispose_db, init_db, init_owner_db).


##### `_seed_target`  (lines 1146–1177)

```
async def _seed_target(blob: WorkspaceBlobStore, named: str) -> UUID
```

**Purpose**: Writes the actual kitchen-sink demo conversation into one workspace. It chooses the main agent and first member so the content looks like normal workspace data.

**Data flow**: It resolves the target workspace, enters that workspace context, reads the main agent and earliest member, raises an error if no member exists, creates a `KitchenSink` writer with blob storage and IDs, and returns the written conversation ID.

**Call relations**: `_seed_kitchen_sink` calls this after database and blob setup. It uses `_target_workspace` to avoid writing demo data into the wrong workspace.

*Call graph*: calls 1 internal fn (_target_workspace); called by 1 (_seed_kitchen_sink); 5 external calls (__init__, ClickException, select, workspace_tx, ws).


### `core/src/ufo/bundle.py`

`orchestration` · `bundle creation / deployment packaging`

This file exists so a UFO deployment can be frozen into something repeatable. Instead of relying on whatever happens to be installed on a machine at runtime, it records the exact extensions and their content hashes, then writes the files needed to build a container image. Think of it like packing a travel kit: the config, allowed extensions, executable helper, and installation recipe all go into one bag so the same trip can be repeated elsewhere.

The main class, Bundle, is given an existing config file, an optional extension catalog, an output folder, the built UFO wheel, and the sandbox client binary. Its build method creates the output folder, copies the config and client binary, writes a fresh lockfile, and writes a Dockerfile. The lockfile is important because it says which extensions are allowed and what their package bytes should look like. At boot, that lets the runtime check that it is using the same extension code the bundle was built with.

The careful part is extension pinning. If a lockfile already exists, the bundle keeps those pinned extension names. If not, it uses all currently discovered installed extensions. It can also add catalog entries marked as disabled, because those are meant to be installed only when building a bundle, not dynamically at runtime. For each extension, it opens the UFO wheel and hashes the extension’s packaged files, so the bundle records the code that the container will actually install, not just whatever source files may be lying around locally.

#### Function details

##### `wheel_name`  (lines 35–38)

```
def wheel_name() -> str
```

**Purpose**: This function builds the expected filename of the UFO Python wheel. A wheel is a packaged Python distribution file; here it names the local UFO package that the Dockerfile will install because it is not fetched from a public package index.

**Data flow**: It reads the current UFO version from the extension store helper, places that version into the standard wheel filename pattern, and returns the resulting string, such as a versioned .whl filename.

**Call relations**: Bundle._dockerfile calls this when writing the Dockerfile, so the Docker build recipe copies and installs the wheel file whose name matches the current UFO version.

*Call graph*: called by 1 (_dockerfile); 1 external calls (ufo_version).


##### `Bundle.build`  (lines 62–82)

```
def build(self) -> BundleResult
```

**Purpose**: This is the top-level bundle builder. Someone uses it when they want to turn the current deployment setup into a folder that Docker can build into a runnable UFO image.

**Data flow**: It starts with the Bundle object’s inputs: the source config path, optional catalog, output folder, wheel path, and sandbox client binary path. It first asks _pins to decide and hash the extension set. Then it creates the output folder, copies the config text into ufo.toml, writes a JSON lockfile using the current UFO version and the extension pins, copies the sandbox client binary, writes the Dockerfile text, and returns a BundleResult that points to all produced files and records the pins.

**Call relations**: This method coordinates the whole file. It calls _pins before writing the lockfile because the lockfile needs the final extension list. It calls _dockerfile after copying the other bundle ingredients so the Docker build context has the recipe and the files it refers to. The result object is handed back to the caller as a concise receipt of what was created.

*Call graph*: calls 2 internal fn (_dockerfile, _pins); 3 external calls (__init__, __init__, ufo_version).


##### `Bundle._pins`  (lines 84–128)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: This function decides which extensions belong in the bundle and records a content digest for each one. The digest is a fingerprint of the packaged files, used later to prove the running image has the expected extension code.

**Data flow**: It discovers the extensions installed in the current environment and looks for an existing UFO lockfile. If a lockfile exists, it starts from the extension names already pinned there; otherwise it starts from all discovered extensions. If a catalog is available, it adds catalog entries marked disabled, because those are bundle-only additions. It removes duplicate names while keeping order. For each name, it checks that the extension is installed, finds the top-level Python package or module for that extension inside the UFO wheel, ignores cache and compiled files, hashes the remaining packaged bytes, and creates an ExtensionPin with the extension name, version, and digest. It returns all pins as a tuple. If an expected extension or package is missing, it raises an error instead of creating an incomplete bundle.

**Call relations**: Bundle.build calls this before writing the lockfile. This function relies on the extension loader to discover installed extensions, read an existing lockfile, and compute content digests. It also opens the wheel file directly, because the lock should describe the exact files that the Docker image will install.

*Call graph*: called by 1 (build); 7 external calls (__init__, Path, discovered, extension_content_digest, lockfile_path, read_lockfile, ZipFile).


##### `Bundle._dockerfile`  (lines 130–146)

```
def _dockerfile(self) -> str
```

**Purpose**: This function writes the text of the Dockerfile used to build the runnable UFO container image. The Dockerfile is the recipe Docker follows to assemble the final image.

**Data flow**: It uses fixed bundle filenames, the standard Python base image name, the expected wheel filename from wheel_name, and the sandbox client install path. From those pieces it returns one complete Dockerfile string. That recipe installs the local UFO wheel, copies in the pinned config and lockfile, installs the sandbox client binary, sets environment variables so UFO knows where those files are, and starts the container with ufoctl serve by default.

**Call relations**: Bundle.build calls this after preparing the bundle contents and writes the returned text to the Dockerfile in the output folder. This function calls wheel_name so the Dockerfile refers to the same versioned wheel file that the bundle process expects to place beside the Docker build context.

*Call graph*: calls 1 internal fn (wheel_name); called by 1 (build).


### Sandbox Validation Utilities
Standalone sandbox scripts validate that the execution image and proxy safety behavior are correct before the sandbox is used by the system.

### `sandbox/build_template.py`

`entrypoint` · `build and deploy time`

This script is the build recipe for UFO’s sandbox: the prepared environment where agent-created code can run with the right tools already installed. Without it, the sandbox might be missing basics like Node, Python packages, browser tools, PDF tools, the compiled `ufo` command, or the built-in skill bundle, and failures would appear later during user work instead of at build time.

The file defines one shared set of layers, like one packing list for two suitcases. One suitcase is an E2B template, which is a hosted sandbox image. The other is a Docker image, used locally or by the Docker carrier. The base images differ, but the installed tools, copied files, environment variables, startup command, and readiness checks stay in sync.

A central idea is the build digest: a fingerprint made from the recipe’s important inputs. The script bakes that fingerprint into the image. Later, `--check` can boot the live E2B template, read the fingerprint, and tell whether the published template is stale. Normal publishing also boots the newly built template and runs a readiness probe, so a broken image is caught immediately.

The script can print a Dockerfile, build a Docker image, check existing E2B templates, or publish new E2B templates for several sizes.

#### Function details

##### `template_name`  (lines 259–260)

```
def template_name(size: str) -> str
```

**Purpose**: Creates the E2B template name for a given sandbox size, such as small, medium, or large. This keeps all size-specific template names following one predictable pattern.

**Data flow**: It receives a size name as text. It combines that size with the shared base template name. It returns the full template name string used when checking or publishing that size.

**Call relations**: The main command uses this when it loops over sandbox sizes, so every check and publish talks to the correctly named E2B template.

*Call graph*: called by 1 (main).


##### `client_definition`  (lines 263–289)

```
def client_definition() -> dict[str, str]
```

**Purpose**: Describes the compiled `ufo` client that will be baked into the sandbox, including a source-code fingerprint. It uses the client source instead of the built binary bytes because release binaries may differ slightly between machines even when built from the same code.

**Data flow**: It reads selected files and folders from the Rust client project. It feeds each file path and file content into a SHA-256 hash, which is a standard way to make a compact fingerprint. It returns a small dictionary containing the binary name, the build target, and the source fingerprint.

**Call relations**: The build digest function calls this while assembling the full image fingerprint. That means a meaningful client source change makes the sandbox definition change too.

*Call graph*: called by 1 (build_definition_digest); 1 external calls (sha256).


##### `stage_client_binary`  (lines 292–303)

```
def stage_client_binary() -> Path
```

**Purpose**: Copies the already-built `ufo` client binary into a known artifacts folder inside the repository. The image build can then copy it from a stable location.

**Data flow**: It asks the client-binary helper where the correct compiled binary is. It creates the staging folder if needed, copies the binary there, marks it executable, and returns the staged path.

**Call relations**: The Docker-image build path and the normal E2B publish path call this before building. This makes missing or unstaged client artifacts fail early instead of accidentally using an old binary.

*Call graph*: called by 2 (build_docker_image, main); 2 external calls (copyfile, client_binary).


##### `system_skill_bundle`  (lines 307–322)

```
def system_skill_bundle() -> SystemSkillBundle
```

**Purpose**: Collects all built-in system skills and packages them into one bundle for the sandbox. A skill is a reusable capability the agent can call on, such as document or media processing.

**Data flow**: It searches the project for `SKILL.md` files in the core, extensions, and packs areas. It finds the top-level skill directories, asks the skill discovery code to load the skills, and returns a `SystemSkillBundle` containing their archive and digest.

**Call relations**: The staging function uses this to write the skill archive, and the build digest uses it to include the skill bundle fingerprint. It is cached, so repeated calls during one run do not rescan and rebuild the bundle.

*Call graph*: calls 1 internal fn (from_skills); called by 2 (build_definition_digest, stage_system_skills); 1 external calls (discover_skills).


##### `stage_system_skills`  (lines 325–328)

```
def stage_system_skills() -> Path
```

**Purpose**: Writes the bundled system skills to the sandbox artifacts folder so the image build can copy them in. This prepares the skills before an actual Docker or E2B build.

**Data flow**: It creates the artifacts folder if needed. It gets the archive bytes from `system_skill_bundle`, writes them to a zip file, and returns that zip file path.

**Call relations**: The Docker build path and the normal publish path call this before building. Later, `apply_layers` tells the image builder to copy and unpack this staged archive inside the sandbox.

*Call graph*: calls 1 internal fn (system_skill_bundle); called by 2 (build_docker_image, main).


##### `build_definition_digest`  (lines 331–373)

```
def build_definition_digest(sizing: Sizing | None) -> str
```

**Purpose**: Creates the fingerprint for the entire sandbox build recipe. This is the drift detector: if the recipe changes but the published template was not rebuilt, the mismatch can be caught.

**Data flow**: It receives either a sizing choice for E2B or `None` for Docker. It gathers the base template, CPU and memory settings, installed packages, environment variables, startup and readiness commands, runtime directories, client source fingerprint, skill bundle digest, and sandbox module file hashes. It serializes this information in a stable order and returns a SHA-256 digest string.

**Call relations**: The E2B-template and Dockerfile builders call this before applying layers. The main check command also calls it to compare the current source recipe with the digest baked into live templates.

*Call graph*: calls 2 internal fn (client_definition, system_skill_bundle); called by 3 (e2b_template, main, pod_dockerfile); 2 external calls (sha256, dumps).


##### `apply_layers`  (lines 376–421)

```
def apply_layers(builder: TemplateBuilder, digest: str) -> TemplateFinal
```

**Purpose**: Applies the shared sandbox recipe to an image builder. This is the heart of the file: it installs the tools, copies the UFO pieces, sets permissions, sets environment variables, and defines how the sandbox starts and proves it is ready.

**Data flow**: It receives an image builder and the digest to bake into the image. It switches to the build user, runs package installation commands, removes sudo, installs GitHub CLI, Node, Python packages, npm packages, and Playwright’s browser, creates important directories, writes the digest, copies and unpacks the system skills, copies the `ufo` client and sandbox helper modules, then switches to the runtime user. It returns the finalized template definition with the start command and readiness command attached.

**Call relations**: Both `e2b_template` and `pod_dockerfile` call this, which is what keeps the E2B and Docker outputs aligned. It hands its work to the E2B SDK builder methods, which record the actual image-building steps.

*Call graph*: called by 2 (e2b_template, pod_dockerfile); 5 external calls (copy, run_cmd, set_envs, set_start_cmd, set_user).


##### `e2b_template`  (lines 424–426)

```
def e2b_template(size: str) -> TemplateFinal
```

**Purpose**: Builds the E2B template definition for one sandbox size. It starts from E2B’s base code-interpreter template and adds UFO’s shared layers.

**Data flow**: It receives a size name. It creates an E2B template builder using the repository as the file context, computes the digest for that size’s CPU and memory tier, applies the shared layers, and returns the final template definition.

**Call relations**: The main publish flow calls this for each size before asking E2B to build and publish it. It relies on `build_definition_digest` and `apply_layers` to make the size-specific template match the shared recipe.

*Call graph*: calls 2 internal fn (apply_layers, build_definition_digest); called by 1 (main); 1 external calls (Template).


##### `pod_dockerfile`  (lines 429–431)

```
def pod_dockerfile() -> str
```

**Purpose**: Renders the Docker version of the sandbox image as a Dockerfile. This lets the Docker carrier use the same installed tools and copied files as the E2B template.

**Data flow**: It creates a builder starting from the public Docker base image, computes a digest without E2B sizing, applies the shared layers, and converts the result into Dockerfile text. It returns that text.

**Call relations**: The main command uses this when `--dockerfile` is requested, and `build_docker_image` uses it before running `docker build`. Because it also goes through `apply_layers`, Docker and E2B stay tied to the same recipe.

*Call graph*: calls 2 internal fn (apply_layers, build_definition_digest); called by 2 (build_docker_image, main); 2 external calls (Template, to_dockerfile).


##### `build_docker_image`  (lines 434–447)

```
def build_docker_image() -> None
```

**Purpose**: Builds the local Docker sandbox image from the shared recipe. This path does not need an E2B account because it only talks to the local Docker daemon.

**Data flow**: It first stages the compiled client binary and system skills. It renders the Dockerfile text, sends that text to `docker build` through standard input, uses the repository root as the build context, and tags the image. If Docker reports failure, it exits with an error; otherwise it prints the image tag.

**Call relations**: The main command calls this for `--build-docker`. It prepares artifacts through the staging functions, then hands the rendered Dockerfile to the external Docker command.

*Call graph*: calls 3 internal fn (pod_dockerfile, stage_client_binary, stage_system_skills); called by 1 (main); 1 external calls (run).


##### `verify_published_template`  (lines 450–465)

```
def verify_published_template(name: str) -> None
```

**Purpose**: Checks that a newly published E2B template really boots and has the required tools. This is a publish gate, so a broken template does not look successful just because the build command finished.

**Data flow**: It receives a published template reference. It creates a temporary sandbox from that template, runs the same readiness command that was baked into the image, and always kills the sandbox afterward. If the command fails or returns a nonzero exit code, it raises an error.

**Call relations**: The normal publish flow in `main` calls this after each E2B build. It hands the live sandbox the readiness probe, which checks for expected tools like Python, Node, pnpm, the `ufo` command, browser support, and system skills.

*Call graph*: called by 1 (main); 1 external calls (create).


##### `check_published_template`  (lines 468–488)

```
def check_published_template(name: str, expected: str) -> None
```

**Purpose**: Checks whether a live E2B template matches the current source recipe without publishing anything. This is the drift gate used by continuous integration or release checks.

**Data flow**: It receives a template name and the digest expected from the current source. It boots a sandbox from the live template, reads the digest file baked into that image, kills the sandbox, and compares the live value with the expected value. If the digest is missing or different, it raises an error explaining that the template must be republished.

**Call relations**: The main command calls this for each size when `--check` is used. It depends on the digest created by `build_definition_digest`, turning the baked fingerprint into a simple up-to-date check.

*Call graph*: called by 1 (main); 1 external calls (create).


##### `main`  (lines 491–534)

```
def main() -> None
```

**Purpose**: Provides the command-line behavior for this script. Depending on the flags, it prints a Dockerfile, builds a Docker image, checks E2B templates for drift, or publishes and verifies fresh E2B templates.

**Data flow**: It reads command-line arguments. For `--dockerfile`, it writes Dockerfile text to standard output. For `--build-docker`, it builds the local Docker image. For `--check`, it computes each size’s expected digest and compares it with the live E2B template. With no special flag, it stages artifacts, builds each sized E2B template, verifies the published result, and prints references to the new builds.

**Call relations**: This is the top-level driver. It calls the smaller functions in the order needed for each mode: naming templates, staging artifacts, computing digests, rendering definitions, building images, checking drift, and verifying published templates.

*Call graph*: calls 9 internal fn (build_definition_digest, build_docker_image, check_published_template, e2b_template, pod_dockerfile, stage_client_binary, stage_system_skills, template_name, verify_published_template); 2 external calls (ArgumentParser, build).


### `sandbox/proxy_gate.py`

`entrypoint` · `deployment validation`

This script is a deployment gate: a test that must pass before the sandbox’s outbound HTTPS route is considered ready. The real problem it guards against is subtle but important. Sandboxes need to send web requests through a TLS proxy, and that proxy uses a certificate authority certificate so the sandbox can trust it. If that certificate is missing, installed incorrectly, or the proxy route is not ready, later sandbox work would fail with confusing network or security errors.

The script takes a public HTTPS proxy URL, reads the certificate and E2B template settings from environment variables, then creates a temporary sandbox. Inside that sandbox it writes the certificate into a staging path and runs the certificate-install command as root. After that, it repeatedly runs a tiny Python probe through the proxy.

The probe tries to reach Anthropic’s messages API using an intentionally invalid proxy run token. A successful gate is not a normal web success. It expects a 403 response, meaning the proxy was reached, TLS worked, and the request was rejected for the intended reason: bad credentials. That is like testing a locked front door by using the wrong key and confirming the lock says “no,” rather than finding that the doorbell wire is cut.

If the probe reports temporary connection trouble, the script waits and retries until a timeout. Any unexpected result causes an error. The sandbox is always killed at the end so the check does not leave temporary machines running.

#### Function details

##### `ProxyTlsGate.run`  (lines 69–118)

```
def run(self) -> None
```

**Purpose**: Runs the actual proxy TLS readiness check inside a fresh sandbox. It verifies that the proxy URL is HTTPS, installs the trusted certificate in the sandbox, then checks that HTTPS traffic through the proxy reaches the proxy and is rejected with the expected 403 status.

**Data flow**: It starts with three stored values: the public proxy URL, the certificate text, and the sandbox template name. It turns the proxy URL into a credentialed proxy address using a deliberately invalid run token, builds a command that runs a Python network probe inside the sandbox, creates the sandbox, writes and installs the certificate, and then runs the probe until it either sees the expected 403 result or times out. It prints a success message when the gate passes, raises an error when it fails, and always shuts down the sandbox afterward.

**Call relations**: This is called by main after command-line arguments and environment settings have been gathered. During the check it leans on the sandbox service to create the temporary machine, uses shell quoting to build a safe command string, uses the clock to enforce retry deadlines, and sleeps between retry attempts while waiting for the proxy route to become ready.

*Call graph*: 5 external calls (create, join, monotonic, sleep, urlsplit).


##### `main`  (lines 121–132)

```
def main() -> None
```

**Purpose**: Acts as the command-line entry point for the gate. It collects the proxy URL and required environment settings, chooses the correct sandbox template, and starts the check.

**Data flow**: It reads the --proxy-url argument from the command line, reads the certificate and template configuration from environment variables, and rejects the run immediately if either required environment value is missing. It converts the template configuration into a concrete sandbox template, creates a ProxyTlsGate with the proxy URL, certificate, and template, and asks it to run.

**Call relations**: This is what runs when the file is executed as a script. It performs the setup work and then hands the real validation to ProxyTlsGate.run, keeping command-line parsing and environment lookup separate from the sandbox probing logic.

*Call graph*: 3 external calls (__init__, ArgumentParser, sandbox_templates).

## 📊 State Registers Touched

- `reg-effective-config` — The current trusted settings for how the service should run, including database, provider, deployment, and safety options.
- `reg-pack-and-feature-selection` — The chosen product packs and feature switches that decide which parts of the system are enabled.
- `reg-extension-installation-lock` — The saved list of installed extensions and exact versions that should be loaded again consistently.
- `reg-workspace-directory` — The durable list of workspaces and their core ownership, admin, billing, and setup state.
- `reg-member-auth-principals` — The shared answer to who the current person or service is and what member identity they are acting as.
- `reg-cancellation-flags` — The shared stop signals and cleanup markers used to cancel turns, child work, sandboxes, and stuck jobs safely.
- `reg-credential-vault` — The encrypted store of API keys, connected accounts, grants, and approvals that lets tools use outside services without exposing secrets.
- `reg-egress-policy` — The shared network exit rules that decide which outside addresses sandboxes may contact and when secrets may be added.
- `reg-sandbox-handles` — The remembered execution workspaces, browser workbenches, terminal sessions, and sandbox IDs used across a conversation or turn.
- `reg-auth-and-oauth-flow-state` — Short-lived login and OAuth handoff state such as nonces, return targets, code-verifier data, pending claims, and callback correlation before it becomes an authenticated principal or stored credential.
- `reg-extension-catalog-update-cache` — Cached public extension catalog and update/compatibility check metadata used when selecting, installing, bundling, or refreshing extensions.
