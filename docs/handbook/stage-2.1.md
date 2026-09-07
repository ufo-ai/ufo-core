# CLI, initialization, and product inspection commands  `stage-2.1`

This stage is the operator’s control panel for UFO. It runs outside the main HTTP service loop, so it is used before, after, or alongside the server rather than while handling web requests. Its job is to let a person set up a local workspace, start or run parts of the system, inspect what is installed, package things, and perform administration tasks.

The main piece here is `core/src/ufo/cli.py`. It defines `ufoctl`, the command-line tool, which is the front door for people operating the project from a terminal. A command-line tool is a program you run by typing commands, like asking a machine to perform a specific task. `ufoctl` turns those typed commands into concrete actions inside the UFO workspace. In practice, it acts like a dispatcher: it reads what the operator asked for, prepares the needed settings and context, then calls the right internal routines to initialize, inspect, run, or manage the deployment.

## Files in this stage

### CLI, initialization, and product inspection commands
### `core/src/ufo/cli.py`

`entrypoint` · `command invocation`

This file turns many internal UFO capabilities into clear terminal commands. Without it, a developer or operator would have to call low-level Python code directly to set up the database, start the server, open the browser portal, install extensions, set spending controls, inspect billing, store secrets, cancel stuck turns, or seed demo data.

The file is built around Click, a library for making command-line programs. The top-level `main` command loads local secrets from `.env`, then subcommands do the actual work. Many commands follow the same pattern: read `ufo.toml`, open the database, choose the right workspace, call a focused runtime service, print a human-readable result, and close database resources. It is like a control panel: the buttons are simple, but each button reaches into a different machine room.

A few areas are especially important. `init` writes a default config, creates development secrets, migrates the database, onboards the first owner, and saves a CLI token. `serve`, `portal`, and `ingress` start or connect to running services. Billing and spend commands expose safety limits and balances. Extension, credential, bundle, turn, and seed commands support day-to-day development and operations. The file also takes care to avoid unsafe behavior, such as refusing broad provider keys in `.env` and passing browser login tokens through a temporary local page instead of putting them in a URL.

#### Function details

##### `_ufoctl_dir`  (lines 130–132)

```
def _ufoctl_dir() -> Path
```

**Purpose**: Finds the local directory where `ufoctl` stores machine-specific files, such as the saved CLI login token. It lets users override the location with an environment variable.

**Data flow**: It reads the `UFOCTL_DIR` environment variable. If it is set, it turns that value into a filesystem path; otherwise it uses the user’s home directory plus `.ufoctl`. It returns that path and does not create it.

**Call relations**: The `init` command uses this path when writing the CLI token, and `portal` uses it later when reading that token to sign the browser in.

*Call graph*: called by 2 (init, portal); 2 external calls (Path, home).


##### `_dotenv_path`  (lines 135–136)

```
def _dotenv_path() -> Path
```

**Purpose**: Finds the `.env` file that sits beside the UFO config file. This keeps local secrets close to `ufo.toml` instead of scattered around the machine.

**Data flow**: It asks the config system where `ufo.toml` lives, takes that file’s parent directory, and appends `.env`. It returns the resulting path.

**Call relations**: Startup secret loading, initialization, missing-key checks, and development-secret writing all call this so they agree on one `.env` location.

*Call graph*: called by 4 (_load_dotenv, _missing_deploy_keys, _write_dev_secrets, init); 1 external calls (config_path).


##### `_dotenv_pairs`  (lines 139–173)

```
def _dotenv_pairs(text: str) -> list[tuple[str, str]]
```

**Purpose**: Parses a small, practical version of the `.env` file format into name/value pairs. It understands comments, blank lines, optional `export`, quoted values, and multi-line quoted secrets such as private keys.

**Data flow**: It receives the raw text of a `.env` file. It scans line by line, skips comments and blanks, separates each `KEY=VALUE`, strips matching quotes when needed, and returns a list of pairs. If a quoted value never closes, it raises an error.

**Call relations**: `_load_dotenv`, `_missing_deploy_keys`, and `_write_dev_secrets` use this parser so all `.env` reading in this file follows the same rules.

*Call graph*: called by 3 (_load_dotenv, _missing_deploy_keys, _write_dev_secrets).


##### `_load_dotenv`  (lines 176–192)

```
def _load_dotenv() -> None
```

**Purpose**: Loads local `.env` settings into the current process before any CLI command runs. It also blocks unsafe bare model-provider key names that would be picked up by unrelated tools.

**Data flow**: It locates `.env`, reads and parses it if present, checks for reserved names like `OPENAI_API_KEY`, and then copies approved values into environment variables. If a reserved name appears, it stops with a clear command-line error.

**Call relations**: The top-level `main` command calls this first, so every subcommand sees the same local secret environment.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (main); 1 external calls (ClickException).


##### `main`  (lines 196–198)

```
def main() -> None
```

**Purpose**: Defines the root `ufoctl` command group. It is the entry point that all subcommands hang from.

**Data flow**: When a user runs any `ufoctl` command, this function runs first and loads `.env` values into the process. It does not return user data; it prepares the environment for the selected subcommand.

**Call relations**: Click calls this before dispatching to commands such as `init`, `serve`, `portal`, `balance`, or `turn`.

*Call graph*: calls 1 internal fn (_load_dotenv).


##### `_one_address`  (lines 201–206)

```
def _one_address(_ctx: click.Context, _param: click.Parameter, value: str) -> str
```

**Purpose**: Checks that an email-like owner address is in the expected single `local@domain` shape. This catches typos before database setup tries to use them.

**Data flow**: It receives the command-line value for `--email`, asks the seat/email helper whether it has a domain, and returns the original value if valid. If invalid, it raises a Click parameter error.

**Call relations**: Click uses it as the validation callback for `init --email`, so bad owner addresses are rejected before `init` starts onboarding.

*Call graph*: 2 external calls (BadParameter, email_domain).


##### `init`  (lines 223–263)

```
def init(email: str, model: str, reasoning: ReasoningEffort, member_model_provider: str | None) -> None
```

**Purpose**: Creates a ready-to-run local UFO workspace. It writes default config when needed, prepares secrets, migrates the database, creates the owner and default agent, and saves a long-lived CLI token.

**Data flow**: It reads command-line choices such as owner email, model, reasoning level, and optional provider. It writes config and `.env` files as needed, applies database schema migrations, runs onboarding, mints a signed token, saves it under the `ufoctl` directory, and prints next steps or missing keys.

**Call relations**: This is usually the first command a developer runs. It delegates setup details to `_write_dev_secrets`, `_create_postgres_system_database`, `_onboard`, `_ufoctl_dir`, and `_missing_deploy_keys`.

*Call graph*: calls 6 internal fn (_create_postgres_system_database, _dotenv_path, _missing_deploy_keys, _onboard, _ufoctl_dir, _write_dev_secrets); 7 external calls (run, ClickException, echo, config_path, load_config, apply_migrations, mint_token).


##### `_missing_deploy_keys`  (lines 266–282)

```
def _missing_deploy_keys(config: Config) -> tuple[str, ...]
```

**Purpose**: Reports extension-required environment keys that are not currently available. It warns early so a feature does not fail much later when a job finally needs a secret.

**Data flow**: It loads active extension manifests, gathers their declared deployment keys, reads current `.env` and process environment values, and returns the missing names in the `UFO_...` form expected for scoped local configuration.

**Call relations**: `init` calls this after onboarding, using the result only for friendly warnings rather than blocking a zero-service local boot.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 1 external calls (load_manifests).


##### `_write_dev_secrets`  (lines 285–307)

```
def _write_dev_secrets(config: Config) -> tuple[str, ...]
```

**Purpose**: Creates the development secrets needed for a local UFO server to run without manual secret setup. It avoids overwriting anything the user already provided.

**Data flow**: It generates an encryption key and signing secrets, reads existing `.env` names and current environment variables, writes only missing ones to `.env`, also puts newly generated values into the current process environment, and returns the names it added.

**Call relations**: `init` calls this before onboarding so token minting and credential encryption have the secrets they need.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 2 external calls (generate_key, token_urlsafe).


##### `_onboard`  (lines 310–364)

```
async def _onboard(config: Config, email: str, model: str, reasoning: ReasoningEffort, member_model_provider: str | None) -> Onboarded
```

**Purpose**: Creates the first workspace, owner member, default agent, and extension onboarding data. It is the deeper setup step behind `ufoctl init`.

**Data flow**: It initializes database access, optionally builds an encrypted credential store, loads extension manifests, checks any requested member model provider key, runs core onboarding, stores the member model key if requested, runs extension onboarding steps, returns an `Onboarded` result, and finally closes database resources.

**Call relations**: `init` calls this after config, secrets, and migrations are ready. It hands most creation work to the `Onboarding` service and credential storage code.

*Call graph*: called by 1 (init); 9 external calls (__init__, __init__, Fernet, dispose_db, init_db, load_manifests, deploy_env, member_slot, items).


##### `_create_postgres_system_database`  (lines 367–378)

```
async def _create_postgres_system_database(config: Config) -> None
```

**Purpose**: Creates the separate PostgreSQL system database if the configured app database uses PostgreSQL and the system database does not exist yet.

**Data flow**: It converts the async database URL into a PostgreSQL connection string, connects, checks whether the target database name exists, creates it if missing, and closes the connection.

**Call relations**: `init` calls this only for PostgreSQL setups before migrations, so the later durable/runtime tables have somewhere to live.

*Call graph*: called by 1 (init); 1 external calls (connect).


##### `migrate`  (lines 382–399)

```
def migrate() -> None
```

**Purpose**: Brings the database schema up to date. A schema is the set of tables and columns the application expects.

**Data flow**: It loads configuration, optionally swaps in an owner database URL from the environment for hosted deployments, applies core and extension migrations, and prints that the schema is current.

**Call relations**: Operators run this after installing extensions or during deployment. It delegates the actual schema changes to the migration system.

*Call graph*: 3 external calls (echo, load_config, apply_migrations).


##### `_one_slug`  (lines 402–405)

```
def _one_slug(_ctx: click.Context, _param: click.Parameter, value: str) -> str
```

**Purpose**: Checks that a new migration name is safe snake_case text. This prevents odd filenames or unclear migration labels.

**Data flow**: It receives a proposed slug, matches it against the allowed pattern, returns it if valid, and raises a Click parameter error if not.

**Call relations**: Click uses it to validate the `new-migration` command argument before that command writes any files.

*Call graph*: 1 external calls (BadParameter).


##### `new_migration`  (lines 410–427)

```
def new_migration(slug: str) -> None
```

**Purpose**: Creates a new empty core database migration file with a timestamp revision. This gives developers a safe starting point for schema changes.

**Data flow**: It reads the current core migration head, generates a UTC timestamp revision, writes a template migration file, and updates the `HEAD` marker file to that new revision.

**Call relations**: Developers call this while changing the database schema. It uses containment helpers so the generated file stays inside the migrations directory.

*Call graph*: 4 external calls (echo, now, core_migration_head, contained_file).


##### `serve`  (lines 431–440)

```
def serve() -> None
```

**Purpose**: Starts the main UFO server process. It also tells the user where the browser portal will be if the active pack provides one.

**Data flow**: It loads config and extension manifests, asks which portal surface should be home, prints a portal URL when available, and then hands control to the server runner.

**Call relations**: This is the command that moves from setup into the running application. It calls `_serve_base` for the public URL and `serve_run` for the actual server loop.

*Call graph*: calls 1 internal fn (_serve_base); 5 external calls (echo, load_config, load_manifests, home_surface, run).


##### `portal`  (lines 444–462)

```
def portal() -> None
```

**Purpose**: Opens the UFO browser portal already signed in with this machine’s CLI token. It saves the user from copying and pasting login tokens.

**Data flow**: It loads config, finds the home surface, reads the saved CLI token, checks that the server answers, builds the portal URL, starts a secure browser handoff, and prints what it opened.

**Call relations**: Users run this after `init` and `serve`. It relies on `_ufoctl_dir` to find the token, `_serve_base` to choose the URL, and `BrowserHandoff` to pass the token safely to the browser.

*Call graph*: calls 2 internal fn (_serve_base, _ufoctl_dir); 7 external calls (__init__, ClickException, echo, get, load_config, load_manifests, home_surface).


##### `_serve_base`  (lines 465–472)

```
def _serve_base(config: Config) -> str
```

**Purpose**: Chooses the base web address users and browser sessions should use for the running server. This matters because browser cookies are tied to a host name.

**Data flow**: It reads config. If `connect.public_base_url` is set, it returns that public address; otherwise it builds a local URL from the configured server host and port.

**Call relations**: `serve` uses it when printing the portal hint, and `portal` uses it when checking and opening the portal.

*Call graph*: called by 2 (portal, serve).


##### `BrowserHandoff.open`  (lines 487–495)

```
def open(self) -> None
```

**Purpose**: Transfers the saved CLI token into the browser through a temporary local web page. The token is put in a form body rather than exposed in the address bar.

**Data flow**: It creates a random local path, starts a one-use HTTP server on loopback, opens the browser to that path, serves requests until the token page has been delivered, and then closes the server.

**Call relations**: `portal` calls this after verifying the server is reachable. It creates the responder with `_responder`, which in turn serves the page made by `_page`.

*Call graph*: calls 1 internal fn (_responder); 5 external calls (echo, HTTPServer, token_urlsafe, Event, open).


##### `BrowserHandoff._responder`  (lines 497–514)

```
def _responder(self, path: str, delivered: threading.Event) -> type[BaseHTTPRequestHandler]
```

**Purpose**: Builds the tiny HTTP request handler used by the browser handoff. It only serves the secret-bearing page at the one random path.

**Data flow**: It receives the allowed path and an event object, prepares the HTML page bytes, and returns a handler class that can answer one correct GET request and reject others.

**Call relations**: `BrowserHandoff.open` passes this handler class into `HTTPServer` so the temporary listener knows how to respond.

*Call graph*: calls 1 internal fn (_page); called by 1 (open).


##### `BrowserHandoff._responder.do_GET`  (lines 501–510)

```
def do_GET(self) -> None
```

**Purpose**: Answers the browser’s GET request during token handoff. It either serves the login-forwarding page or rejects the request.

**Data flow**: It reads the incoming request path. If the path is wrong, it sends 404; if correct, it sends the HTML page, writes it to the response, and marks the handoff as delivered.

**Call relations**: The temporary local HTTP server calls this when the browser opens the random handoff URL created by `BrowserHandoff.open`.


##### `BrowserHandoff._responder.log_message`  (lines 512–512)

```
def log_message(self, *args: object) -> None
```

**Purpose**: Silences the default local HTTP server logging. This keeps token handoff from printing noisy request lines to the terminal.

**Data flow**: It receives whatever log arguments the server would normally print and deliberately does nothing.

**Call relations**: The handler class returned by `_responder` uses this override whenever the built-in HTTP server tries to log a request.


##### `BrowserHandoff._page`  (lines 516–523)

```
def _page(self) -> str
```

**Purpose**: Creates the HTML page that posts the CLI token to the portal. It includes a button as a fallback, but JavaScript submits it automatically.

**Data flow**: It reads the handoff object’s portal URL and token, HTML-escapes both for safety, places them into a small form, and returns the page as a string.

**Call relations**: `_responder` calls this once when preparing the temporary page that `do_GET` later sends to the browser.

*Call graph*: called by 1 (_responder); 1 external calls (escape).


##### `ingress`  (lines 527–529)

```
def ingress() -> None
```

**Purpose**: Starts the sandbox ingress service, which is a protected doorway to sandbox ports used by conversations.

**Data flow**: It takes no command-specific input and simply calls the sandbox ingress runner. The runner takes over from there.

**Call relations**: Operators use this when they need the sandbox reverse proxy running alongside the main system.

*Call graph*: 1 external calls (run).


##### `spend_cap`  (lines 533–534)

```
def spend_cap() -> None
```

**Purpose**: Defines the command group for spend-cap operations. Spend caps are safety rules that limit how much model usage can cost over a time window.

**Data flow**: It does not process data itself. It provides a grouping point for subcommands that set or list caps.

**Call relations**: Click uses this as the parent for `spend-cap set` and `spend-cap list`.


##### `spend_cap_set`  (lines 545–563)

```
def spend_cap_set(scope: str, subject_id: str, window_seconds: int, limit_micro_usd: int, on_breach: str) -> None
```

**Purpose**: Creates or updates a spending limit for a workspace, member, or agent. This helps stop runaway cost before work is admitted or continued.

**Data flow**: It validates the scope and subject id, converts the subject id to a UUID when needed, loads config, writes the cap in the database, converts micro-dollars into dollars for display, and prints the result.

**Call relations**: This command is the user-facing wrapper around `_write_spend_cap`, which does the database insert or update.

*Call graph*: calls 1 internal fn (_write_spend_cap); 5 external calls (run, ClickException, echo, load_config, UUID).


##### `spend_cap_list`  (lines 567–577)

```
def spend_cap_list() -> None
```

**Purpose**: Prints the currently configured spend caps. It gives operators a quick view of the active cost guardrails.

**Data flow**: It loads config, reads cap rows from the database, and prints either a friendly empty message or one formatted line per cap.

**Call relations**: This command delegates database reading to `_read_spend_caps` and focuses on turning the result into terminal output.

*Call graph*: calls 1 internal fn (_read_spend_caps); 3 external calls (run, echo, load_config).


##### `_write_spend_cap`  (lines 580–634)

```
async def _write_spend_cap(config: Config, scope: str, subject: UUID | None, window_seconds: int, limit_micro_usd: int, on_breach: str) -> UUID
```

**Purpose**: Writes one spend-cap rule to the database, updating an existing matching rule instead of creating a duplicate.

**Data flow**: It opens the database, finds the current workspace, checks for a cap with the same workspace, scope, subject, and window, updates it if found, otherwise creates a new UUID and inserts a new row. It returns the cap id and closes the database.

**Call relations**: `spend_cap_set` calls this after command-line validation. The runtime later uses these database rows to decide whether work may proceed.

*Call graph*: called by 1 (spend_cap_set); 7 external calls (insert, select, update, dispose_db, init_db, workspace_tx, uuid4).


##### `_read_spend_caps`  (lines 637–663)

```
async def _read_spend_caps(config: Config) -> list[tuple[UUID, str, UUID | None, int, int, str]]
```

**Purpose**: Reads all spend-cap rules for the current workspace. It returns structured rows for the listing command to print.

**Data flow**: It opens the database, finds the workspace id, selects cap fields ordered by scope, converts the rows into tuples, returns them, and closes the database.

**Call relations**: `spend_cap_list` calls this and then formats the returned caps for humans.

*Call graph*: called by 1 (spend_cap_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `balance`  (lines 667–668)

```
def balance() -> None
```

**Purpose**: Defines the command group for prepaid balance operations. The balance is the pool of money-like credit used to admit work.

**Data flow**: It does not read or write balance data itself. It groups the show, credit, and reserve subcommands.

**Call relations**: Click uses this as the parent for `balance show`, `balance credit`, and `balance reserve`.


##### `balance_show`  (lines 673–688)

```
def balance_show(workspace_id: str) -> None
```

**Purpose**: Shows the workspace’s current prepaid balance and related lifetime totals. It helps an operator see whether work has enough financial headroom.

**Data flow**: It loads config, reads the balance for the named or only workspace, and prints balance, reserve, granted total, charged total, and last purchase time. If no balance exists, it says so.

**Call relations**: This command calls `_read_balance`, which opens the right workspace scope and asks the billing layer for the balance.

*Call graph*: calls 1 internal fn (_read_balance); 3 external calls (run, echo, load_config).


##### `balance_credit`  (lines 696–709)

```
def balance_credit(granted_micro_usd: int, charged_micro_usd: int, reference: str, workspace_id: str) -> None
```

**Purpose**: Adds credit to a workspace balance once per reference key. The reference prevents accidental double-crediting when a command or payment callback is retried.

**Data flow**: It validates that the granted amount is not zero, loads config, asks `_credit_balance` to apply the credit, and prints whether it was newly credited or already applied.

**Call relations**: This user-facing command delegates the safe idempotent balance update to `_credit_balance`.

*Call graph*: calls 1 internal fn (_credit_balance); 4 external calls (run, ClickException, echo, load_config).


##### `balance_reserve`  (lines 715–723)

```
def balance_reserve(micro_usd: int, workspace_id: str) -> None
```

**Purpose**: Sets the minimum balance headroom a turn needs before it may start. This is a financial safety buffer.

**Data flow**: It checks that the reserve amount is not negative, loads config, calls `_set_reserve`, prints the new reserve if a balance exists, or errors if there is no balance yet.

**Call relations**: It wraps `_set_reserve`, which performs the database-scoped billing update.

*Call graph*: calls 1 internal fn (_set_reserve); 4 external calls (run, ClickException, echo, load_config).


##### `_target_workspace`  (lines 726–748)

```
async def _target_workspace(named: str) -> UUID
```

**Purpose**: Chooses which workspace an admin command should act on. If none is named, it only proceeds when the deployment has exactly one workspace.

**Data flow**: It reads through the owner database connection. With a named workspace, it validates that the UUID exists; without one, it lists workspaces and either returns the only id or raises a clear error for none or many.

**Call relations**: Balance and seed helpers call this before entering workspace-specific database context, avoiding accidental writes to the wrong workspace.

*Call graph*: called by 2 (_balance_scope, _seed_target); 4 external calls (ClickException, select, owner_tx, UUID).


##### `_balance_scope`  (lines 752–769)

```
async def _balance_scope(config: Config, named: str) -> AsyncIterator[tuple[AsyncConnection, UUID]]
```

**Purpose**: Opens the correct database context for balance commands. It first resolves the workspace, then pins later reads and writes to that workspace.

**Data flow**: It initializes app and owner database access, uses `_target_workspace` to choose a workspace, enters the workspace context, yields a database connection and workspace id to the caller, and finally disposes database resources.

**Call relations**: `_read_balance`, `_credit_balance`, and `_set_reserve` all use this shared setup so each balance operation acts on the same carefully chosen workspace.

*Call graph*: calls 1 internal fn (_target_workspace); called by 3 (_credit_balance, _read_balance, _set_reserve); 5 external calls (dispose_db, init_db, init_owner_db, workspace_tx, ws).


##### `_read_balance`  (lines 772–774)

```
async def _read_balance(config: Config, named: str) -> Balance | None
```

**Purpose**: Reads the balance record for the selected workspace. It is the database-facing helper behind `balance show`.

**Data flow**: It opens `_balance_scope`, passes the connection and workspace id to the billing balance reader, and returns either a balance object or `None`.

**Call relations**: `balance_show` calls this and then formats the returned balance for terminal output.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_show); 1 external calls (read_balance).


##### `_credit_balance`  (lines 777–783)

```
async def _credit_balance(config: Config, named: str, granted_micro_usd: int, charged_micro_usd: int, reference: str) -> bool
```

**Purpose**: Applies a credit or correction to the selected workspace balance. It relies on the billing layer to enforce one credit per reference.

**Data flow**: It opens `_balance_scope`, sends the grant amount, charged amount, and reference to the billing credit function, and returns `true` if a new credit was applied or `false` if the reference was already used.

**Call relations**: `balance_credit` calls this after validating command-line input.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_credit); 1 external calls (credit).


##### `_set_reserve`  (lines 786–788)

```
async def _set_reserve(config: Config, named: str, reserve_micro_usd: int) -> bool
```

**Purpose**: Sets the reserve amount for the selected workspace balance. The reserve is the amount that must remain available before new work can start.

**Data flow**: It opens `_balance_scope`, passes the workspace id and reserve amount to the billing layer, and returns whether the reserve was set successfully.

**Call relations**: `balance_reserve` calls this and turns the boolean result into either a success message or a clear error.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_reserve); 1 external calls (set_reserve).


##### `flags`  (lines 795–800)

```
def flags() -> None
```

**Purpose**: Defines the command group for feature flag administration. Feature flags let operators turn behavior on or off without changing code.

**Data flow**: It does not change flags itself. It groups subcommands that write flag states to the selected backend service.

**Call relations**: Click uses this as the parent for `flags set`.


##### `flags_set`  (lines 806–828)

```
def flags_set(key: str, on: bool) -> None
```

**Purpose**: Sets the default on/off value for one feature flag in the configured flag backend. It refuses unknown flags so dashboards do not show settings that no active code reads.

**Data flow**: It loads config and extension manifests, verifies the flag backend exists and the key is declared by an active extension, imports the backend admin module, asks it to serve the flag on or off, and prints the new state.

**Call relations**: Operators call this directly. It bridges extension-declared flags to the backend-specific admin implementation loaded at runtime.

*Call graph*: 5 external calls (ClickException, echo, import_module, load_config, load_manifests).


##### `spend`  (lines 836–859)

```
def spend(window_seconds: int) -> None
```

**Purpose**: Prints a cost report for a recent time window. It breaks spending down by dimensions such as member, agent, origin, and price version.

**Data flow**: It loads config, reads the spend report for the requested window, converts micro-dollars to dollars, and prints the total plus several breakdown sections.

**Call relations**: This command calls `_read_spend`, which asks the billing rollup service to summarize ledger entries.

*Call graph*: calls 1 internal fn (_read_spend); 3 external calls (run, echo, load_config).


##### `_read_spend`  (lines 862–869)

```
async def _read_spend(config: Config, window_seconds: int) -> SpendReport
```

**Purpose**: Reads the billing ledger rollup for the current workspace over a chosen time window.

**Data flow**: It initializes the database, finds the workspace id, creates a spend rollup reader, asks it for a report, returns that report, and closes the database.

**Call relations**: `spend` calls this and then handles all human-readable formatting.

*Call graph*: called by 1 (spend); 5 external calls (__init__, select, dispose_db, init_db, workspace_tx).


##### `transcript_reads`  (lines 877–890)

```
def transcript_reads(limit: int) -> None
```

**Purpose**: Lists recorded admin disclosures for reading another member’s private transcript. This gives operators an audit view of sensitive access.

**Data flow**: It validates that the limit is positive, loads config, reads recent transcript access rows, and prints either an empty message or one line per access record with time, reader, subject, and conversation.

**Call relations**: This command wraps `_read_transcript_accesses`, which performs the database query.

*Call graph*: calls 1 internal fn (_read_transcript_accesses); 4 external calls (run, ClickException, echo, load_config).


##### `_read_transcript_accesses`  (lines 893–927)

```
async def _read_transcript_accesses(config: Config, limit: int) -> list[tuple[str, str, UUID, datetime]]
```

**Purpose**: Reads recent transcript-access disclosure records from the database. These records show who read whose private conversation transcript and when.

**Data flow**: It initializes the database, joins transcript access rows to reader and subject member records, filters to the workspace, orders newest first, limits the result, converts rows into tuples, and closes the database.

**Call relations**: `transcript_reads` calls this and formats the audit rows for the terminal.

*Call graph*: called by 1 (transcript_reads); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `grants`  (lines 931–943)

```
def grants() -> None
```

**Purpose**: Lists OAuth account grants available to agents. OAuth is a standard way to let an app access another service without storing a password.

**Data flow**: It loads config, reads grant summaries, and prints either `no grants` or one line per agent/provider/account with whether the grant is shared or private.

**Call relations**: This command calls `_read_grants`, which gathers the workspace’s grant summary data.

*Call graph*: calls 1 internal fn (_read_grants); 3 external calls (run, echo, load_config).


##### `_read_grants`  (lines 946–953)

```
async def _read_grants(config: Config) -> tuple[GrantSummary, ...]
```

**Purpose**: Fetches summarized connected-account grants for the current workspace.

**Data flow**: It initializes the database, finds the workspace id, calls the grant-summary service for that workspace, returns the summaries, and closes database resources.

**Call relations**: `grants` calls this and then turns each summary into a readable command-line row.

*Call graph*: called by 1 (grants); 5 external calls (select, dispose_db, init_db, workspace_tx, workspace_grant_summaries).


##### `credential`  (lines 957–959)

```
def credential() -> None
```

**Purpose**: Defines the command group for bring-your-own-key credential slots. These are secret values extensions ask the workspace owner to provide.

**Data flow**: It does not read or write credentials itself. It groups commands for setting and listing declared credential slots.

**Call relations**: Click uses this as the parent for `credential set` and `credential list`.


##### `credential_set`  (lines 964–982)

```
def credential_set(slot: str) -> None
```

**Purpose**: Stores one secret value in a declared credential slot, encrypted at rest. It avoids unsafe input paths by never taking the secret as a command-line argument.

**Data flow**: It loads config, checks that the slot is declared by an active extension, checks that the encryption key is available, reads the secret from a hidden prompt or standard input, validates it is not empty, writes it, and prints confirmation without revealing the value.

**Call relations**: It uses `_declared_slots` to validate the slot and `_write_credential` to store the encrypted secret.

*Call graph*: calls 2 internal fn (_declared_slots, _write_credential); 5 external calls (run, ClickException, echo, prompt, load_config).


##### `credential_list`  (lines 986–996)

```
def credential_list() -> None
```

**Purpose**: Shows which declared credential slots are set or unset. It never prints secret values.

**Data flow**: It loads config, gathers declared slots, reads stored slot names, and prints each slot with its owning extension and status. If no slots are declared, it says so.

**Call relations**: It combines `_declared_slots` and `_read_stored_slots` to compare what extensions request with what the workspace has stored.

*Call graph*: calls 2 internal fn (_declared_slots, _read_stored_slots); 3 external calls (run, echo, load_config).


##### `_declared_slots`  (lines 999–1004)

```
def _declared_slots(config: Config) -> dict[str, str]
```

**Purpose**: Collects credential slots declared by active extensions. This is the allow-list for credentials users may set.

**Data flow**: It loads extension manifests for the configured pack and returns a mapping from slot name to extension name. If manifest loading fails, it turns that into a command-line error.

**Call relations**: `credential_set` uses it to reject unknown slots, and `credential_list` uses it to show all expected slots.

*Call graph*: called by 2 (credential_list, credential_set); 2 external calls (ClickException, load_manifests).


##### `_write_credential`  (lines 1007–1014)

```
async def _write_credential(config: Config, key: str, slot: str, value: str) -> None
```

**Purpose**: Encrypts and stores a credential value for the current workspace.

**Data flow**: It initializes the database, finds the workspace id, creates a credential store using the provided encryption key, writes the slot value, and closes the database.

**Call relations**: `credential_set` calls this after safely collecting the secret from the user.

*Call graph*: called by 1 (credential_set); 6 external calls (__init__, Fernet, select, dispose_db, init_db, workspace_tx).


##### `_read_stored_slots`  (lines 1017–1031)

```
async def _read_stored_slots(config: Config) -> frozenset[str]
```

**Purpose**: Reads the names of credential slots that already have stored values. It reads only names, not secret contents.

**Data flow**: It initializes the database, finds the workspace id, selects credential slot names for that workspace, returns them as an immutable set, and closes the database.

**Call relations**: `credential_list` calls this to decide whether each declared slot should be shown as set or unset.

*Call graph*: called by 1 (credential_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `ext`  (lines 1035–1036)

```
def ext() -> None
```

**Purpose**: Defines the command group for extension-store operations. Extensions add capabilities to a UFO pack.

**Data flow**: It does not interact with the store itself. It groups search, install, and remove commands.

**Call relations**: Click uses this as the parent for `ext search`, `ext install`, and `ext remove`.


##### `_store`  (lines 1039–1042)

```
def _store(config: Config) -> ExtensionStore
```

**Purpose**: Builds an extension-store object from config and the local lockfile. The store knows what extensions exist and what is pinned for this deploy.

**Data flow**: It checks that an extension store is configured, reads the catalog, finds the lockfile path, and returns an `ExtensionStore`. If no store is configured, it raises a command-line error.

**Call relations**: All extension subcommands call this before searching, installing, or removing extensions.

*Call graph*: called by 3 (ext_install, ext_remove, ext_search); 4 external calls (__init__, ClickException, lockfile_path, read_catalog).


##### `ext_search`  (lines 1047–1060)

```
def ext_search(query: str) -> None
```

**Purpose**: Searches the configured extension catalog and shows matching extensions. It marks whether each match is installed, available, or bundle-only.

**Data flow**: It loads config, builds the store, searches with the optional query, and prints either an empty message or formatted listings.

**Call relations**: This is the read-only extension command. It relies on `_store` to connect catalog data with the current lockfile.

*Call graph*: calls 1 internal fn (_store); 2 external calls (echo, load_config).


##### `ext_install`  (lines 1065–1071)

```
def ext_install(name: str) -> None
```

**Purpose**: Pins an extension from the catalog into the deploy’s lockfile. The next server start will load it.

**Data flow**: It loads config, builds the store, asks it to install the named extension, catches user-facing store errors, and prints the pinned name, version, and digest.

**Call relations**: Operators call this before running migrations and restarting or serving, so extension code and database schema line up.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `ext_remove`  (lines 1076–1082)

```
def ext_remove(name: str) -> None
```

**Purpose**: Removes an extension pin from the deploy’s lockfile. The next server start stops loading that extension.

**Data flow**: It loads config, builds the store, asks it to remove the named extension, catches user-facing store errors, and prints confirmation.

**Call relations**: This is the counterpart to `ext_install`, using `_store` for access to the catalog and lockfile.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `_ufo_project_dir`  (lines 1088–1099)

```
def _ufo_project_dir() -> Path
```

**Purpose**: Finds the source checkout of the UFO Python project so bundling can build the correct wheel. A wheel is a Python package archive.

**Data flow**: It walks upward from this file, looking for a `pyproject.toml` whose project name is `ufo`. It returns that directory, or raises an error if running from an install that lacks source files.

**Call relations**: `bundle` calls this before invoking the package builder, so bundles are built from the actual UFO source project rather than the current working directory.

*Call graph*: called by 1 (bundle); 3 external calls (ClickException, Path, loads).


##### `bundle`  (lines 1111–1141)

```
def bundle(out: Path, client_binary: Path) -> None
```

**Purpose**: Creates a runnable deploy bundle containing the UFO wheel, client binary, config, and pinned extensions. This packages a local deploy into an artifact.

**Data flow**: It loads config and optional extension catalog, runs `uv build` to create the UFO wheel, verifies the expected wheel exists, builds the bundle object, and prints the output path and pinned extensions.

**Call relations**: This command ties packaging pieces together. It uses `_ufo_project_dir` to find source code and the `Bundle` builder to assemble the final artifact.

*Call graph*: calls 1 internal fn (_ufo_project_dir); 8 external calls (__init__, ClickException, echo, run, wheel_name, config_path, load_config, read_catalog).


##### `turn`  (lines 1145–1146)

```
def turn() -> None
```

**Purpose**: Defines the command group for acting on a single turn. A turn is one unit of agent work in a conversation.

**Data flow**: It does not act on turns directly. It groups commands for cancelling a turn and inspecting its durable steps.

**Call relations**: Click uses this as the parent for `turn cancel` and `turn steps`.


##### `turn_cancel`  (lines 1152–1162)

```
def turn_cancel(turn_id: str, workspace_id: str) -> None
```

**Purpose**: Cancels one stuck or unwanted turn. It is an operator escape hatch when normal user-facing cancellation cannot finish the job.

**Data flow**: It loads config, converts the given turn id to a UUID, calls `_cancel_turn`, and prints whether the turn was cancelled or had already reached a final state.

**Call relations**: This command is the terminal-facing wrapper around `_cancel_turn`, which resolves the workspace and talks to the durable workflow system.

*Call graph*: calls 1 internal fn (_cancel_turn); 4 external calls (run, echo, load_config, UUID).


##### `_cancel_turn`  (lines 1165–1199)

```
async def _cancel_turn(config: Config, turn_id: UUID, named_workspace: str) -> bool
```

**Purpose**: Performs the actual cancellation of a turn, including finding its workspace when needed and asking the durable runtime to cancel it safely.

**Data flow**: It initializes database access, resolves the workspace either from the argument or owner database, verifies the turn exists when appropriate, creates a replay-safe durable client, enters the workspace context, calls the cancellation service, returns whether anything was cancelled, and closes resources.

**Call relations**: `turn_cancel` calls this. It coordinates database lookup, workspace scoping, durable-client creation, and the runtime cancellation function.

*Call graph*: called by 1 (turn_cancel); 11 external calls (ClickException, select, dispose_db, init_db, init_owner_db, owner_tx, workspace_tx, replay_safe_client, cancel_one_turn, ws (+1 more)).


##### `turn_steps`  (lines 1205–1213)

```
def turn_steps(turn_id: str, workspace_id: str) -> None
```

**Purpose**: Prints the recorded durable step log for one turn. This helps operators understand what a turn actually did, even if the conversation transcript no longer shows every detail.

**Data flow**: It loads config, converts the turn id to a UUID, and calls `_print_turn_steps` with the optional workspace id. It does not format the steps itself.

**Call relations**: This command is the user-facing entry for step inspection, delegating lookup and printing to `_print_turn_steps` and `_echo_turn_steps`.

*Call graph*: calls 1 internal fn (_print_turn_steps); 3 external calls (run, load_config, UUID).


##### `_print_turn_steps`  (lines 1216–1248)

```
async def _print_turn_steps(config: Config, turn_id: UUID, named_workspace: str) -> None
```

**Purpose**: Finds a turn’s workspace and prints the durable workflow steps recorded for that turn or its running attempt.

**Data flow**: It initializes databases, resolves the workspace from the argument or owner database, enters that workspace, reads the turn’s running attempt id, reads durable steps from the DBOS step log through a replay-safe client, prints them, and disposes resources.

**Call relations**: `turn_steps` calls this. After gathering the step tuple, it hands formatting to `_echo_turn_steps`.

*Call graph*: calls 1 internal fn (_echo_turn_steps); called by 1 (turn_steps); 11 external calls (__init__, ClickException, select, dispose_db, init_db, init_owner_db, owner_tx, workspace_tx, replay_safe_client, ws (+1 more)).


##### `_echo_turn_steps`  (lines 1251–1264)

```
def _echo_turn_steps(steps: tuple[TurnStep, ...]) -> None
```

**Purpose**: Formats durable turn steps for the terminal. It shows step number, kind, name, function, duration, and any recorded messages.

**Data flow**: It receives a tuple of step records. If empty, it prints a simple message; otherwise it loops through steps and messages, printing text directly and formatting structured content blocks through `_echo_block`.

**Call relations**: `_print_turn_steps` calls this after reading step data. It delegates individual structured block display to `_echo_block`.

*Call graph*: calls 1 internal fn (_echo_block); called by 1 (_print_turn_steps); 1 external calls (echo).


##### `_echo_block`  (lines 1267–1277)

```
def _echo_block(block: object) -> str
```

**Purpose**: Turns one structured message block into a readable line of text. It knows how to describe text, tool calls, and tool results.

**Data flow**: It receives an arbitrary block object, checks its type, and returns a string: plain text for text blocks, a compact JSON argument display for tool calls, a result line for tool results, or the type name for unknown blocks.

**Call relations**: `_echo_turn_steps` calls this whenever a step message contains block-style content instead of a simple string.

*Call graph*: called by 1 (_echo_turn_steps); 1 external calls (dumps).


##### `seed`  (lines 1281–1282)

```
def seed() -> None
```

**Purpose**: Defines the command group for writing demonstration content into a workspace.

**Data flow**: It does not create demo data itself. It groups seeding subcommands.

**Call relations**: Click uses this as the parent for `seed kitchen-sink`.


##### `seed_kitchen_sink`  (lines 1287–1296)

```
def seed_kitchen_sink(workspace_id: str) -> None
```

**Purpose**: Writes a rich demo conversation that exercises many portal display shapes. Designers and developers can use it as a stable visual test case.

**Data flow**: It loads config, asks `_seed_kitchen_sink` to create or replace the demo content in the chosen workspace, and prints the portal route for the resulting conversation.

**Call relations**: This is the user-facing seed command. It delegates database and blob setup to `_seed_kitchen_sink`.

*Call graph*: calls 1 internal fn (_seed_kitchen_sink); 3 external calls (run, echo, load_config).


##### `_seed_kitchen_sink`  (lines 1299–1310)

```
async def _seed_kitchen_sink(config: Config, named: str) -> UUID
```

**Purpose**: Sets up the database and blob-store environment needed to write the kitchen-sink demo conversation.

**Data flow**: It initializes app and optional owner database access, creates a workspace-aware blob store from config, calls `_seed_target` to write the actual content, returns the conversation id, and disposes database resources.

**Call relations**: `seed_kitchen_sink` calls this. It prepares infrastructure, while `_seed_target` chooses the workspace and writes the seed data.

*Call graph*: calls 1 internal fn (_seed_target); called by 1 (seed_kitchen_sink); 5 external calls (__init__, blob_store_for, dispose_db, init_db, init_owner_db).


##### `_seed_target`  (lines 1313–1344)

```
async def _seed_target(blob: WorkspaceBlobStore, named: str) -> UUID
```

**Purpose**: Writes the kitchen-sink demo into one resolved workspace using that workspace’s main agent and first member.

**Data flow**: It resolves the target workspace, enters workspace context, reads the main agent id and first member, errors if no member exists, creates a `KitchenSink` writer with blob, workspace, agent, member, and email information, writes the content, and returns the conversation id.

**Call relations**: `_seed_kitchen_sink` calls this after database and blob setup. It uses `_target_workspace` for safe workspace selection and `KitchenSink` for the actual demo content.

*Call graph*: calls 1 internal fn (_target_workspace); called by 1 (_seed_kitchen_sink); 5 external calls (__init__, ClickException, select, workspace_tx, ws).
