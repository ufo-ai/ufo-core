# Process entrypoints, command modes, and deployment preparation  `stage-1`

This stage is the system’s front door. It covers the commands people run when they start UFO, prepare it for deployment, set up a new workspace, or check that its sandbox is safe to use. The pack selection part chooses a “toolbox” of extensions for the current setting, such as local development, hosted cloud use, billing tests, or evaluation runs.

The ufoctl command in cli.py is the main local control panel. It helps developers create, run, inspect, and manage a workspace. When a workspace is new, onboarding.py creates the first workspace, admin user, assistant agent, checks needed secret keys, and lets extensions finish their own setup. bundle.py packages the chosen setup into a reproducible Docker build folder with pinned extensions.

For hosted deployments, ufo_control/main.py provides maintenance commands, such as starting the hosted gateway, preparing the database, and issuing invitations. serve.py is the main service launcher: it assembles the web server, background workers, storage, credentials, extensions, and sandbox support. The sandbox scripts build the runtime image and test that its secure proxy route works before release.

## Sub-stages

- [Pack selection and deployment capability bundles](stage-1.1.md) `stage-1.1` — 9 files

## Files in this stage

### Local CLI and workspace setup
Human-facing commands initialize workspaces and package reproducible local deployments.

### `core/src/ufo/cli.py`

`entrypoint` · `operator command execution`

This file turns many backend capabilities into simple terminal commands. Without it, a newcomer would have to know which database setup, secret creation, onboarding, migration, server startup, billing, credential, extension, and debugging functions to call by hand. The file is like the front desk of the system: it checks what the user asked for, gathers the needed configuration and secrets, then sends the work to the right specialist module.

At startup, the command group loads a `.env` file beside the config so locally generated secrets are available automatically. The `init` command creates a default config if needed, writes development secrets, prepares the database, creates the first workspace and owner, and stores a long-lived CLI token on the machine. Other top-level commands start the main server, proxy, ingress service, or open the browser portal with a short local handoff page so the token is not placed in a URL.

The rest of the file exposes operator tools: database migrations, spend caps, prepaid balance, spend reports, transcript-read audit logs, OAuth grant summaries, encrypted extension credentials, extension search/install/remove, bundle creation, turn cancellation, and demo seed data. Most commands follow the same pattern: load config, open the right database scope, perform one focused action, print a human-readable result, and close database resources.

#### Function details

##### `_ufoctl_dir`  (lines 78–80)

```
def _ufoctl_dir() -> Path
```

**Purpose**: Finds the private directory where this machine stores `ufoctl` state, such as the CLI login token. It lets tests or deployments override the location with an environment variable.

**Data flow**: It reads the `UFOCTL_DIR` environment variable. If it is set, it turns that value into a filesystem path; otherwise it uses a `.ufoctl` folder in the current user's home directory. It returns that path and does not create it.

**Call relations**: `init` uses this path when writing the CLI token after onboarding. `portal` uses the same path later to read that token before opening a signed-in browser session.

*Call graph*: called by 2 (init, portal); 2 external calls (Path, home).


##### `_dotenv_path`  (lines 83–84)

```
def _dotenv_path() -> Path
```

**Purpose**: Locates the `.env` file that lives beside the main UFO config file. This keeps local secrets close to the config that needs them.

**Data flow**: It asks the config system where the config file is, takes that file's parent folder, and returns the path to `.env` inside it.

**Call relations**: The startup loader reads from this path, `init` reports it to the user, and secret/key helpers use it to decide what local environment values already exist or need to be written.

*Call graph*: called by 4 (_load_dotenv, _missing_deploy_keys, _write_dev_secrets, init); 1 external calls (config_path).


##### `_dotenv_pairs`  (lines 87–121)

```
def _dotenv_pairs(text: str) -> list[tuple[str, str]]
```

**Purpose**: Parses a simple `.env` file into name-and-value pairs. It supports the parts this project needs, including quoted multi-line secrets such as private keys.

**Data flow**: It receives raw text, skips blank lines and comments, accepts lines shaped like `KEY=VALUE`, strips optional `export` and matching quotes, and preserves multi-line quoted values. It returns a list of `(name, value)` pairs, or raises an error if a quote never closes.

**Call relations**: The environment loader uses it to fill missing variables. The init helpers use it to avoid overwriting existing secrets and to detect which deployment keys are still missing.

*Call graph*: called by 3 (_load_dotenv, _missing_deploy_keys, _write_dev_secrets).


##### `_load_dotenv`  (lines 124–133)

```
def _load_dotenv() -> None
```

**Purpose**: Loads local `.env` values into the process before any command tries to read secrets. Already exported environment variables win, so explicit shell settings are not overwritten.

**Data flow**: It finds the `.env` file, stops if it does not exist, parses it into pairs, and adds each value to `os.environ` only when that name is not already set.

**Call relations**: `main` calls this as the command group starts, so every subcommand gets the same smooth local behavior without each command repeating the loading step.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (main).


##### `main`  (lines 137–139)

```
def main() -> None
```

**Purpose**: Defines the root `ufoctl` command group. It is the entry point that Click, the command-line framework, uses before dispatching to a specific subcommand.

**Data flow**: It receives no user arguments directly. When invoked, it loads `.env` values into the process, then Click continues to whichever command the user requested.

**Call relations**: All commands in this file hang underneath this group. Its main job is shared startup preparation before the selected command runs.

*Call graph*: calls 1 internal fn (_load_dotenv).


##### `_one_address`  (lines 142–147)

```
def _one_address(_ctx: click.Context, _param: click.Parameter, value: str) -> str
```

**Purpose**: Checks that the owner email passed to `init` looks like exactly one `local@domain` address. This gives the user a clear command-line error before any database write happens.

**Data flow**: It receives the raw option value from Click, asks the seat/email helper whether it has a valid domain shape, and returns the same value if valid. If not, it raises a Click parameter error.

**Call relations**: Click calls this automatically while parsing `ufoctl init --email ...`. If it accepts the value, `init` can safely use it for onboarding.

*Call graph*: 2 external calls (BadParameter, email_domain).


##### `init`  (lines 153–185)

```
def init(email: str, model: str) -> None
```

**Purpose**: Bootstraps a new UFO workspace for local or initial use. It creates default config, secrets, database schema, the first owner/member/agent setup, and a CLI token.

**Data flow**: It takes an owner email and model name from the command line. It writes a config if missing, generates needed local secrets, creates a PostgreSQL system database when needed, applies migrations, runs onboarding, mints a signed CLI token, saves it under the `ufoctl` directory, and prints next-step messages.

**Call relations**: This is usually the first command a user runs. It coordinates many helpers in this file and hands real creation work to the config, migration, onboarding, token, and extension systems.

*Call graph*: calls 6 internal fn (_create_postgres_system_database, _dotenv_path, _missing_deploy_keys, _onboard, _ufoctl_dir, _write_dev_secrets); 7 external calls (run, ClickException, echo, mint_token, config_path, load_config, apply_migrations).


##### `_missing_deploy_keys`  (lines 188–203)

```
def _missing_deploy_keys(config: Config) -> tuple[str, ...]
```

**Purpose**: Reports provider API keys that installed extensions say they need but the environment does not currently provide. It warns instead of blocking, so the server can still start for features that do not need those keys.

**Data flow**: It reads extension manifests for the configured pack, gathers their declared deployment key names, reads names already in `.env` and the current environment, and returns the missing names sorted.

**Call relations**: `init` calls this near the end so the user sees missing optional setup while they are already configuring the project.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 1 external calls (load_manifests).


##### `_write_dev_secrets`  (lines 206–228)

```
def _write_dev_secrets(config: Config) -> tuple[str, ...]
```

**Purpose**: Creates local development secrets needed for a zero-configuration server run, without overwriting existing secrets. These include keys for encrypted credentials and signed tokens.

**Data flow**: It builds fresh random values, reads existing `.env` and environment names, keeps only names that are absent, appends those to `.env`, mirrors them into the current process environment, and returns the names it added.

**Call relations**: `init` calls this before onboarding and token minting so later steps can rely on the needed secrets being present.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 2 external calls (generate_key, token_urlsafe).


##### `_onboard`  (lines 231–250)

```
async def _onboard(config: Config, email: str, model: str) -> Onboarded
```

**Purpose**: Creates the initial workspace, owner, default agent, model setup, and extension onboarding data. It does this inside a database lifecycle that is opened once and always closed.

**Data flow**: It receives config, owner email, and model name. It initializes the database, optionally builds an encrypted credential store from the configured key, creates an `Onboarding` object with extension manifests, runs core creation and extension steps, returns the onboarding result, and disposes database resources.

**Call relations**: `init` calls this after migrations are ready. This helper delegates the actual workspace creation rules to the onboarding subsystem.

*Call graph*: called by 1 (init); 6 external calls (__init__, __init__, Fernet, dispose_db, init_db, load_manifests).


##### `_create_postgres_system_database`  (lines 253–264)

```
async def _create_postgres_system_database(config: Config) -> None
```

**Purpose**: Ensures the separate PostgreSQL system database exists before migrations use it. This is only needed for PostgreSQL setups.

**Data flow**: It derives a plain PostgreSQL connection string from config, connects, checks whether the configured system database exists, creates it if missing, and closes the connection.

**Call relations**: `init` calls this before applying migrations when the app database URL points at PostgreSQL.

*Call graph*: called by 1 (init); 1 external calls (connect).


##### `migrate`  (lines 268–285)

```
def migrate() -> None
```

**Purpose**: Applies database migrations so the core schema and active extension schemas are up to date. It is safe to run again after installing extensions.

**Data flow**: It loads config, chooses either an owner database URL from the environment or the normal config URL, applies migrations for the configured pack, and prints confirmation.

**Call relations**: Users or deployment jobs invoke this command before serving new code or newly installed extensions. It hands schema work to the migration system.

*Call graph*: 3 external calls (echo, load_config, apply_migrations).


##### `serve`  (lines 289–298)

```
def serve() -> None
```

**Purpose**: Starts the main UFO runtime: web surfaces, workers, and jobs. Before starting, it tells the user where the browser portal will be if the installed pack has one.

**Data flow**: It loads config, checks extension manifests for a home browser surface, prints the portal URL when available, and then starts the serving system.

**Call relations**: This is the command that turns an initialized workspace into a running service. It relies on the serve module for the long-running work.

*Call graph*: calls 1 internal fn (_serve_base); 5 external calls (echo, load_config, load_manifests, home_surface, run).


##### `portal`  (lines 302–320)

```
def portal() -> None
```

**Purpose**: Opens the workspace portal in the user's browser and signs it in using this machine's stored CLI token. It avoids making the user copy and paste a token.

**Data flow**: It loads config, finds the configured portal surface, reads the saved token, checks that the server answers, starts a one-use browser handoff, and prints the URL it opened. If the server or token is missing, it gives a clear error.

**Call relations**: Users run this after `serve` is running. It uses `BrowserHandoff` to pass the token to the browser safely.

*Call graph*: calls 2 internal fn (_serve_base, _ufoctl_dir); 7 external calls (__init__, ClickException, echo, get, load_config, load_manifests, home_surface).


##### `_serve_base`  (lines 323–324)

```
def _serve_base(config: Config) -> str
```

**Purpose**: Builds the local base URL for the running UFO server from config. It centralizes the host-and-port formatting.

**Data flow**: It reads the configured serve host and port and returns a string like `http://host:port`.

**Call relations**: `serve` uses it for the printed portal hint. `portal` uses it to check reachability and build the portal URL.

*Call graph*: called by 2 (portal, serve).


##### `BrowserHandoff.open`  (lines 339–347)

```
def open(self) -> None
```

**Purpose**: Starts a tiny one-use local web server that gives the browser a page containing the CLI token in a form body. This signs the browser in without putting the token in the address bar.

**Data flow**: It creates a random unguessable path, opens a loopback-only HTTP server on a random port, asks the default browser to visit it, serves requests until the one correct page is delivered, then exits.

**Call relations**: `portal` creates a `BrowserHandoff` and calls this when the server is reachable and a token exists. It calls `_responder` to build the special request handler.

*Call graph*: calls 1 internal fn (_responder); 5 external calls (echo, HTTPServer, token_urlsafe, Event, open).


##### `BrowserHandoff._responder`  (lines 349–366)

```
def _responder(self, path: str, delivered: threading.Event) -> type[BaseHTTPRequestHandler]
```

**Purpose**: Creates the request handler class used by the one-use browser handoff server. The handler only serves the secret handoff page at the exact random path.

**Data flow**: It receives the allowed path and a delivery event, builds the HTML page once, and returns a `BaseHTTPRequestHandler` subclass that can serve it.

**Call relations**: `BrowserHandoff.open` asks this for a handler before starting the temporary server. The returned handler uses `_page` for the actual HTML.

*Call graph*: calls 1 internal fn (_page); called by 1 (open).


##### `BrowserHandoff._responder.do_GET`  (lines 353–362)

```
def do_GET(self) -> None
```

**Purpose**: Serves the handoff page for the one correct browser request, or rejects any other path. It marks the token as delivered after writing the page.

**Data flow**: It reads the incoming request path. If it is wrong, it sends a 404 error; if it matches, it sends an HTML response containing the auto-submitting form and sets the delivery event.

**Call relations**: The temporary HTTP server calls this when the browser requests the local handoff URL. Setting the event lets `BrowserHandoff.open` stop listening.


##### `BrowserHandoff._responder.log_message`  (lines 364–364)

```
def log_message(self, *args: object) -> None
```

**Purpose**: Suppresses the default local HTTP server logging. This keeps the terminal output clean during browser sign-in.

**Data flow**: It receives log message arguments from the HTTP server and deliberately does nothing with them.

**Call relations**: The generated request handler uses this automatically whenever the HTTP server would normally print a request log.


##### `BrowserHandoff._page`  (lines 368–375)

```
def _page(self) -> str
```

**Purpose**: Builds the small HTML page that posts the CLI token to the portal. The form auto-submits, with a button as a fallback.

**Data flow**: It reads the handoff object's portal URL and token, escapes them for safe HTML, and returns a complete HTML string containing a hidden token field.

**Call relations**: `_responder` calls this while preparing the response body for the temporary browser handoff server.

*Call graph*: called by 1 (_responder); 1 external calls (escape).


##### `proxy`  (lines 379–381)

```
def proxy() -> None
```

**Purpose**: Starts the shared egress proxy, which is the service sandboxes use to reach outside resources through one controlled front door.

**Data flow**: It takes no command-specific input and simply starts the proxy server's run loop.

**Call relations**: Click invokes this when the user runs `ufoctl proxy`. The actual network behavior lives in the proxy serving module.

*Call graph*: 1 external calls (run).


##### `ingress`  (lines 385–387)

```
def ingress() -> None
```

**Purpose**: Starts the sandbox ingress service, a token-protected reverse proxy into sandbox ports. In plain terms, it lets approved traffic reach a sandbox safely.

**Data flow**: It takes no command-specific input and starts the ingress server's run loop.

**Call relations**: Click invokes this for `ufoctl ingress`. It hands off immediately to the ingress serving module.

*Call graph*: 1 external calls (run).


##### `spend_cap`  (lines 391–392)

```
def spend_cap() -> None
```

**Purpose**: Defines the command group for reading and setting spend caps. Spend caps are money limits enforced before or during model work.

**Data flow**: It does not process data itself. It groups subcommands such as `set` and `list` under `ufoctl spend-cap`.

**Call relations**: Click uses this as a parent command so the spend-cap subcommands share one namespace.


##### `spend_cap_set`  (lines 403–421)

```
def spend_cap_set(scope: str, subject_id: str, window_seconds: int, limit_micro_usd: int, on_breach: str) -> None
```

**Purpose**: Creates or updates a spend cap for the workspace, a member, or an agent. This gives operators a direct way to limit spending over a time window.

**Data flow**: It reads command options for scope, optional subject id, time window, limit, and breach behavior. It validates that the subject id matches the chosen scope, writes the cap through `_write_spend_cap`, then prints the cap id and dollar amount.

**Call relations**: Click calls this for `ufoctl spend-cap set`. It performs user-facing validation and delegates the database insert-or-update to `_write_spend_cap`.

*Call graph*: calls 1 internal fn (_write_spend_cap); 5 external calls (run, ClickException, echo, load_config, UUID).


##### `spend_cap_list`  (lines 425–435)

```
def spend_cap_list() -> None
```

**Purpose**: Shows all spend caps currently set for the workspace. It gives operators a quick view of active cost limits.

**Data flow**: It loads config, reads caps from the database, prints a friendly empty message if none exist, otherwise formats each cap with scope, subject, amount, window, and breach behavior.

**Call relations**: Click calls this for `ufoctl spend-cap list`. It delegates database reading to `_read_spend_caps`.

*Call graph*: calls 1 internal fn (_read_spend_caps); 3 external calls (run, echo, load_config).


##### `_write_spend_cap`  (lines 438–492)

```
async def _write_spend_cap(config: Config, scope: str, subject: UUID | None, window_seconds: int, limit_micro_usd: int, on_breach: str) -> UUID
```

**Purpose**: Writes a spend cap to the database, updating an existing matching cap instead of creating duplicates. Matching means the same workspace, scope, subject, and time window.

**Data flow**: It opens the database, finds the workspace id, searches for an existing matching cap, updates its limit and behavior if found, or inserts a new cap with a new id. It returns the cap id and always closes database resources.

**Call relations**: `spend_cap_set` calls this after validating command-line input. The helper is the point where the user request becomes a database change.

*Call graph*: called by 1 (spend_cap_set); 7 external calls (insert, select, update, dispose_db, init_db, workspace_tx, uuid4).


##### `_read_spend_caps`  (lines 495–521)

```
async def _read_spend_caps(config: Config) -> list[tuple[UUID, str, UUID | None, int, int, str]]
```

**Purpose**: Reads all spend caps for the current workspace. It returns only the fields needed for the command-line display.

**Data flow**: It opens the database, finds the workspace id, selects cap rows for that workspace ordered by scope, converts rows into simple tuples, and closes the database.

**Call relations**: `spend_cap_list` calls this and then formats the returned data for humans.

*Call graph*: called by 1 (spend_cap_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `balance`  (lines 525–526)

```
def balance() -> None
```

**Purpose**: Defines the command group for prepaid workspace balance operations. Balance commands let operators view funds, add credit, and set required reserve.

**Data flow**: It does not process data itself. It groups `show`, `credit`, and `reserve` under `ufoctl balance`.

**Call relations**: Click uses this as the parent for balance subcommands.


##### `balance_show`  (lines 531–546)

```
def balance_show(workspace_id: str) -> None
```

**Purpose**: Prints the current prepaid balance and related lifetime totals. It helps an operator see whether work has enough money to start.

**Data flow**: It reads an optional workspace id, loads config, asks `_read_balance` for the current balance, prints `no balance` if absent, otherwise formats balance, reserve, granted, charged, and last purchase time.

**Call relations**: Click calls this for `ufoctl balance show`. It leaves workspace selection and database access to `_read_balance`.

*Call graph*: calls 1 internal fn (_read_balance); 3 external calls (run, echo, load_config).


##### `balance_credit`  (lines 554–567)

```
def balance_credit(granted_micro_usd: int, charged_micro_usd: int, reference: str, workspace_id: str) -> None
```

**Purpose**: Adds a credit or correction to a workspace balance, once per reference key. The reference makes the operation safe to retry without double-crediting.

**Data flow**: It reads granted and charged amounts, a reference, and optional workspace id. It rejects a zero granted amount, calls `_credit_balance`, and prints whether a new credit was applied or already existed.

**Call relations**: Click calls this for `ufoctl balance credit`. It delegates idempotent accounting to the balance module through `_credit_balance`.

*Call graph*: calls 1 internal fn (_credit_balance); 4 external calls (run, ClickException, echo, load_config).


##### `balance_reserve`  (lines 573–581)

```
def balance_reserve(micro_usd: int, workspace_id: str) -> None
```

**Purpose**: Sets the minimum prepaid headroom required before a turn may begin. This protects the system from starting work when the balance is too low.

**Data flow**: It reads the reserve amount and optional workspace id, rejects negative values, calls `_set_reserve`, and prints success or an error if no balance record exists yet.

**Call relations**: Click calls this for `ufoctl balance reserve`. It uses `_set_reserve` to make the database change inside the correct workspace.

*Call graph*: calls 1 internal fn (_set_reserve); 4 external calls (run, ClickException, echo, load_config).


##### `_target_workspace`  (lines 584–606)

```
async def _target_workspace(named: str) -> UUID
```

**Purpose**: Decides which workspace an operator command should act on. If no workspace is named, it only chooses automatically when there is exactly one workspace.

**Data flow**: It reads across workspaces through an owner-level transaction. If a workspace id is provided, it validates that it exists and returns it. If none is provided, it returns the sole workspace or raises a clear error for zero or many workspaces.

**Call relations**: `_balance_scope` and `_seed_target` call this before doing workspace-bound work. It prevents multi-workspace deployments from accidentally acting on the wrong tenant.

*Call graph*: called by 2 (_balance_scope, _seed_target); 4 external calls (ClickException, select, owner_tx, UUID).


##### `_balance_scope`  (lines 610–627)

```
async def _balance_scope(config: Config, named: str) -> AsyncIterator[tuple[AsyncConnection, UUID]]
```

**Purpose**: Opens the correct database context for balance commands. It first resolves the target workspace, then binds later reads and writes to that workspace.

**Data flow**: It initializes the app database and optional owner database, finds the target workspace id, enters a workspace context, opens a workspace transaction, yields the connection and workspace id, then disposes database resources afterward.

**Call relations**: `_read_balance`, `_credit_balance`, and `_set_reserve` use this shared setup so all balance operations select the workspace in the same safe way.

*Call graph*: calls 1 internal fn (_target_workspace); called by 3 (_credit_balance, _read_balance, _set_reserve); 5 external calls (dispose_db, init_db, init_owner_db, workspace_tx, ws).


##### `_read_balance`  (lines 630–632)

```
async def _read_balance(config: Config, named: str) -> Balance | None
```

**Purpose**: Reads the balance record for a selected workspace. It is the database helper behind the `balance show` command.

**Data flow**: It enters `_balance_scope`, receives a database connection and workspace id, asks the balance module to read the record, and returns either a `Balance` object or `None`.

**Call relations**: `balance_show` calls this after loading config. This helper connects the command-line request to the reusable balance logic.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_show); 1 external calls (read_balance).


##### `_credit_balance`  (lines 635–641)

```
async def _credit_balance(config: Config, named: str, granted_micro_usd: int, charged_micro_usd: int, reference: str) -> bool
```

**Purpose**: Applies a balance credit or correction for a selected workspace. It returns whether the credit was newly applied.

**Data flow**: It enters `_balance_scope`, passes the connection, workspace id, amounts, and reference to the balance module, and returns the boolean result.

**Call relations**: `balance_credit` calls this after validating user input. The lower-level balance module enforces the one-credit-per-reference rule.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_credit); 1 external calls (credit).


##### `_set_reserve`  (lines 644–646)

```
async def _set_reserve(config: Config, named: str, reserve_micro_usd: int) -> bool
```

**Purpose**: Updates the reserve amount for a selected workspace balance. The reserve is the required cushion before work can start.

**Data flow**: It enters `_balance_scope`, passes the connection, workspace id, and reserve amount to the balance module, and returns whether a balance row was found and updated.

**Call relations**: `balance_reserve` calls this after checking that the amount is not negative.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_reserve); 1 external calls (set_reserve).


##### `spend`  (lines 654–677)

```
def spend(window_seconds: int) -> None
```

**Purpose**: Prints a spending report for a recent time window. It breaks cost down by dimension, member, agent, origin, and price version.

**Data flow**: It reads the window length from the command line, loads config, gets a `SpendReport`, converts micro-dollars to dollars, and prints the total plus each breakdown section.

**Call relations**: Click invokes this for `ufoctl spend`. It relies on `_read_spend` to gather the accounting data.

*Call graph*: calls 1 internal fn (_read_spend); 3 external calls (run, echo, load_config).


##### `_read_spend`  (lines 680–687)

```
async def _read_spend(config: Config, window_seconds: int) -> SpendReport
```

**Purpose**: Reads a spend rollup for the current workspace over a requested time window. A rollup is a summarized view of many ledger entries.

**Data flow**: It opens the database, finds the workspace id, creates a spend rollup reader for that workspace, asks it for the report, returns the report, and closes resources.

**Call relations**: `spend` calls this and then turns the structured report into terminal output.

*Call graph*: called by 1 (spend); 5 external calls (__init__, select, dispose_db, init_db, workspace_tx).


##### `transcript_reads`  (lines 695–708)

```
def transcript_reads(limit: int) -> None
```

**Purpose**: Lists audit records for admin reads of another member's private transcript. This gives operators a command-line view of sensitive transcript access disclosures.

**Data flow**: It reads a limit, rejects values below one, loads config, fetches recent transcript access records, prints an empty message if none exist, otherwise prints time, reader, subject, and conversation id.

**Call relations**: Click calls this for `ufoctl transcript-reads`. It delegates the database query to `_read_transcript_accesses`.

*Call graph*: calls 1 internal fn (_read_transcript_accesses); 4 external calls (run, ClickException, echo, load_config).


##### `_read_transcript_accesses`  (lines 711–745)

```
async def _read_transcript_accesses(config: Config, limit: int) -> list[tuple[str, str, UUID, datetime]]
```

**Purpose**: Reads recent transcript access disclosure records for the current workspace. It joins member records so the command can show email addresses instead of only ids.

**Data flow**: It opens the database, aliases the member table for reader and subject, finds the workspace id, selects recent access rows up to the requested limit, converts them into tuples, and closes resources.

**Call relations**: `transcript_reads` calls this and formats the returned audit records for display.

*Call graph*: called by 1 (transcript_reads); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `grants`  (lines 749–761)

```
def grants() -> None
```

**Purpose**: Lists OAuth account grants that agents can use. OAuth is a standard way to let an app access an external account without storing the account password.

**Data flow**: It loads config, reads grant summaries, prints `no grants` if empty, otherwise prints agent, provider, account id, sharing scope, and grant date.

**Call relations**: Click calls this for `ufoctl grants`. It gets data through `_read_grants`, which uses the grants subsystem.

*Call graph*: calls 1 internal fn (_read_grants); 3 external calls (run, echo, load_config).


##### `_read_grants`  (lines 764–771)

```
async def _read_grants(config: Config) -> tuple[GrantSummary, ...]
```

**Purpose**: Reads summarized OAuth grants for the current workspace. It identifies the workspace first, then asks the grants module for the full summary.

**Data flow**: It opens the database, reads the workspace id, calls the grant-summary helper, returns the tuple of summaries, and closes database resources.

**Call relations**: `grants` calls this before printing the human-readable grant list.

*Call graph*: called by 1 (grants); 5 external calls (select, dispose_db, init_db, workspace_tx, workspace_grant_summaries).


##### `credential`  (lines 775–777)

```
def credential() -> None
```

**Purpose**: Defines the command group for encrypted BYOK credential slots. BYOK means “bring your own key,” where an operator or member supplies a secret needed by an extension.

**Data flow**: It does not process data itself. It groups credential subcommands such as `set` and `list`.

**Call relations**: Click uses this as the parent for credential-related commands.


##### `credential_set`  (lines 782–805)

```
def credential_set(slot: str) -> None
```

**Purpose**: Stores one declared credential secret securely, without accepting it as a visible command-line argument. The value is encrypted before storage.

**Data flow**: It loads config, checks that the slot is declared by an extension and is allowed to be filled by a person, reads the encryption key from the environment, prompts hidden or reads from stdin, rejects empty values, writes the credential, and prints confirmation.

**Call relations**: Click calls this for `ufoctl credential set SLOT`. It uses `_declared_slots`, `_fillable_slots`, and `_write_credential` to validate and store the secret.

*Call graph*: calls 3 internal fn (_declared_slots, _fillable_slots, _write_credential); 5 external calls (run, ClickException, echo, prompt, load_config).


##### `credential_list`  (lines 809–819)

```
def credential_list() -> None
```

**Purpose**: Shows which declared credential slots exist and whether each has been set. It never prints secret values.

**Data flow**: It loads config, gathers declared slots from extension manifests, reads stored slot names from the database, and prints each slot with its owning extension and set/unset status.

**Call relations**: Click calls this for `ufoctl credential list`. It uses `_declared_slots` for declarations and `_read_stored_slots` for stored state.

*Call graph*: calls 2 internal fn (_declared_slots, _read_stored_slots); 3 external calls (run, echo, load_config).


##### `_declared_slots`  (lines 822–827)

```
def _declared_slots(config: Config) -> dict[str, str]
```

**Purpose**: Builds a map of credential slot names to the extension that declared them. This is the source of truth for which credential names are valid.

**Data flow**: It loads extension manifests for the configured pack and returns a dictionary from slot name to manifest name. If manifests cannot be loaded, it turns that into a command-line error.

**Call relations**: `credential_set` uses this to reject unknown slots. `credential_list` uses it to know what to display.

*Call graph*: called by 2 (credential_list, credential_set); 2 external calls (ClickException, load_manifests).


##### `_fillable_slots`  (lines 830–839)

```
def _fillable_slots(config: Config) -> frozenset[str]
```

**Purpose**: Finds which credential slots may be filled by a human operator or member. Some slots are written by the deployment itself and should not be typed in.

**Data flow**: It loads extension manifests, filters credential declarations to those marked member-fillable, and returns their names as a frozen set. Manifest loading errors become Click errors.

**Call relations**: `credential_set` calls this after confirming the slot exists, so it can block attempts to type values for machine-written slots.

*Call graph*: called by 1 (credential_set); 2 external calls (ClickException, load_manifests).


##### `_write_credential`  (lines 842–849)

```
async def _write_credential(config: Config, key: str, slot: str, value: str) -> None
```

**Purpose**: Encrypts and stores a credential value for the current workspace. It keeps the command-line code separate from the credential storage details.

**Data flow**: It opens the database, finds the workspace id, creates a credential store using the supplied Fernet encryption key, writes the slot value for that workspace, and closes resources.

**Call relations**: `credential_set` calls this once it has safely collected and validated the secret.

*Call graph*: called by 1 (credential_set); 6 external calls (__init__, Fernet, select, dispose_db, init_db, workspace_tx).


##### `_read_stored_slots`  (lines 852–866)

```
async def _read_stored_slots(config: Config) -> frozenset[str]
```

**Purpose**: Reads which credential slots have stored values for the current workspace. It returns names only, never the encrypted or plain secret values.

**Data flow**: It opens the database, finds the workspace id, selects credential slot names for that workspace, returns them as a frozen set, and closes resources.

**Call relations**: `credential_list` calls this so it can mark each declared slot as set or unset.

*Call graph*: called by 1 (credential_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `ext`  (lines 870–871)

```
def ext() -> None
```

**Purpose**: Defines the command group for extension store operations. Extensions add capabilities to a UFO pack.

**Data flow**: It does not process data itself. It groups search, install, and remove commands under `ufoctl ext`.

**Call relations**: Click uses this as the parent for extension-store subcommands.


##### `_store`  (lines 874–877)

```
def _store(config: Config) -> ExtensionStore
```

**Purpose**: Creates an extension store object from config and the local lockfile. The store is what search, install, and remove commands operate on.

**Data flow**: It checks that an extension store is enabled in config, reads the catalog, locates the lockfile, and returns an `ExtensionStore`. If no store is configured, it raises a command-line error.

**Call relations**: `ext_search`, `ext_install`, and `ext_remove` all call this before doing store work.

*Call graph*: called by 3 (ext_install, ext_remove, ext_search); 4 external calls (__init__, ClickException, lockfile_path, read_catalog).


##### `ext_search`  (lines 882–895)

```
def ext_search(query: str) -> None
```

**Purpose**: Searches the configured extension catalog and shows matching extensions. It also tells the user whether each match is already installed, available, or bundle-only.

**Data flow**: It loads config, creates the extension store, searches with the optional query, and prints either an empty message or one line per listing.

**Call relations**: Click calls this for `ufoctl ext search`. It uses `_store` to avoid duplicating catalog and lockfile setup.

*Call graph*: calls 1 internal fn (_store); 2 external calls (echo, load_config).


##### `ext_install`  (lines 900–906)

```
def ext_install(name: str) -> None
```

**Purpose**: Pins an extension from the store into the lockfile so future runs load it. Pinning records the exact version and digest.

**Data flow**: It loads config, creates the store, asks it to install the named extension, turns install errors into Click errors, and prints the installed name, version, and digest.

**Call relations**: Click calls this for `ufoctl ext install NAME`. After this, the user typically runs migrations and restarts serving.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `ext_remove`  (lines 911–917)

```
def ext_remove(name: str) -> None
```

**Purpose**: Removes an extension pin from the lockfile so future runs stop loading it. It does not itself run the server or migrations.

**Data flow**: It loads config, creates the store, asks it to remove the named extension, turns errors into Click errors, and prints confirmation.

**Call relations**: Click calls this for `ufoctl ext remove NAME`. It uses the same `_store` setup as search and install.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `_ufo_project_dir`  (lines 923–934)

```
def _ufo_project_dir() -> Path
```

**Purpose**: Finds the source project directory needed to build the UFO Python wheel for a bundle. It searches upward from this file instead of trusting the current shell directory.

**Data flow**: It walks parent directories, reads any `pyproject.toml` it finds, and returns the first ancestor whose project name is `ufo`. If none is found, it raises a clear error saying a source checkout is required.

**Call relations**: `bundle` calls this before running the wheel build command, so bundling works from any directory inside the source tree.

*Call graph*: called by 1 (bundle); 3 external calls (ClickException, Path, loads).


##### `bundle`  (lines 941–957)

```
def bundle(out: Path) -> None
```

**Purpose**: Freezes the current deployment into a runnable bundle, including config, extension pins, and a built UFO wheel. This prepares a portable artifact rather than a live server.

**Data flow**: It loads config, optionally reads the extension catalog, builds bundle files into the output directory, runs `uv build` to create a wheel, verifies the wheel exists, and prints the bundle location plus pinned extensions.

**Call relations**: Click calls this for `ufoctl bundle`. It coordinates the bundle builder, project-directory finder, external wheel build command, and final user summary.

*Call graph*: calls 1 internal fn (_ufo_project_dir); 8 external calls (__init__, ClickException, echo, run, wheel_name, config_path, load_config, read_catalog).


##### `turn`  (lines 961–962)

```
def turn() -> None
```

**Purpose**: Defines the command group for acting on a single turn. A turn is one unit of conversation work.

**Data flow**: It does not process data itself. It groups turn-specific subcommands such as `cancel`.

**Call relations**: Click uses this as the parent for turn operations.


##### `turn_cancel`  (lines 967–977)

```
def turn_cancel(turn_id: str) -> None
```

**Purpose**: Cancels one turn by id when it is stuck or should not continue. If the turn is already finished, it reports that nothing changed.

**Data flow**: It reads the turn id, loads config, converts the id to a UUID, calls `_cancel_turn`, and prints either `cancelled` or `was already terminal`.

**Call relations**: Click calls this for `ufoctl turn cancel TURN_ID`. It delegates workspace lookup and durable workflow cancellation to `_cancel_turn`.

*Call graph*: calls 1 internal fn (_cancel_turn); 4 external calls (run, echo, load_config, UUID).


##### `_cancel_turn`  (lines 980–1002)

```
async def _cancel_turn(config: Config, turn_id: UUID) -> bool
```

**Purpose**: Finds which workspace owns a turn and cancels that turn inside the correct workspace context. This is important in hosted deployments where many workspaces share infrastructure.

**Data flow**: It initializes database access, optionally initializes owner-level access, reads the turn's workspace id through an owner transaction, errors if no turn exists, creates a replay-safe durable-workflow client, binds to the workspace, calls the cancellation helper, returns whether cancellation happened, and disposes resources.

**Call relations**: `turn_cancel` calls this after parsing the id. It bridges the operator command to the cancellation subsystem while preserving tenant boundaries.

*Call graph*: called by 1 (turn_cancel); 9 external calls (ClickException, select, cancel_one_turn, dispose_db, init_db, init_owner_db, owner_tx, replay_safe_client, ws).


##### `seed`  (lines 1006–1007)

```
def seed() -> None
```

**Purpose**: Defines the command group for writing demonstration content into a workspace. Seed data helps reviewers and developers see UI shapes with real stored data.

**Data flow**: It does not process data itself. It groups seed subcommands such as `kitchen-sink`.

**Call relations**: Click uses this as the parent for seed commands.


##### `seed_kitchen_sink`  (lines 1012–1021)

```
def seed_kitchen_sink(workspace_id: str) -> None
```

**Purpose**: Writes a demonstration conversation that includes many content shapes the portal can display. It prints the portal path where the seeded conversation can be viewed.

**Data flow**: It reads an optional workspace id, loads config, asks `_seed_kitchen_sink` to write the demo content, and prints a URL fragment pointing at the conversation.

**Call relations**: Click calls this for `ufoctl seed kitchen-sink`. It delegates workspace setup and actual content writing to helper functions and the seed subsystem.

*Call graph*: calls 1 internal fn (_seed_kitchen_sink); 3 external calls (run, echo, load_config).


##### `_seed_kitchen_sink`  (lines 1024–1035)

```
async def _seed_kitchen_sink(config: Config, named: str) -> UUID
```

**Purpose**: Sets up database and blob storage access for the kitchen-sink seed operation. Blob storage is where file-like content is kept.

**Data flow**: It initializes app and optional owner database connections, builds a workspace blob store from config, calls `_seed_target` with that store and the requested workspace name, returns the new conversation id, and closes database resources.

**Call relations**: `seed_kitchen_sink` calls this as the async worker. It prepares infrastructure, while `_seed_target` chooses the workspace and writes the content.

*Call graph*: calls 1 internal fn (_seed_target); called by 1 (seed_kitchen_sink); 5 external calls (__init__, blob_store_for, dispose_db, init_db, init_owner_db).


##### `_seed_target`  (lines 1038–1069)

```
async def _seed_target(blob: WorkspaceBlobStore, named: str) -> UUID
```

**Purpose**: Writes the kitchen-sink demo conversation into the chosen workspace. It finds the main agent and first member so the demo looks like normal workspace content.

**Data flow**: It resolves the workspace id, enters that workspace context, reads the main agent id and earliest member, errors if no member exists, then creates a `KitchenSink` writer with blob storage and identity details and returns the conversation id it writes.

**Call relations**: `_seed_kitchen_sink` calls this after setting up shared resources. It uses `_target_workspace` for safe workspace selection and hands final content creation to the seed module.

*Call graph*: calls 1 internal fn (_target_workspace); called by 1 (_seed_kitchen_sink); 5 external calls (__init__, ClickException, select, workspace_tx, ws).


### `core/src/ufo/bundle.py`

`orchestration` · `during the `ufoctl bundle` command, before building or running the container`

This file is the machinery behind the idea of “freezing” a UFO deployment into something repeatable. Without it, a deployment could depend on whatever extensions happen to be installed on a machine at runtime, which makes it harder to trust that one server will behave like another.

The bundle works like packing a travel kit before a trip. It copies the current configuration, writes a fresh lockfile listing the exact extensions and versions/digests to use, and creates a Dockerfile that can build a runnable container image. The lockfile matters because it is a promise: when the bundle starts later, UFO can check that the extensions match what was frozen.

The main class, `Bundle`, takes three things: the path to the current config, an optional extension catalog, and an output folder. Its `build` method creates the output folder, copies the config, writes the lockfile, writes the Dockerfile, and returns a small `BundleResult` showing what was produced.

The file also decides which extensions must be pinned. If there is already a lockfile, it starts from that. If not, it uses the extensions currently discovered in the environment. If a catalog is available, it also adds “bundle-only” extensions: entries marked disabled in the store, meaning they are installed during bundling rather than later at runtime.

#### Function details

##### `wheel_name`  (lines 25–28)

```
def wheel_name() -> str
```

**Purpose**: This function builds the filename of the UFO Python wheel that the generated Dockerfile will install. A wheel is a packaged Python distribution file; here it represents the UFO code that must be copied into the Docker build context.

**Data flow**: It reads the current UFO version from the extension store version helper, inserts that version into the standard wheel filename pattern, and returns the finished filename string.

**Call relations**: The Dockerfile generator calls this when it needs to write the `COPY` and `pip install` lines. That keeps the Dockerfile tied to the exact UFO version being bundled.

*Call graph*: called by 1 (_dockerfile); 1 external calls (ufo_version).


##### `Bundle.build`  (lines 49–62)

```
def build(self) -> BundleResult
```

**Purpose**: This is the main bundle-making step. It creates the output directory and writes the three files that make the deployment portable: the config file, the lockfile, and the Dockerfile.

**Data flow**: It starts with the bundle’s input config path, optional catalog, and output folder. It asks `_pins` for the exact extension pins, creates the output folder if needed, copies the config text, writes a JSON lockfile containing the UFO version and pinned extensions, writes the Dockerfile text from `_dockerfile`, and returns a `BundleResult` that points to all produced files and records the pins.

**Call relations**: This method is the coordinator for the whole file. It first calls `_pins` to decide what must be frozen, then calls `_dockerfile` to create the container recipe, and finally packages the results into `BundleResult` for the caller of the bundle command.

*Call graph*: calls 2 internal fn (_dockerfile, _pins); 3 external calls (__init__, __init__, ufo_version).


##### `Bundle._pins`  (lines 64–78)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: This function decides which extensions belong in the bundle lockfile. It makes sure the bundle includes what the current deployment already uses, plus any catalog entries that are meant to be installed only at bundle time.

**Data flow**: It first asks the extension loader what extensions are installed or discoverable. Then it checks whether an existing lockfile exists. If there is one, it uses the extension names already pinned there; if not, it uses every discovered extension. If a catalog is available, it adds catalog extensions marked disabled, which are treated as bundle-only. It removes duplicate names while keeping their order, then turns each name into a verified extension pin and returns all pins as a tuple.

**Call relations**: The main `build` method calls this before writing the lockfile. This function relies on the extension loader to read the current environment and existing lockfile, and on `pin_for` to convert each extension name into a concrete pin that can later be checked at startup.

*Call graph*: called by 1 (build); 4 external calls (discovered, lockfile_path, read_lockfile, pin_for).


##### `Bundle._dockerfile`  (lines 80–94)

```
def _dockerfile(self) -> str
```

**Purpose**: This function writes the text of the Dockerfile used to build the runnable UFO container image. The Dockerfile installs UFO from the bundled wheel and tells the container where to find the frozen config and lockfile.

**Data flow**: It builds a list of Dockerfile lines: start from a Python base image, set `/app` as the working folder, set environment variables pointing to the bundled config and lockfile, copy in the UFO wheel, install it with `pip`, copy the config and lockfile into the image, and set the default command to run `ufoctl serve`. It joins those lines into one text string and returns it.

**Call relations**: The `build` method calls this when it is ready to write the Dockerfile to disk. This function calls `wheel_name` so the Dockerfile copies and installs the wheel file whose name matches the current UFO version.

*Call graph*: calls 1 internal fn (wheel_name); called by 1 (build).


### `core/src/ufo/onboarding.py`

`orchestration` · `first-run init`

This file is the “new office setup” checklist for the system. On a cold start, there is no workspace, no user, and no main assistant agent yet. Without this file, the system would not know how to safely create those first durable records, and extensions would not get a chance to prepare anything they need for the new workspace.

The main class, `Onboarding`, is used by the init flow. It first checks that the selected AI model has the environment variable it needs for its API key, if the platform can know that key name up front. It also checks that extension onboarding can safely store secrets if any installed extension has setup steps that require credentials.

Then it opens a database transaction, which means the core database changes are treated as one all-or-nothing operation. It refuses to run if a member already exists, so running init twice does not accidentally create a second “first” workspace. If the database is empty, it creates a workspace, an initial admin member, and the main agent with a default prompt.

After the core workspace exists, it provisions agents contributed by extensions and runs each extension’s onboarding steps inside that workspace. Extension failures are logged and skipped, so one broken add-on cannot ruin the base setup.

#### Function details

##### `run_onboarding_steps`  (lines 47–75)

```
async def run_onboarding_steps(manifests: tuple[Manifest, ...], workspace_id: UUID, credentials: CredentialStore | None) -> None
```

**Purpose**: Runs the setup steps supplied by installed extensions for a newly created workspace. It gives each extension its own scoped context, so the extension sees only the credential slots it declared.

**Data flow**: It receives the installed extension manifests, the new workspace ID, and an optional credential store. It enters the workspace context, walks through each extension, builds a context for extensions that have onboarding steps, and calls each step. If there is no credential store, it logs that the extension’s steps were skipped. If a step fails, it logs the failure and continues with the next step instead of stopping the whole onboarding process.

**Call relations**: `Onboarding.run_steps` calls this after the core workspace is already created and extension agents have been provisioned. Inside the flow, it uses `ws` to make the workspace current, `context_for` to create the extension-specific handle, and `log` to record skipped or failed extension work.

*Call graph*: called by 1 (run_steps); 3 external calls (context_for, log, ws).


##### `Onboarding.run`  (lines 90–93)

```
async def run(self) -> Onboarded
```

**Purpose**: Runs the full first-time setup from start to finish. It creates the core workspace records first, then runs extension-related setup.

**Data flow**: It starts with the `Onboarding` object’s stored configuration, email address, model name, credentials, and manifests. It calls `create` to produce an `Onboarded` result containing the new workspace and member IDs, then passes that result into `run_steps`. It returns the same `Onboarded` result so the caller can bind later actions to the new workspace and admin member.

**Call relations**: This is the top-level method for this file’s flow. It delegates the core creation work to `Onboarding.create`, then delegates the add-on setup work to `Onboarding.run_steps`.

*Call graph*: calls 2 internal fn (create, run_steps).


##### `Onboarding.create`  (lines 95–101)

```
async def create(self) -> Onboarded
```

**Purpose**: Creates the core, durable parts of a new installation: the workspace, the first admin member, and the main agent. It performs safety checks first so setup does not leave behind a half-created workspace.

**Data flow**: It reads the configured model, credentials, extension manifests, and environment settings through the `Onboarding` object. First it checks for the model key, then checks whether extension setup requires a credential key. If those checks pass, it calls `_create_workspace`, which writes the core records to the database and returns the new workspace and member IDs.

**Call relations**: `Onboarding.run` calls this as the first major phase. This method coordinates three smaller steps: `_require_model_key`, `_require_credentials_for_steps`, and `_create_workspace`.

*Call graph*: calls 3 internal fn (_create_workspace, _require_credentials_for_steps, _require_model_key); called by 1 (run).


##### `Onboarding.run_steps`  (lines 103–105)

```
async def run_steps(self, onboarded: Onboarded) -> None
```

**Purpose**: Runs the post-creation setup that depends on an existing workspace. This includes extension-provided agent provisioning and extension onboarding steps.

**Data flow**: It receives an `Onboarded` value containing the new workspace ID. It uses the installed manifests to apply `AgentProvisioning` to that workspace, then passes the manifests, workspace ID, and credential store into `run_onboarding_steps`. It does not return a new value; its effect is to prepare extension-related parts of the workspace.

**Call relations**: `Onboarding.run` calls this only after `Onboarding.create` succeeds. It hands off extension agent setup to `AgentProvisioning`, then hands extension onboarding step execution to `run_onboarding_steps`.

*Call graph*: calls 1 internal fn (run_onboarding_steps); called by 1 (run); 1 external calls (__init__).


##### `Onboarding._require_credentials_for_steps`  (lines 107–118)

```
def _require_credentials_for_steps(self) -> None
```

**Purpose**: Checks whether extension onboarding steps need a credential key before database records are created. This prevents setup from creating a workspace and only then discovering it cannot safely run required extension setup.

**Data flow**: It reads the current credential store and the installed manifests from the `Onboarding` object. If a credential store exists, it does nothing. If any extension has onboarding steps but no credential store is available, it raises an error that names the needed environment variable.

**Call relations**: `Onboarding.create` calls this before `_create_workspace`. It is one of the early guard checks that protects the first-run flow from leaving behind a partial installation.

*Call graph*: called by 1 (create).


##### `Onboarding._require_model_key`  (lines 120–127)

```
def _require_model_key(self) -> None
```

**Purpose**: Checks that the selected AI model has its required environment variable set, when the system can know that variable name during init. This avoids creating a workspace that cannot run its first assistant turn because the model key is missing.

**Data flow**: It asks `_model_key_env` for the environment variable name required by the selected model. If there is no known variable name, it allows setup to continue. If there is a known variable name but the environment does not contain a value for it, it raises an error explaining what must be set.

**Call relations**: `Onboarding.create` calls this before any database work. It relies on `_model_key_env` to discover which environment variable, if any, should be checked.

*Call graph*: calls 1 internal fn (_model_key_env); called by 1 (create).


##### `Onboarding._model_key_env`  (lines 129–132)

```
def _model_key_env(self) -> str | None
```

**Purpose**: Finds the name of the environment variable that should contain the API key for the selected model, if the core model registry can identify one. Some extension-provided models resolve their keys later, so this may return no name.

**Data flow**: It reads the current config, installed manifests, and chosen model from the `Onboarding` object. It builds or queries the model registry, then asks it for the key environment variable for that model. The result is either a string environment variable name or `None`.

**Call relations**: `Onboarding._require_model_key` calls this as its lookup step. This function hands the model-key decision to `model_registry`, which knows about core and extension-contributed model providers.

*Call graph*: called by 1 (_require_model_key); 1 external calls (model_registry).


##### `Onboarding._create_workspace`  (lines 134–158)

```
async def _create_workspace(self) -> Onboarded
```

**Purpose**: Writes the first workspace, first admin member, and main agent into the database. It also prevents duplicate initialization by refusing to run if any member already exists.

**Data flow**: It opens a workspace database transaction. First it checks the member table for an existing email. If one is found, it raises `AlreadyInitialized`. Otherwise it creates new IDs, inserts a workspace row, calls `create_member` to add the initial admin, inserts the main agent row with the default prompt and selected model, and returns an `Onboarded` value containing the new workspace and member IDs.

**Call relations**: `Onboarding.create` calls this after all preflight checks pass. It uses `workspace_tx` for the database transaction, SQLAlchemy helpers to select and insert records, `create_member` to create the admin member consistently, `uuid4` to make new IDs, and `Onboarded` to package the result for later steps.

*Call graph*: called by 1 (create); 7 external calls (__init__, __init__, insert, select, workspace_tx, create_member, uuid4).


### Hosted control commands
Hosted-control entrypoints run gateway and maintenance tasks for the control service.

### `control/src/ufo_control/main.py`

`entrypoint` · `startup and operator maintenance`

This file defines the operational commands a human or deployment script uses to run the hosted shared-workspace service. Think of it like the service desk for the control system: one command opens the public counter, while other commands do behind-the-scenes administration.

When the command-line tool starts, it sets up normal logging and can optionally send logs to an OTLP collector, which is a standard service that gathers logs from running systems. The `gateway` command starts the web application through Uvicorn, the Python web server used here.

The other commands are maintenance tools. `migrate` shapes the control database schema so the tables and ledgers the gateway expects are present. `invite` grants a work email address or waitlist object a new workspace invitation, then emails the invite. It carefully checks email setup before spending a one-time grant, so a misconfigured deployment does not accidentally consume an approval. `slack-connect-retry` re-arms a previously failed Slack Connect delivery after an operator fixes the cause. `rls-bootstrap` installs database roles and row-level security policies, which are database rules that limit which rows an app role can see or change.

Most commands are small wrappers around asynchronous work: the visible command validates inputs and prints a result, while a helper opens the database, performs the real operation, and closes the connection cleanly.

#### Function details

##### `main`  (lines 42–45)

```
def main() -> None
```

**Purpose**: This is the root command group for the control service command-line tool. It prepares logging before any subcommand runs, so operators get useful output whether they start the gateway or run a maintenance task.

**Data flow**: It reads the optional log-export endpoint from the environment, sets a standard log format and level, then passes that endpoint into the log export setup. It does not return business data; it prepares the process for the command that Click will run next.

**Call relations**: Click uses this as the parent command for the file's subcommands. During that startup, it calls `_export_logs` so logging is ready before commands such as `gateway`, `migrate`, or `invite` do their work.

*Call graph*: calls 1 internal fn (_export_logs); 1 external calls (basicConfig).


##### `_export_logs`  (lines 48–58)

```
def _export_logs(otlp_endpoint: str | None) -> None
```

**Purpose**: This optionally connects the program's logs to the platform's OpenTelemetry log collector. If no endpoint is configured, it leaves logging on normal console output only.

**Data flow**: It receives either a collector base URL or nothing. With no URL, it stops immediately. With a URL, it builds an OpenTelemetry logger provider, points it at the collector's logs path, and installs it into the root logging system.

**Call relations**: `main` calls this once at command startup. When exporting is enabled, it hands the configured logger provider to `_install_root_handler`, which attaches the bridge from ordinary Python logging to OpenTelemetry.

*Call graph*: calls 1 internal fn (_install_root_handler); called by 1 (main); 4 external calls (OTLPLogExporter, LoggerProvider, BatchLogRecordProcessor, create).


##### `_install_root_handler`  (lines 61–66)

```
def _install_root_handler(logger_provider: LoggerProvider) -> None
```

**Purpose**: This attaches the OpenTelemetry logging bridge to Python's root logger, so ordinary log messages can be exported. It deliberately ignores logs from OpenTelemetry itself to avoid a feedback loop if log exporting fails.

**Data flow**: It receives a prepared OpenTelemetry logger provider. It creates a logging handler from it, adds a filter that rejects records whose logger name starts with `opentelemetry`, and adds the handler to the root logger.

**Call relations**: `_export_logs` calls this after it has built the exporter. From then on, log records from the rest of the process can flow through the added handler to the collector.

*Call graph*: called by 1 (_export_logs); 2 external calls (getLogger, LoggingHandler).


##### `gateway`  (lines 70–75)

```
def gateway() -> None
```

**Purpose**: This starts the hosted web gateway that serves onboarding, fleet counts, and the terminal client. It is the command used when this service should actually listen for web traffic.

**Data flow**: It reads the gateway port from the environment, falling back to the default port if none is set. It then starts Uvicorn with the `ufo_control.gateway:app` web app, binding to all network interfaces.

**Call relations**: This is a Click subcommand under `main`. It hands control to Uvicorn, which runs the web application until the process is stopped.

*Call graph*: 1 external calls (run).


##### `migrate`  (lines 79–82)

```
def migrate() -> None
```

**Purpose**: This brings the control database schema up to the shape the service expects. Operators use it when deploying or upgrading so the gateway has the tables and database structures it needs.

**Data flow**: It gets the database owner connection string, runs the asynchronous schema-shaping routine, then prints a short success message. The main change is in the database, not in the Python process.

**Call relations**: This is a Click subcommand under `main`. It uses `asyncio.run` to call the async schema function from `ufo_control.schema`, using the owner database connection from `ufo_control.rls`.

*Call graph*: 4 external calls (run, echo, owner_dsn, shape_control_schema).


##### `invite`  (lines 95–114)

```
def invite(email: str, business: str | None, goals: str | None, object_number: int | None) -> None
```

**Purpose**: This grants a new workspace invitation to an email address, optionally tied to a waitlist object and signup answers. It is an operator-facing command for approving someone and sending them the invite email.

**Data flow**: It receives command-line values: the email address, optional business and goals text, and an optional waitlist object number. It rejects cases where only one of business or goals is provided, builds a signup profile when both are present, asks `_mint_invite` to create and email the invitation, and prints who was approved and when the invite expires.

**Call relations**: This Click subcommand does the human-facing validation and messaging. It calls `_mint_invite` for the database and email work, catches invite or work-email errors, and turns them into clear command-line failures.

*Call graph*: calls 1 internal fn (_mint_invite); 4 external calls (__init__, run, ClickException, echo).


##### `_mint_invite`  (lines 117–141)

```
async def _mint_invite(object_number: int | None, email: str, profile: SignupProfile | None) -> MintedInvite
```

**Purpose**: This creates the actual invitation grant and sends the invitation email. It is careful about ordering: it checks that mail sending can be configured before it spends a live grant in the database.

**Data flow**: It receives an optional waitlist object number, an email address, and an optional signup profile. It reads the public host and database connection string, verifies the control schema exists, builds the email sender, opens a small database pool, mints the invite, closes the pool, builds the email subject and body, and sends the message. It returns the minted invite record; if sending fails after the grant exists, it reports that the grant still stands.

**Call relations**: `invite` calls this after checking command-line inputs. This helper coordinates other subsystems: schema checks, database invitation minting through `InviteCodes`, email text creation through `invite_email`, and delivery through the configured sender.

*Call graph*: called by 1 (invite); 8 external calls (__init__, create_pool, ClickException, email_sender_from_env, invite_email, public_apex_host, owner_dsn, require_control_schema).


##### `slack_connect_retry`  (lines 146–153)

```
def slack_connect_retry(email_domain: str) -> None
```

**Purpose**: This lets an operator retry one failed Slack Connect delivery for a signup domain after the underlying problem has been fixed. It prevents blind retries by only re-arming a delivery that is actually recorded as failed.

**Data flow**: It receives an email domain from the command line, trims spaces, lowercases it, and asks `_rearm_slack_connect` to re-arm the failed delivery. If none is found, it raises a command-line error. If one is found, it prints the time since the delivery had been failed.

**Call relations**: This Click subcommand is the operator-facing wrapper. It calls `_rearm_slack_connect` for the database update and turns the result into either a helpful error or a success message.

*Call graph*: calls 1 internal fn (_rearm_slack_connect); 3 external calls (run, ClickException, echo).


##### `_rearm_slack_connect`  (lines 156–163)

```
async def _rearm_slack_connect(email_domain: str) -> datetime | None
```

**Purpose**: This performs the database work needed to make one failed Slack Connect delivery eligible to run again. It returns when the failure started so the operator can see what was retried.

**Data flow**: It receives a normalized email domain. It gets the database owner connection string, checks that the control schema exists, opens a small database pool, calls the Slack Connect retry routine, closes the pool, and returns either the original failure time or nothing if there was no failed delivery.

**Call relations**: `slack_connect_retry` calls this from the synchronous command line. This helper connects the command to the Slack Connect domain logic in `ufo_control.gateway_slack_connect` and ensures the database pool is closed afterward.

*Call graph*: called by 1 (slack_connect_retry); 4 external calls (create_pool, rearm_failed_delivery, owner_dsn, require_control_schema).


##### `rls_bootstrap`  (lines 167–170)

```
def rls_bootstrap() -> None
```

**Purpose**: This installs or updates the shared database role and row-level security policies. Row-level security means the database itself enforces which workspace rows an application role may access.

**Data flow**: It takes no command-line data. It runs `_bootstrap`, waits for it to finish, and prints a success message once the policies and role are in place.

**Call relations**: This Click subcommand is the human-facing entry point for database security bootstrapping. It delegates the actual asynchronous work to `_bootstrap`.

*Call graph*: calls 1 internal fn (_bootstrap); 2 external calls (run, echo).


##### `_bootstrap`  (lines 173–176)

```
async def _bootstrap() -> None
```

**Purpose**: This applies the database security setup used by the hosted service. It creates or updates workspace policies and ensures the serving role exists.

**Data flow**: It reads the owner database connection string, applies the row-level security policies, then ensures the serve role is present. Its output is the changed database state rather than a returned value.

**Call relations**: `rls_bootstrap` calls this through `asyncio.run`. It hands the owner database connection string to `bootstrap_policies` and `ensure_serve_role`, which perform the actual database changes.

*Call graph*: called by 1 (rls_bootstrap); 3 external calls (bootstrap_policies, ensure_serve_role, owner_dsn).


### Service runtime launch
The shared UFO service process assembles web serving, workers, sandbox support, extensions, and safety checks.

### `core/src/ufo/serve.py`

`entrypoint` · `startup, request handling, background work, shutdown`

Think of this file as the control room that turns many separate parts into one running service. UFO serves many workspaces from the same process, so a central problem is keeping each request and background task tied to the right workspace. Without that boundary, one workspace could accidentally read or write another workspace’s data.

At startup, `run` loads configuration, opens databases, checks required secrets, loads extensions, creates shared services such as blob storage, model access, search, memory, connectors, sandboxes, and the live update hub, then registers durable background jobs. It also starts a heartbeat so other workers know this process is alive.

The file mounts two kinds of web routes. Extension routes live under `/ext/...` and must identify a workspace before running. Surface routes live under `/surface/...` and are the member-facing entry points. A middleware called `WorkspaceScopeBoundary` clears workspace state before and after each HTTP request, like wiping a whiteboard between customers.

The file also chooses optional backends registered by extensions, such as browser control, search, terminal transport, authentication proxy, and memory search. It fails early if configuration is ambiguous or required pieces are missing. During shutdown, it carefully drains work before retiring this worker’s “seat,” avoiding duplicate execution by another worker.

#### Function details

##### `_assert_no_reserved_routes`  (lines 172–188)

```
def _assert_no_reserved_routes(app: FastAPI) -> None
```

**Purpose**: Checks that this service has not mounted routes under URL prefixes reserved for the separate onboarding and login gateway. This prevents confusing routing where the gateway silently takes traffic that this app thought it owned.

**Data flow**: It receives the FastAPI app, reads its registered routes, looks for paths beginning with reserved prefixes, and raises an error if any are found. If there are no conflicts, it returns nothing and startup continues.

**Call relations**: Near the end of `run`, after all routers and surfaces have been mounted, this function performs a final safety check before the server starts accepting traffic.

*Call graph*: called by 1 (run).


##### `run`  (lines 191–362)

```
def run() -> None
```

**Purpose**: Starts the shared UFO fleet process. It builds all major services, registers background jobs, mounts web routes, launches the DBOS worker system, and finally runs the Uvicorn web server.

**Data flow**: It begins with configuration and environment variables, then creates database connections, credentials, storage, extension-provided backends, sandbox support, model registries, job runners, and a FastAPI app. The output is a running process that serves HTTP requests and durable background workflows until shutdown, when it drains and retires safely.

**Call relations**: This is the top-level entry point. It calls the selection, validation, mounting, proxy, connector, job, and shutdown helpers in this file, so the rest of the file exists mostly to keep `run` readable and to make each startup decision explicit.

*Call graph*: calls 18 internal fn (from_env, _assert_no_reserved_routes, _connect_flow, _connector_registry, _launch_jobs, _mount_ext_routes, _mount_shared_surfaces, _one_shot, _proxy_endpoint, _select_cdp_provider (+8 more)); 50 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


##### `run.invoker_for`  (lines 233–234)

```
def invoker_for(workspace_id: UUID) -> AdmissionInvoker
```

**Purpose**: Creates an admission helper bound to one workspace. It is used when a job or request needs to admit work for a specific workspace without mixing it with others.

**Data flow**: It receives a workspace ID, combines it with the shared `Admission` object, and returns an `AdmissionInvoker` that knows which workspace it belongs to.

**Call relations**: This small closure is created inside `run` and passed into runtime and job setup so later code can ask for a workspace-specific invoker whenever work is admitted.

*Call graph*: 1 external calls (__init__).


##### `_one_shot`  (lines 365–377)

```
def _one_shot(coro: Coroutine[Any, Any, T]) -> T
```

**Purpose**: Runs one asynchronous setup or teardown operation on a temporary event loop and then cleans up database engines tied to that loop. This avoids leaving pooled database connections attached to a loop that has already closed.

**Data flow**: It receives a coroutine, wraps it in an inner cleanup step, runs that step with `asyncio.run`, and returns the coroutine’s result. As a side effect, it disposes database engines used by that temporary loop.

**Call relations**: `run` uses this for startup database checks and seat recording. `_stop_executor` uses it during shutdown to retire the heartbeat seat safely.

*Call graph*: called by 2 (_stop_executor, run); 1 external calls (run).


##### `_one_shot.step`  (lines 371–375)

```
async def step() -> T
```

**Purpose**: Performs the actual awaited work for `_one_shot` and guarantees cleanup afterward. It is the protective wrapper around the one-time asynchronous operation.

**Data flow**: It awaits the coroutine passed into `_one_shot`, returns its result if successful, and always calls database engine disposal before the temporary loop closes.

**Call relations**: This nested function is only used by `_one_shot`; it is the reason `_one_shot` can safely run database-touching startup and shutdown tasks outside the long-lived server loop.

*Call graph*: 1 external calls (dispose_loop_engines).


##### `_stop_executor`  (lines 380–394)

```
def _stop_executor(dbos: DBOS, heartbeat: Heartbeat, graceful_shutdown_seconds: int) -> None
```

**Purpose**: Shuts down DBOS workflow execution without causing duplicate work. It only retires this process’s worker seat if there are no active workflows still running.

**Data flow**: It receives the DBOS object, heartbeat, and drain timeout. It asks DBOS to stop after a graceful wait, checks whether active workflows remain, logs and keeps the seat if work is still alive, or retires the heartbeat seat if the executor is empty.

**Call relations**: `run` calls this in its `finally` block after Uvicorn exits. It hands off to `_one_shot` for the asynchronous heartbeat retirement step.

*Call graph*: calls 2 internal fn (retire, _one_shot); called by 1 (run); 2 external calls (destroy, log).


##### `_shared_owner_dsn`  (lines 397–411)

```
def _shared_owner_dsn(config: Config) -> str
```

**Purpose**: Finds the database connection string used for cross-workspace owner-level reads. This is needed for background sweeps that first list work across all workspaces and then re-enter each workspace safely.

**Data flow**: It reads the owner database URL from an environment variable or configuration. If none is set, it raises an error; otherwise it returns the chosen database connection string.

**Call relations**: `run` calls this before initializing the owner database connection, because shared fleet mode cannot safely perform cross-workspace sweeps without it.

*Call graph*: called by 1 (run).


##### `_launch_jobs`  (lines 414–472)

```
def _launch_jobs(runtime: Runtime, invoker_for: InvokerFactory, sync_driver: SyncDriver, page_feed: CorePageFeed) -> None
```

**Purpose**: Registers and starts durable background jobs, including source sync, turn dispatch, page indexing, and delivery cleanup. These jobs keep queued work, synced data, and delegated subagent results moving even when no web request is active.

**Data flow**: It receives the runtime, workspace invoker factory, sync driver, and page feed. It builds probe tools, job runners, and job bindings from core and extension job definitions, then launches the job runner as DBOS schedules and one-shot enqueues.

**Call relations**: `run` calls this after runtime setup and before the web server starts. It connects runtime services such as sandboxes, blob storage, models, index, and embed backends to the job system.

*Call graph*: called by 1 (run); 12 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, connector_clis, injecting_slots (+2 more)).


##### `_source_backends`  (lines 475–489)

```
def _source_backends(manifests: tuple[Manifest, ...]) -> dict[str, SourceBackend]
```

**Purpose**: Builds the list of source-sync backends that know how to read external or local sources. It always includes the built-in folder source and adds extension-provided sources.

**Data flow**: It receives extension manifests, starts with the core folder backend, then asks each extension source provider to build a backend using only that extension’s declared credentials. It returns a mapping from backend name to implementation, or raises if two extensions claim the same name.

**Call relations**: `run` uses this while creating the `SyncDriver`, so feed-sync jobs can resolve each source row to exactly one backend.

*Call graph*: called by 1 (run); 2 external calls (__init__, __init__).


##### `_source_identity_resolvers`  (lines 492–533)

```
def _source_identity_resolvers(manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: WorkspaceBlobStore) -> dict[str, SourceIdentityResolver]
```

**Purpose**: Builds helpers that can ask a surface who the current user is for source-sync identity matching. This lets synced data be tied to the right member identity.

**Data flow**: It receives manifests, the credential store, and workspace blob storage. For each surface that declares a self-user lookup, it creates an async resolver and returns them by surface name, rejecting duplicate surface names.

**Call relations**: `run` passes these resolvers into the `SyncDriver`. The nested `resolve` functions are later used when source syncing needs a surface-specific user identity.

*Call graph*: called by 1 (run).


##### `_source_identity_resolvers.resolve`  (lines 506–530)

```
async def resolve(workspace_id: UUID, handler=surface.self_user_id, slots=declared, store=credentials) -> str | None
```

**Purpose**: Runs one surface’s self-user identity lookup inside the correct workspace. It gives the surface a safe context with blob storage and credential access.

**Data flow**: It receives a workspace ID. It creates a credential-reading helper limited to the extension’s declared slots, enters the workspace scope, calls the surface’s identity handler, and returns the user ID or `None`.

**Call relations**: This resolver is created by `_source_identity_resolvers` and later used by source-sync code through the resolver map supplied by `run`.

*Call graph*: 2 external calls (__init__, ws).


##### `_source_identity_resolvers.resolve.credential`  (lines 512–521)

```
async def credential(credential_slot: str) -> str
```

**Purpose**: Reads one credential for a surface identity lookup, while enforcing that the surface declared permission to use that credential slot.

**Data flow**: It receives a credential slot name, checks that the slot was declared, checks that a credential store exists, then reads and returns the credential value for the current workspace.

**Call relations**: This nested helper is handed to the surface identity handler through `SurfaceIdentityContext`, so extension code can request credentials without bypassing declaration checks.


##### `_select_hub`  (lines 536–554)

```
def _select_hub(config: Config, manifests: tuple[Manifest, ...]) -> Hub
```

**Purpose**: Chooses the live-update hub backend for this process. The hub is the place where running turns and connected clients exchange live frames or updates.

**Data flow**: It reads the configured hub backend, combines the built-in in-process option with extension-registered hub builders, rejects duplicate names or unknown selections, and returns the built hub.

**Call relations**: `run` calls this during startup so surfaces, runtime, and hub tailing can all share the same hub implementation.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_terminal_transport`  (lines 557–599)

```
def _select_terminal_transport(config: Config, manifests: tuple[Manifest, ...], blob: FleetBlobStore) -> TerminalTransport
```

**Purpose**: Chooses how terminal sessions connect between a member’s browser and a running sandbox. It prevents unsafe setups where a multi-process deployment tries to use process-local terminal state.

**Data flow**: It reads terminal and hub configuration, checks for an invalid in-process terminal with cross-process hub combination, adds built-in and extension terminal builders, rejects duplicates or unknown backends, and returns the selected transport.

**Call relations**: `run` calls this while constructing sandbox support. The chosen transport is passed into `ConversationSandbox` so terminal input and output can meet even across pods when needed.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_cdp_provider`  (lines 602–630)

```
def _select_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> CdpProvider | None
```

**Purpose**: Chooses the browser-control provider, if one is installed and selected. CDP means Chrome DevTools Protocol, a way for software to drive a browser.

**Data flow**: It reads provider specs from manifests, checks for duplicate backend names, looks up the configured provider, validates credential-key availability when needed, and returns a built provider or `None`.

**Call relations**: `run` uses it to attach browser capability to the runtime. `_require_cdp_provider` also calls it during extension requirement validation.

*Call graph*: called by 2 (_require_cdp_provider, run); 1 external calls (__init__).


##### `_validate_requires`  (lines 633–657)

```
def _validate_requires(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Checks that every active extension’s declared required seams are actually available. A seam is a plug-in point such as browser control, search, or memory search.

**Data flow**: It reads each manifest’s `requires` list, looks up the matching checker, and runs it. If a seam is unknown or unavailable, it raises an error that names the extension and missing capability.

**Call relations**: `run` calls this early, before building the full runtime, so misconfigured extensions fail at boot instead of failing during a user action.

*Call graph*: called by 1 (run).


##### `_require_cdp_provider`  (lines 660–674)

```
def _require_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that a usable browser-control provider exists when an extension says it needs one.

**Data flow**: It receives config, manifests, and credentials, calls `_select_cdp_provider`, and raises if the result is `None`.

**Call relations**: `_validate_requires` calls this when an extension requires the `cdp_providers` seam.

*Call graph*: calls 1 internal fn (_select_cdp_provider).


##### `_select_search_provider`  (lines 677–712)

```
def _select_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> SearchProvider | None
```

**Purpose**: Chooses the research search backend, if configured. This is the service used by research tools to search outside stored workspace memory.

**Data flow**: It reads search provider specs from manifests, rejects duplicate names, returns `None` if no provider is configured, or builds the configured provider after checking registration and credential-key availability.

**Call relations**: `run` uses this to attach search capability to the runtime. `_require_search_provider` calls it when an extension requires search.

*Call graph*: called by 2 (_require_search_provider, run); 2 external calls (__init__, __init__).


##### `_require_search_provider`  (lines 715–728)

```
def _require_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that research search is configured and usable when an extension requires it.

**Data flow**: It checks that the search provider setting is present, then delegates to `_select_search_provider` to verify the named backend can be built.

**Call relations**: `_validate_requires` calls this for extensions that declare the `search_providers` seam.

*Call graph*: calls 1 internal fn (_select_search_provider).


##### `_require_memory_search`  (lines 731–757)

```
def _require_memory_search(_config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that exactly one default memory-search provider is available when an extension requires memory search.

**Data flow**: It scans manifests for the default memory-search provider name, raises if none or more than one are found, and checks that credentials are configured if the provider declares credential slots.

**Call relations**: `_validate_requires` calls this for extensions that declare the `memory_search` seam.


##### `_select_auth_proxy`  (lines 769–806)

```
def _select_auth_proxy(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> AuthProxy | None
```

**Purpose**: Chooses the fallback authentication proxy used by connector sync when a connector does not bring its own broker. This lets feed-sync code obtain provider credentials in a controlled way.

**Data flow**: It gathers auth-proxy specs from manifests, handles automatic selection when only one exists, rejects ambiguity, unknown names, duplicate names, or missing credential keys, and returns the built proxy or `None`.

**Call relations**: `_connector_registry` calls this while building connector routing, so connectors have a single fallback path for unbrokered authentication.

*Call graph*: called by 1 (_connector_registry); 2 external calls (__init__, __init__).


##### `_mount_ext_routes`  (lines 809–847)

```
def _mount_ext_routes(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, index: IndexBackend, embed: EmbedClient) -> None
```

**Purpose**: Mounts extension-defined HTTP routes under `/ext/<extension>/...`. Each request must first identify its workspace before the extension handler can run.

**Data flow**: It receives the FastAPI app, manifests, credentials, index, and embed client. For each extension route, it builds an extension context and adds a FastAPI route whose endpoint checks identity, enters the workspace, then calls the extension handler.

**Call relations**: `run` calls this after job setup and before shared surfaces are mounted. The nested endpoint function is what actually runs for each extension request.

*Call graph*: called by 1 (run); 2 external calls (add_route, context_for).


##### `_mount_ext_routes.endpoint`  (lines 831–841)

```
async def endpoint(request: Request, handler=spec.handler, identify=spec.identify, extension_context=context) -> Response
```

**Purpose**: Serves one mounted extension route request after proving which workspace the request belongs to.

**Data flow**: It receives a request, asks the route’s identify function for a workspace, returns a 401 response if unresolved, otherwise enters that workspace and calls the extension route handler with its context.

**Call relations**: This endpoint is registered by `_mount_ext_routes` through FastAPI. It protects extension code from running without a workspace boundary.

*Call graph*: 2 external calls (Response, ws).


##### `WorkspaceScopeBoundary.__call__`  (lines 868–876)

```
async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None
```

**Purpose**: Clears workspace state at the start and end of every HTTP request. This prevents stale workspace identity from leaking between requests handled by the same process.

**Data flow**: It receives the ASGI request scope, receive function, and send function. Non-HTTP traffic passes through unchanged; HTTP traffic has `current_workspace` cleared before calling the downstream app and cleared again in a `finally` block after the response completes.

**Call relations**: `_mount_shared_surfaces` installs this as middleware. Surface endpoints set the workspace during a request, and this boundary guarantees cleanup even for streamed responses or errors.

*Call graph*: 1 external calls (set).


##### `_mount_shared_surfaces`  (lines 879–1042)

```
def _mount_shared_surfaces(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: WorkspaceBlobStore, sandboxes: ConversationSandbox, hub: Hub, dbos_client: DBOSClie
```

**Purpose**: Mounts member-facing shared surface routes under `/surface/...` and prepares supporting objects such as admission, stopping, live tailing, writeback, and mid-turn reply polling.

**Data flow**: It receives the app plus runtime services such as manifests, credentials, blob storage, sandboxes, hub, DBOS client, models, skills, memory, and objects. It installs workspace-cleaning middleware, builds deployment metadata and context factories, registers each surface route, mounts the home redirect, and creates pollers for durable surfaces when needed.

**Call relations**: `run` calls this once during startup. It calls `_mount_home`, uses `home_surface` through its nested context factory, and creates the nested route endpoint that handles each surface request.

*Call graph*: calls 2 internal fn (_mount_home, index); called by 1 (run); 21 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, add_middleware (+11 more)).


##### `_mount_shared_surfaces.context_for`  (lines 963–990)

```
def context_for(workspace_id: UUID, surface: str) -> SurfaceContext
```

**Purpose**: Builds the per-request `SurfaceContext` that gives a surface everything it is allowed to use for one workspace. This is the surface’s toolbox for admitting turns, reading blobs, accessing credentials, listing skills, and more.

**Data flow**: It receives a workspace ID and surface name, combines them with shared services and deployment metadata, creates a workspace-bound `MemberAdmission`, and returns a `SurfaceContext`.

**Call relations**: Surface route endpoints call this after identifying the workspace. Writeback and mid-turn reply pollers also use the same context factory so durable delivery runs with the same workspace rules.

*Call graph*: calls 1 internal fn (home_surface); 2 external calls (__init__, __init__).


##### `_mount_shared_surfaces.endpoint`  (lines 1006–1020)

```
async def endpoint(request: Request, handler=route.handler, identify=resolver, surface=spec.name, surface_auth=auth) -> Response
```

**Purpose**: Serves one mounted surface route request. It authenticates the request, binds the workspace, and hands the request to the surface’s handler.

**Data flow**: It receives a request, asks the surface identify function to resolve it, returns an explicit response or 401 when needed, sets `current_workspace` to the resolved workspace, creates a `SurfaceContext`, and returns the handler’s response.

**Call relations**: This endpoint is registered by `_mount_shared_surfaces` for each surface route. `WorkspaceScopeBoundary` later clears the workspace after the full response has finished.

*Call graph*: 2 external calls (Response, set).


##### `home_surface`  (lines 1045–1052)

```
def home_surface(manifests: tuple[Manifest, ...]) -> str | None
```

**Purpose**: Finds the single surface marked as the browser home page. It prevents a deployment from having two different surfaces both claiming to be the default front door.

**Data flow**: It scans all manifests for surfaces marked as home, raises if more than one exists, returns the one home surface name, or returns `None` if no browser home is installed.

**Call relations**: `_mount_home` uses this to decide whether `/` should redirect. `_mount_shared_surfaces.context_for` also uses it so surfaces know the deployment’s home surface.

*Call graph*: called by 2 (_mount_home, context_for).


##### `_mount_home`  (lines 1055–1067)

```
def _mount_home(app: FastAPI, manifests: tuple[Manifest, ...]) -> None
```

**Purpose**: Adds a simple `GET /` redirect to the configured home surface. This makes the bare host useful instead of returning a 404.

**Data flow**: It asks `home_surface` for the default surface. If there is none, it changes nothing; otherwise it registers a route that redirects browsers to `/surface/<home>`.

**Call relations**: `_mount_shared_surfaces` calls this after mounting all surface routes, so the redirect points at a route that already exists.

*Call graph*: calls 1 internal fn (home_surface); called by 1 (_mount_shared_surfaces); 1 external calls (add_route).


##### `_mount_home.home`  (lines 1064–1065)

```
async def home(_request: Request) -> Response
```

**Purpose**: Returns the actual redirect response for `GET /`.

**Data flow**: It ignores the request details and returns a 303 redirect to the chosen surface path.

**Call relations**: This nested handler is registered by `_mount_home` as the app’s root route when a home surface exists.

*Call graph*: 1 external calls (RedirectResponse).


##### `_serve_lifespan`  (lines 1071–1094)

```
async def _serve_lifespan(app: FastAPI) -> AsyncIterator[None]
```

**Purpose**: Runs background tasks tied to the FastAPI app’s lifetime. These tasks recover abandoned workflows, reconcile cancellations, and poll durable surfaces for writebacks or mid-turn replies.

**Data flow**: When the app starts, it creates an async task group and starts recovery, cancellation, and optional poller tasks. When the app shuts down, it cancels those tasks before leaving the lifespan context.

**Call relations**: `run` passes this function as FastAPI’s lifespan handler. It works alongside, but separate from, the heartbeat thread started in `run`.

*Call graph*: 3 external calls (__init__, __init__, TaskGroup).


##### `_proxy_endpoint`  (lines 1097–1125)

```
def _proxy_endpoint(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, pricing: Pricing, run_tokens: RunTokenCodec, blob: FilesystemBlobStore | S3BlobStore) -> Proxy
```

**Purpose**: Decides how sandboxes reach the network through an egress proxy. Egress means outbound traffic leaving the sandbox.

**Data flow**: It reads sandbox proxy configuration. If no public proxy URL is configured, it starts a local in-process proxy through `_local_egress_proxy`; otherwise it reads the shared proxy certificate from the environment and returns a `ProxyEndpoint` pointing at the external proxy.

**Call relations**: `run` calls this while building `ConversationSandbox`, so every sandbox receives the correct proxy address and trust certificate.

*Call graph*: calls 1 internal fn (_local_egress_proxy); called by 1 (run); 1 external calls (__init__).


##### `_local_egress_proxy`  (lines 1128–1186)

```
def _local_egress_proxy(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, pricing: Pricing, run_tokens: RunTokenCodec, blob: FilesystemBlobStore | S3BlobStore) -> P
```

**Purpose**: Starts an in-process sandbox egress proxy for local or single-node deployments. It runs on its own event loop so proxy traffic does not depend on the turn-processing loop.

**Data flow**: It creates a new asyncio event loop, starts it in a daemon thread, parses cache-daemon configuration, schedules the nested `_boot` coroutine on that loop, waits for startup, and returns the resulting proxy endpoint.

**Call relations**: `_proxy_endpoint` calls this when no external proxy URL is configured. The nested `_boot` function performs the actual async proxy setup.

*Call graph*: called by 1 (_proxy_endpoint); 4 external calls (new_event_loop, run_coroutine_threadsafe, Thread, parse_cache_daemon).


##### `_local_egress_proxy._boot`  (lines 1148–1184)

```
async def _boot() -> ProxyEndpoint
```

**Purpose**: Builds and starts the local egress proxy, including network rules, certificates, authorization checks, and optional cache credential callback support.

**Data flow**: It derives base allow rules, artifact rules, connector rules, credential slots, and cache settings, creates a temporary certificate authority, starts `EgressProxy`, optionally starts a credential callback service for the cache daemon, and returns the proxy endpoint.

**Call relations**: This coroutine is scheduled by `_local_egress_proxy` on the proxy’s private event loop. It hands rule resolution and live-turn authorization into the proxy server.

*Call graph*: 11 external calls (__init__, __init__, __init__, __init__, connector_clis, injecting_slots, model_rule_base, connector_transfer_hosts, derive_artifact_store_rules, derive_manifest_rules (+1 more)).


##### `_connector_registry`  (lines 1192–1215)

```
def _connector_registry(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> ConnectorRegistry
```

**Purpose**: Builds the registry that routes connector-related work by provider name. Connectors are integrations such as external services that may need OAuth grants or brokered credentials.

**Data flow**: It scans manifests for connector declarations, rejects duplicate provider names, creates registry entries, selects a fallback auth proxy, and returns a `ConnectorRegistry` with namespace resolution.

**Call relations**: `run` calls this during startup. The runtime and sync driver later use this registry to resolve connector tools and feed-sync credentials.

*Call graph*: calls 1 internal fn (_select_auth_proxy); called by 1 (run); 3 external calls (__init__, __init__, open_connector_namespace).


##### `_connect_flow`  (lines 1218–1249)

```
def _connect_flow(credentials: CredentialStore | None, config: Config, manifests: tuple[Manifest, ...], index: IndexBackend | None=None, embed: EmbedClient | None=None) -> ConnectFlow | None
```

**Purpose**: Creates the OAuth connection flow used to start and finish connector authorization. OAuth is the common browser-based permission handoff used by many external services.

**Data flow**: If no credential store exists, it returns `None`. Otherwise it gathers connector OAuth providers, rejects duplicates, builds the callback redirect URI, creates a grant store and connection hooks, and returns a `ConnectFlow`.

**Call relations**: `run` installs the result globally with `install_connect_flow`. This lets tools, private surface authorization, and OAuth callback routes share the same connection machinery.

*Call graph*: calls 1 internal fn (_connect_redirect_uri); called by 1 (run); 4 external calls (__init__, __init__, connection_hooks, open_connector_namespace).


##### `_connect_redirect_uri`  (lines 1252–1278)

```
def _connect_redirect_uri(config: Config, providers: Mapping[str, OAuthProvider]) -> str
```

**Purpose**: Builds and validates the public OAuth callback URL. This must be a real browser-openable URL because external providers redirect the member’s browser back to it.

**Data flow**: It reads `connect.public_base_url`, checks whether connectors are installed, validates scheme, host, and non-wildcard hostname when needed, and returns the base URL plus the callback path.

**Call relations**: `_connect_flow` calls this while constructing the provider registry and callback settings for connector authorization.

*Call graph*: called by 1 (_connect_flow); 1 external calls (urlparse).


### Sandbox deployment checks
Sandbox scripts build the runtime image and validate proxy connectivity before deployment.

### `sandbox/build_template.py`

`entrypoint` · `build and deploy time`

This file is the build recipe and command-line tool for UFO’s sandbox image. The sandbox is the controlled environment where tools run: Python scripts, Node packages, browser automation, PDF tools, office converters, GitHub CLI, and UFO’s own sandbox helper scripts. Without this file, the project could easily end up with mismatched E2B and Docker sandboxes, where a tool works in one place but is missing in another.

The core idea is “one packing list, two suitcases.” The file defines the packages, helper scripts, environment variables, startup command, and readiness check once. Then it applies that same set of layers either to an E2B base template or to a Docker base image. It also writes a build digest, which is like a fingerprint of the recipe and bundled files. Later, the script can boot a live published template and compare that fingerprint to the current source, catching stale builds.

Run normally, it builds and publishes E2B templates for each size tier, then boots each one and checks that required tools are really present. With `--check`, it only verifies that published templates match the current recipe. With `--dockerfile`, it prints the Dockerfile. With `--build-docker`, it builds the Docker image locally.

#### Function details

##### `template_name`  (lines 188–189)

```
def template_name(size: str) -> str
```

**Purpose**: Creates the published E2B template name for a sandbox size, such as small, medium, or large. This gives every size tier a predictable template name.

**Data flow**: It receives a size name as text, adds it to the shared base template name, and returns the combined name. It does not read or change outside state.

**Call relations**: The main build flow calls this whenever it needs to check, build, publish, or print the status of a size-specific E2B template.

*Call graph*: called by 1 (main).


##### `build_definition_digest`  (lines 192–230)

```
def build_definition_digest(sizing: Sizing | None) -> str
```

**Purpose**: Builds a fingerprint of everything that should affect the sandbox image. This fingerprint lets the project tell whether a published template was built from the same recipe and files that are currently in source control.

**Data flow**: It receives either a sandbox size setting or `None` for Docker. It reads the defined package lists, environment settings, users, startup and readiness commands, and the contents of bundled sandbox scripts and modules. It turns all of that into stable JSON, hashes it with SHA-256, and returns a string like a version stamp.

**Call relations**: The E2B build path calls it before applying layers so the digest can be baked into the template. The Dockerfile path does the same for Docker. The check path in `main` calls it again later and compares the result with the digest found inside the live E2B template.

*Call graph*: called by 3 (e2b_template, main, pod_dockerfile); 2 external calls (sha256, dumps).


##### `apply_layers`  (lines 233–265)

```
def apply_layers(builder: object, digest: str) -> object
```

**Purpose**: Applies the shared sandbox recipe to a template builder. This is the central place that installs system tools, Python and Node packages, UFO helper scripts, environment variables, permissions, and the startup command.

**Data flow**: It receives a builder object and a digest string. It adds commands and copy steps to the builder: install apt packages, remove sudo, install GitHub CLI, install Python and Node dependencies, install Playwright’s browser, write the digest file, copy UFO scripts and modules into the image, set permissions, set environment variables, and switch from root to the normal runtime user. It returns the updated builder.

**Call relations**: Both `e2b_template` and `pod_dockerfile` call this so E2B and Docker get the same image contents. It does not decide which target is being built; it only describes the common layers that both targets share.

*Call graph*: called by 2 (e2b_template, pod_dockerfile).


##### `e2b_template`  (lines 268–270)

```
def e2b_template(size: str) -> object
```

**Purpose**: Creates the build definition for one E2B sandbox template size. It starts from E2B’s base code-interpreter template and adds UFO’s shared sandbox layers.

**Data flow**: It receives a size name. It looks up that size’s CPU and memory setting, computes the matching build digest, creates an E2B template builder from the configured base template, applies the shared layers, and returns the finished build definition.

**Call relations**: The normal publish path in `main` calls this for each size tier before asking E2B to build the template. It delegates the fingerprint work to `build_definition_digest` and the actual image recipe to `apply_layers`.

*Call graph*: calls 2 internal fn (apply_layers, build_definition_digest); called by 1 (main); 1 external calls (Template).


##### `pod_dockerfile`  (lines 273–275)

```
def pod_dockerfile() -> str
```

**Purpose**: Produces the Dockerfile for the Docker version of the sandbox image. This lets local or Docker-based deployments use the same sandbox recipe as E2B without needing an E2B account.

**Data flow**: It starts a template builder from the configured Docker base image, computes a Docker-specific digest with no fixed sandbox size, applies the shared layers, and converts the result into Dockerfile text. The returned value is plain text that can be printed or passed to `docker build`.

**Call relations**: `main` calls this when the user asks for `--dockerfile`. `build_docker_image` calls it when it needs to feed the generated Dockerfile directly into Docker. Like the E2B path, it relies on `build_definition_digest` and `apply_layers`.

*Call graph*: calls 2 internal fn (apply_layers, build_definition_digest); called by 2 (build_docker_image, main); 2 external calls (Template, to_dockerfile).


##### `build_docker_image`  (lines 278–289)

```
def build_docker_image() -> None
```

**Purpose**: Builds the local Docker sandbox image from the generated Dockerfile. This is for deployments that use Docker instead of E2B.

**Data flow**: It asks `pod_dockerfile` for Dockerfile text, sends that text into `docker build` using the repository root as the build context, and tags the result with the configured image name. If Docker reports failure, it stops the script with an error; otherwise it prints the image tag.

**Call relations**: `main` calls this when the user passes `--build-docker`. It is the bridge between the shared image recipe and the local Docker daemon.

*Call graph*: calls 1 internal fn (pod_dockerfile); called by 1 (main); 1 external calls (run).


##### `verify_published_template`  (lines 292–307)

```
def verify_published_template(name: str) -> None
```

**Purpose**: Boots a freshly published E2B template and checks that the required tools are actually present. This prevents a broken image from being accepted just because the build command finished.

**Data flow**: It receives the name or reference of a published template, starts a short-lived E2B sandbox from it, runs the readiness command inside that sandbox, and then kills the sandbox. If the command fails or exits unsuccessfully, it raises an error saying the published template is missing required runtime tools.

**Call relations**: After `main` builds each E2B template, it calls this as a publish gate. The readiness command it runs is the same command baked into the template, so the check matches what the sandbox itself expects.

*Call graph*: called by 1 (main); 1 external calls (create).


##### `check_published_template`  (lines 310–330)

```
def check_published_template(name: str, expected: str) -> None
```

**Purpose**: Checks whether a live E2B template matches the current source recipe without publishing anything. This is useful in continuous integration, where the project wants to fail loudly if someone changed the recipe but did not republish the template.

**Data flow**: It receives a template name and the digest expected from the current source. It starts a sandbox from the live template, reads the baked digest file inside it, then shuts the sandbox down. If the digest file is missing or the value differs, it raises an error telling the user to republish.

**Call relations**: `main` calls this for each size tier when the user passes `--check`. The expected digest it compares against comes from `build_definition_digest`.

*Call graph*: called by 1 (main); 1 external calls (create).


##### `main`  (lines 333–374)

```
def main() -> None
```

**Purpose**: Provides the command-line behavior for this script. It decides whether to print a Dockerfile, build a Docker image, check published E2B templates, or build and verify new E2B templates.

**Data flow**: It reads command-line flags, chooses one path, and then calls the helper functions for that path. With `--dockerfile`, it writes Dockerfile text to standard output. With `--build-docker`, it builds the Docker image. With `--check`, it compares live E2B template digests against current source. With no flag, it builds every E2B size tier, verifies each published template, and prints the resulting template references.

**Call relations**: This is the top-level driver. It ties together naming, digest creation, shared layer application, Docker generation, E2B building, verification, and drift checking by calling the specialized functions at the right moment.

*Call graph*: calls 7 internal fn (build_definition_digest, build_docker_image, check_published_template, e2b_template, pod_dockerfile, template_name, verify_published_template); 2 external calls (ArgumentParser, build).


### `sandbox/proxy_gate.py`

`entrypoint` · `deployment validation`

This script is a “gate” in the sense of a checkpoint: it lets a deployment continue only if the sandbox’s TLS proxy path works. TLS is the security layer behind HTTPS, and a proxy is an intermediate server that outbound web requests pass through. If this check did not exist, the system could deploy a sandbox setup that looks fine from the outside but cannot actually make trusted HTTPS requests through its required egress route.

The file expects two important pieces of information. The proxy URL comes from the command line. The certificate and E2B template list come from environment variables. It chooses a sandbox template, creates a temporary sandbox, writes the certificate into it, and runs the certificate installation command as root.

Then it repeatedly runs a small curl probe inside the sandbox. Curl is a command-line web request tool. The probe tries to connect through the proxy to Anthropic’s API using a deliberately invalid run token. A successful proxy/TLS path should still reach the proxy and produce an HTTP CONNECT status of 403, meaning “the route worked, but the credentials are not allowed.” While the proxy is still starting, some curl failures are treated as temporary and retried. Any unexpected result fails the gate. The sandbox is always killed at the end, like cleaning up a temporary test room after an inspection.

#### Function details

##### `ProxyTlsGate.run`  (lines 47–111)

```
def run(self) -> None
```

**Purpose**: Runs the actual proxy health check inside a temporary E2B sandbox. It verifies that the given proxy URL is HTTPS, installs the needed certificate, probes the proxy until it succeeds or times out, and raises an error if the proxy route does not behave as expected.

**Data flow**: It starts with three stored values: the public proxy URL, the certificate text, and the sandbox template name. It checks and rewrites the proxy URL into a curl-friendly form using an intentionally invalid token, creates a sandbox from the template, writes the certificate into the sandbox, and runs the certificate installer. It then runs a curl command inside the sandbox and reads back the reported proxy CONNECT status plus curl’s exit code. If the status is the expected 403, it prints a success message and returns. If the status is still in a known “not ready yet” shape, it waits and tries again until the deadline. If the result is malformed, unexpected, or too late, it raises a clear failure message. No matter how it ends, it kills the temporary sandbox.

**Call relations**: This is called by main after command-line arguments and environment variables have been gathered. Inside the run, it relies on URL parsing to reject non-HTTPS proxy addresses, shell quoting to build a safe curl command, E2B sandbox creation to get a real test environment, and clock/sleep calls to retry while the proxy may still be coming up. It is the file’s core inspection step: main prepares the inputs, and this method performs the live test.

*Call graph*: 5 external calls (create, join, monotonic, sleep, urlsplit).


##### `main`  (lines 114–125)

```
def main() -> None
```

**Purpose**: Acts as the script’s command-line entry point. It collects the proxy URL and required environment settings, chooses the sandbox template to test with, and starts the gate check.

**Data flow**: It reads the --proxy-url argument from the command line. It then reads the certificate from the sandbox egress certificate environment variable and the available E2B template mapping from another environment variable. If either environment value is missing, it stops with an error because the test would be meaningless. It converts the template mapping into usable template names, picks the first configured sandbox size, builds a ProxyTlsGate object, and calls its run method. The output is either a completed check or an exception that explains why the gate failed.

**Call relations**: This function is invoked when the file is run as a script. It uses the argument parser to get user input and the sandbox template helper to choose the right E2B template, then hands everything to ProxyTlsGate.run. In other words, main is the front desk that checks the paperwork, while ProxyTlsGate.run performs the actual inspection.

*Call graph*: 3 external calls (__init__, ArgumentParser, sandbox_templates).

## 📊 State Registers Touched

- `reg-effective-config` — The merged settings that tell the whole system what is enabled, safe, and available in this run.
- `reg-extension-catalog` — The shared list of installed extensions and the capabilities they registered for this deployment.
- `reg-database-schema` — The durable database layout and migration version that every runtime component must agree on.
- `reg-workspace-roster` — The saved list of workspaces, members, admins, seats, and membership rules.
- `reg-agent-definitions` — The saved assistant agents for each workspace, including their settings, tools, model choices, and provisioning source.
- `reg-credential-vault` — The encrypted store of workspace secrets and API keys that tools and connectors can request through guarded paths.
- `reg-runtime-fleet` — The records of which runtime processes and workers are alive, what they own, and when they last checked in.
- `reg-sandbox-state` — The per-conversation sandbox handle, size, filesystem environment, command execution state, and cleanup state.
- `reg-network-egress-policy` — The shared network access rules and freshness counter that tell sandboxes and proxies where code may connect.
- `reg-ephemeral-cache-bus` — The selected Redis/cache/pub-sub backend and its ephemeral keys, locks, and connection state used to coordinate live delivery, workers, and shared runtime services.
- `reg-sandbox-runtime-image` — The prepared sandbox runtime image, build/cache metadata, and proxy validation state used before sandboxes can be launched safely.
- `reg-http-route-map` — The process-wide web application routing state, including mounted core routes, extension routes, middleware, static assets, and ingress handlers used to dispatch incoming requests.
