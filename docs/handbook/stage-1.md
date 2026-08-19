# Operator entrypoints, local setup, and process launch  `stage-1`

This stage is the system’s front door. It covers the steps an operator or developer uses before UFO is fully running: creating a local workspace, packaging a deployable setup, checking sandbox support, and finally starting the server process.

The ufoctl command in cli.py is the main control panel. It lets people initialize, inspect, package, run, or repair a workspace without editing hidden files or databases by hand. onboarding.py performs the first-run setup behind that command: it creates the workspace, the first admin user, the main assistant agent, checks needed keys, and lets extensions finish their own setup. bundle.py freezes a working setup into a Docker build folder, so the same configuration can be launched elsewhere.

The sandbox validation commands check the safe execution area used later for running code. One script builds and compares sandbox images; another tests that secure proxy networking works. serve.py is the actual service launcher: it loads configuration, connects storage, extensions, sandboxes, jobs, web routes, and workers, then starts the FastAPI web server. The sample skill probe is a tiny diagnostic that confirms a skill can run.

## Sub-stages

- [Sandbox image and deployment validation commands](stage-1.1.md) `stage-1.1` — 2 files

## Files in this stage

### Local workspace commands
Operator-facing CLI commands prepare a UFO workspace, package deployable state, and perform first-run setup.

### `core/src/ufo/cli.py`

`entrypoint` · `operator command / startup`

`cli.py` turns many parts of the system into clear terminal commands. It helps a new local install become usable by writing a default config, creating development secrets, applying database migrations, onboarding the first workspace owner, and saving a local command-line token. It can then start the server, open the browser portal already signed in, run the ingress proxy, and bundle the deploy into a portable artifact.

The file also gives operators safe tools for common maintenance work. They can set spending limits, inspect prepaid balance and recent spend, list recorded transcript disclosures, see connected OAuth grants, fill encrypted credential slots, install or remove extensions, cancel a stuck turn, and seed demo content. Most commands follow the same pattern: read configuration, open the database, do one focused action through the proper subsystem, print a human-readable result, and always close database connections afterward.

A key theme is safety. Secrets are kept out of command arguments, browser sign-in tokens are handed off through a one-use local web page rather than a URL, and hosted multi-workspace actions force the operator to name a workspace instead of guessing. Without this file, the system could still have internal libraries, but it would lack the practical control panel people need to initialize, run, and operate it.

#### Function details

##### `_ufoctl_dir`  (lines 77–79)

```
def _ufoctl_dir() -> Path
```

**Purpose**: Finds the private directory where this machine stores `ufoctl` state, such as the local CLI token. It lets tests or deployments override the location with an environment variable.

**Data flow**: It reads the `UFOCTL_DIR` environment variable. If it is set, that path is used; otherwise it builds a path under the user's home directory called `.ufoctl`. It returns that path without creating it.

**Call relations**: `init` uses this location to write the newly minted CLI token, and `portal` uses it later to read that token back for browser sign-in.

*Call graph*: called by 2 (init, portal); 2 external calls (Path, home).


##### `_dotenv_path`  (lines 82–83)

```
def _dotenv_path() -> Path
```

**Purpose**: Returns the path to the `.env` file that sits beside the main UFO config file. This is where local development secrets are written and later loaded.

**Data flow**: It asks the config module where the config file lives, takes that file's folder, and appends `.env`. The result is a path object.

**Call relations**: Several setup helpers use this as the shared address for environment-style secrets: loading them at command start, writing missing development secrets, and checking which provider keys are missing.

*Call graph*: called by 4 (_load_dotenv, _missing_deploy_keys, _write_dev_secrets, init); 1 external calls (config_path).


##### `_dotenv_pairs`  (lines 86–120)

```
def _dotenv_pairs(text: str) -> list[tuple[str, str]]
```

**Purpose**: Parses simple `.env` text into key-value pairs. It understands comments, optional `export`, quoted values, and multi-line quoted secrets such as private keys.

**Data flow**: It receives raw text, walks through it line by line, skips blank lines and comments, splits `KEY=VALUE` entries, removes matching quotes when present, and returns a list of `(name, value)` pairs. If a quoted value never closes, it raises an error.

**Call relations**: The environment loader, secret writer, and missing-key checker all rely on this one parser so they interpret `.env` files the same way.

*Call graph*: called by 3 (_load_dotenv, _missing_deploy_keys, _write_dev_secrets).


##### `_load_dotenv`  (lines 123–128)

```
def _load_dotenv() -> None
```

**Purpose**: Loads local secrets from the `.env` file into process environment variables. This lets later commands behave as if the user had exported those secrets manually.

**Data flow**: It locates `.env`; if the file is absent, nothing changes. If present, it parses the file and writes each parsed value into `os.environ`.

**Call relations**: The top-level `main` command group calls this before running subcommands, so all CLI commands see the same local secrets.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (main).


##### `main`  (lines 132–134)

```
def main() -> None
```

**Purpose**: Defines the top-level `ufoctl` command group. It is the umbrella under which all other subcommands are registered.

**Data flow**: When a CLI invocation begins, it loads `.env` into the current process. It does not return user-facing data; it prepares the environment for whichever subcommand Click dispatches next.

**Call relations**: Click uses this as the command tree root. Every command in this file hangs from this group or one of its subgroups.

*Call graph*: calls 1 internal fn (_load_dotenv).


##### `_one_address`  (lines 137–142)

```
def _one_address(_ctx: click.Context, _param: click.Parameter, value: str) -> str
```

**Purpose**: Validates that an email option is a single normal-looking address with a domain. It catches typos early, before database onboarding starts.

**Data flow**: It receives the text passed to `--email`, checks whether it has one local part and domain, and either returns the original value or raises a readable Click validation error.

**Call relations**: `init` uses this as the callback for its owner email option, so bad owner addresses are rejected at command-line parsing time.

*Call graph*: 2 external calls (BadParameter, email_domain).


##### `init`  (lines 148–180)

```
def init(email: str, model: str) -> None
```

**Purpose**: Creates a usable workspace from a fresh checkout. It writes default config if needed, prepares secrets, migrates the database, onboards the owner and default agent, and saves a CLI token.

**Data flow**: It reads command options, config files, and environment secrets. It may write `ufo.toml`, append secrets to `.env`, create a PostgreSQL system database, run migrations, create workspace records, mint a long-lived token, and write that token to the private `ufoctl` directory. It prints what it created and warns about missing provider keys.

**Call relations**: This is the main first-run command. It delegates setup details to `_write_dev_secrets`, `_create_postgres_system_database`, `_onboard`, `_ufoctl_dir`, and `_missing_deploy_keys`.

*Call graph*: calls 6 internal fn (_create_postgres_system_database, _dotenv_path, _missing_deploy_keys, _onboard, _ufoctl_dir, _write_dev_secrets); 7 external calls (run, ClickException, echo, mint_token, config_path, load_config, apply_migrations).


##### `_missing_deploy_keys`  (lines 183–198)

```
def _missing_deploy_keys(config: Config) -> tuple[str, ...]
```

**Purpose**: Finds extension-required provider keys that are not currently available. It warns developers about features that will fail later because a needed API key is missing.

**Data flow**: It loads extension manifests, collects the environment variable names they declare as deploy keys, then compares them against `.env` and the current process environment. It returns a sorted tuple of missing names.

**Call relations**: `init` calls this after onboarding so the command can finish successfully while still telling the user what to add before using key-dependent features.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 1 external calls (load_manifests).


##### `_write_dev_secrets`  (lines 201–223)

```
def _write_dev_secrets(config: Config) -> tuple[str, ...]
```

**Purpose**: Creates local development secrets needed for a zero-configuration server run, without overwriting anything already supplied. These include encryption and token-signing secrets.

**Data flow**: It generates candidate secret values, reads any existing `.env`, checks both `.env` and current environment variables, and writes only missing names. It also puts newly written values into the current process environment and returns their names.

**Call relations**: `init` uses this before onboarding because later steps need secrets such as the bearer-token signing key.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 2 external calls (generate_key, token_urlsafe).


##### `_onboard`  (lines 226–245)

```
async def _onboard(config: Config, email: str, model: str) -> Onboarded
```

**Purpose**: Creates the first workspace, owner member, default agent, model setup, and extension onboarding records. It keeps the database open only for the onboarding operation and closes it afterward.

**Data flow**: It initializes the database connection, optionally builds an encrypted credential store from the configured key, creates an `Onboarding` object with config, email, model, credentials, and extension manifests, then runs core creation followed by extension steps. It returns the onboarding result.

**Call relations**: `init` calls this after migrations are applied. It hands most detailed creation work to the onboarding subsystem and ensures database cleanup even on failure.

*Call graph*: called by 1 (init); 6 external calls (__init__, __init__, Fernet, dispose_db, init_db, load_manifests).


##### `_create_postgres_system_database`  (lines 248–259)

```
async def _create_postgres_system_database(config: Config) -> None
```

**Purpose**: Ensures the extra PostgreSQL database used for system or durable workflow state exists. This avoids a later startup failure when the app expects that database to be present.

**Data flow**: It converts the configured async database URL into a form `asyncpg` can connect to, extracts the system database name, checks PostgreSQL's database list, and creates the database if missing. It always closes the connection.

**Call relations**: `init` calls this only for PostgreSQL-backed setups before migrations and onboarding continue.

*Call graph*: called by 1 (init); 1 external calls (connect).


##### `migrate`  (lines 263–280)

```
def migrate() -> None
```

**Purpose**: Brings the database schema up to date. It applies core migrations and migrations from installed extensions.

**Data flow**: It loads config, chooses the owner database URL from the environment when provided or the normal config URL otherwise, runs migrations for the active pack, and prints confirmation.

**Call relations**: Operators run this after installing extensions or during deployment jobs. It delegates the schema work to the database migration subsystem.

*Call graph*: 3 external calls (echo, load_config, apply_migrations).


##### `serve`  (lines 284–293)

```
def serve() -> None
```

**Purpose**: Starts the main UFO runtime: web surfaces, workers, jobs, and related service endpoints. It also prints the portal URL when the installed pack provides one.

**Data flow**: It loads config and extension manifests, asks which browser surface is the home portal, prints a helpful URL if one exists, then starts the server run loop. This command does not return until the server stops.

**Call relations**: This is the local runtime command. It uses `_serve_base` for the displayed URL and then hands execution to `ufo.serve.run`.

*Call graph*: calls 1 internal fn (_serve_base); 5 external calls (echo, load_config, load_manifests, home_surface, run).


##### `portal`  (lines 297–315)

```
def portal() -> None
```

**Purpose**: Opens the browser portal and signs it in using this machine's saved CLI token. It saves the user from copying tokens by hand.

**Data flow**: It loads config and manifests, finds the home surface, reads the saved token, checks that the local server is reachable, and starts a browser handoff. If anything is missing, it raises a clear command error.

**Call relations**: This command is normally used after `init` and `serve`. It uses `_serve_base`, `_ufoctl_dir`, and `BrowserHandoff` to complete browser sign-in.

*Call graph*: calls 2 internal fn (_serve_base, _ufoctl_dir); 7 external calls (__init__, ClickException, echo, get, load_config, load_manifests, home_surface).


##### `_serve_base`  (lines 318–319)

```
def _serve_base(config: Config) -> str
```

**Purpose**: Builds the base local server URL from the configured host and port. It keeps URL formatting consistent between commands.

**Data flow**: It reads `serve.host` and `serve.port` from the config object and returns a string like `http://host:port`.

**Call relations**: `serve` uses it when printing the portal hint, and `portal` uses it when checking and opening the browser portal.

*Call graph*: called by 2 (portal, serve).


##### `BrowserHandoff.open`  (lines 334–342)

```
def open(self) -> None
```

**Purpose**: Safely transfers the CLI bearer token into the browser as a portal login. It uses a temporary local web page instead of putting the token in a URL.

**Data flow**: It creates an unguessable local path, starts a one-off HTTP listener on localhost, opens the browser to that path, and serves requests until the token page has been delivered. If the browser cannot be opened automatically, it prints the local URL.

**Call relations**: `portal` creates a `BrowserHandoff` and calls this method after verifying the server is reachable. This method relies on `_responder` to build the temporary web handler.

*Call graph*: calls 1 internal fn (_responder); 5 external calls (echo, HTTPServer, token_urlsafe, Event, open).


##### `BrowserHandoff._responder`  (lines 344–361)

```
def _responder(self, path: str, delivered: threading.Event) -> type[BaseHTTPRequestHandler]
```

**Purpose**: Builds the small HTTP request handler used during browser sign-in. The handler only serves the generated login page at one secret path.

**Data flow**: It receives the allowed path and a delivery event, renders the HTML page once, and returns a custom request-handler class. That class can serve the page and mark the handoff complete.

**Call relations**: `BrowserHandoff.open` asks this method for a handler before starting its temporary localhost server. The returned handler calls back to `_page` indirectly through the prebuilt page content.

*Call graph*: calls 1 internal fn (_page); called by 1 (open).


##### `BrowserHandoff._responder.do_GET`  (lines 348–357)

```
def do_GET(self) -> None
```

**Purpose**: Responds to the browser's one GET request during token handoff. It serves the login page only if the request path matches the secret path.

**Data flow**: It reads the incoming request path. A wrong path gets a 404 error; the correct path gets an HTML response containing the auto-submitting login form, and the delivery event is set.

**Call relations**: This method is invoked by Python's HTTP server inside `BrowserHandoff.open`. Setting the event tells the outer loop that the handoff can shut down.


##### `BrowserHandoff._responder.log_message`  (lines 359–359)

```
def log_message(self, *args: object) -> None
```

**Purpose**: Suppresses default HTTP server logging for the temporary handoff server. This keeps token handoff quiet and avoids noisy terminal output.

**Data flow**: It accepts the usual log arguments but deliberately does nothing. No value is produced and no state changes.

**Call relations**: Python's HTTP server calls this when it would normally log a request. The custom responder overrides it so `portal` output stays clean.


##### `BrowserHandoff._page`  (lines 363–370)

```
def _page(self) -> str
```

**Purpose**: Creates the tiny HTML page that posts the CLI token to the portal as a normal sign-in form. The form auto-submits but still includes a button as a fallback.

**Data flow**: It reads the portal URL and token from the `BrowserHandoff` object, escapes them for safe HTML, and returns a complete HTML string.

**Call relations**: `_responder` calls this while preparing the temporary HTTP handler that `open` serves to the browser.

*Call graph*: called by 1 (_responder); 1 external calls (escape).


##### `ingress`  (lines 374–376)

```
def ingress() -> None
```

**Purpose**: Starts the sandbox ingress service, a token-protected reverse proxy to sandbox ports. In plain terms, it is a controlled doorway into per-conversation sandbox services.

**Data flow**: It receives no options and hands control directly to the ingress server runner. The runner owns the long-running network work.

**Call relations**: This is a separate CLI command under `main`. It delegates completely to `ufo.ingress_serve.run`.

*Call graph*: 1 external calls (run).


##### `spend_cap`  (lines 380–381)

```
def spend_cap() -> None
```

**Purpose**: Defines the `spend-cap` command group. Its subcommands let operators read or set spending limits.

**Data flow**: It performs no data transformation itself. Click uses it as a container for related commands.

**Call relations**: `spend_cap_set` and `spend_cap_list` live under this group, so users run them as `ufoctl spend-cap set` and `ufoctl spend-cap list`.


##### `spend_cap_set`  (lines 392–410)

```
def spend_cap_set(scope: str, subject_id: str, window_seconds: int, limit_micro_usd: int, on_breach: str) -> None
```

**Purpose**: Creates or updates a spending cap for the workspace, a member, or an agent. A cap limits allowed cost over a time window and says whether excess work is parked or rejected.

**Data flow**: It validates that `--subject-id` is present only when needed, converts it to a UUID when supplied, loads config, writes the cap asynchronously, converts micro-dollars to dollars for display, and prints the result.

**Call relations**: This command is the user-facing wrapper around `_write_spend_cap`, which performs the database insert or update.

*Call graph*: calls 1 internal fn (_write_spend_cap); 5 external calls (run, ClickException, echo, load_config, UUID).


##### `spend_cap_list`  (lines 414–424)

```
def spend_cap_list() -> None
```

**Purpose**: Shows all spend caps currently set for the workspace. It gives operators a quick view of active cost controls.

**Data flow**: It loads config, reads cap rows asynchronously, and prints either a no-cap message or one formatted line per cap with scope, target, limit, window, and breach behavior.

**Call relations**: This command delegates database reading to `_read_spend_caps` and focuses on formatting the result for the terminal.

*Call graph*: calls 1 internal fn (_read_spend_caps); 3 external calls (run, echo, load_config).


##### `_write_spend_cap`  (lines 427–481)

```
async def _write_spend_cap(config: Config, scope: str, subject: UUID | None, window_seconds: int, limit_micro_usd: int, on_breach: str) -> UUID
```

**Purpose**: Writes a spend cap to the database, updating an existing matching cap rather than creating duplicates. Matching is based on workspace, scope, subject, and time window.

**Data flow**: It opens the database, finds the workspace id, checks whether a cap with the same target and window exists, updates limit and breach behavior if found, or inserts a new cap with a fresh UUID. It returns the cap id and closes the database.

**Call relations**: `spend_cap_set` calls this after validating command-line input. It uses the workspace transaction helper so the write happens in the current workspace context.

*Call graph*: called by 1 (spend_cap_set); 7 external calls (insert, select, update, dispose_db, init_db, workspace_tx, uuid4).


##### `_read_spend_caps`  (lines 484–510)

```
async def _read_spend_caps(config: Config) -> list[tuple[UUID, str, UUID | None, int, int, str]]
```

**Purpose**: Reads all spend caps for the current workspace. It returns raw cap details for display by the CLI.

**Data flow**: It opens the database, finds the workspace id, selects cap fields ordered by scope, converts database rows into tuples, and closes the database.

**Call relations**: `spend_cap_list` calls this and then turns the returned tuples into human-readable terminal lines.

*Call graph*: called by 1 (spend_cap_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `balance`  (lines 514–515)

```
def balance() -> None
```

**Purpose**: Defines the `balance` command group. Its subcommands show, credit, and reserve prepaid workspace balance.

**Data flow**: It does not read or write balance data itself. It exists so Click can route related commands under one heading.

**Call relations**: `balance_show`, `balance_credit`, and `balance_reserve` are registered under this group.


##### `balance_show`  (lines 520–535)

```
def balance_show(workspace_id: str) -> None
```

**Purpose**: Displays the workspace's prepaid balance, reserve requirement, lifetime grants, charges, and last purchase time. This helps operators understand whether new turns can start.

**Data flow**: It loads config, reads the selected workspace balance, and prints either `no balance` or formatted dollar amounts converted from micro-dollars.

**Call relations**: It delegates the database work to `_read_balance`, which uses the shared balance-scoping helper.

*Call graph*: calls 1 internal fn (_read_balance); 3 external calls (run, echo, load_config).


##### `balance_credit`  (lines 543–556)

```
def balance_credit(granted_micro_usd: int, charged_micro_usd: int, reference: str, workspace_id: str) -> None
```

**Purpose**: Adds prepaid value to a workspace once per reference key. The reference makes the operation safe to retry without double-crediting.

**Data flow**: It rejects a zero grant, loads config, calls the async credit helper with granted amount, charged amount, and reference, then prints whether a new credit was applied or was already present.

**Call relations**: It is the terminal-facing wrapper around `_credit_balance`, which calls the balance subsystem's idempotent credit function.

*Call graph*: calls 1 internal fn (_credit_balance); 4 external calls (run, ClickException, echo, load_config).


##### `balance_reserve`  (lines 562–570)

```
def balance_reserve(micro_usd: int, workspace_id: str) -> None
```

**Purpose**: Sets the minimum balance headroom required before a turn may begin. This prevents work from starting when too little prepaid budget remains.

**Data flow**: It rejects negative reserve values, loads config, calls the async reserve helper, and prints the new reserve or raises an error if there is no balance record yet.

**Call relations**: It delegates to `_set_reserve`, which opens the right workspace scope and calls the balance subsystem.

*Call graph*: calls 1 internal fn (_set_reserve); 4 external calls (run, ClickException, echo, load_config).


##### `_target_workspace`  (lines 573–595)

```
async def _target_workspace(named: str) -> UUID
```

**Purpose**: Chooses which workspace an operator command should affect. If the user names one, it verifies it exists; otherwise it only auto-selects when there is exactly one workspace.

**Data flow**: It reads across workspaces through an owner-level database transaction. It returns the requested or sole workspace UUID, or raises a clear error if none, many, or a missing named workspace is found.

**Call relations**: `_balance_scope` and `_seed_target` call this before doing workspace-specific work, especially in hosted deployments where guessing would be unsafe.

*Call graph*: called by 2 (_balance_scope, _seed_target); 4 external calls (ClickException, select, owner_tx, UUID).


##### `_balance_scope`  (lines 599–616)

```
async def _balance_scope(config: Config, named: str) -> AsyncIterator[tuple[AsyncConnection, UUID]]
```

**Purpose**: Opens a database transaction for the one workspace a balance command targets. It makes sure hosted multi-workspace deployments use the proper owner lookup and then the proper workspace context.

**Data flow**: It initializes database pools, optionally initializes the owner database pool, resolves the workspace id, binds the workspace context, yields a workspace transaction and id, and finally disposes database connections.

**Call relations**: `_read_balance`, `_credit_balance`, and `_set_reserve` all use this shared context manager so balance commands resolve workspaces consistently.

*Call graph*: calls 1 internal fn (_target_workspace); called by 3 (_credit_balance, _read_balance, _set_reserve); 5 external calls (dispose_db, init_db, init_owner_db, workspace_tx, ws).


##### `_read_balance`  (lines 619–621)

```
async def _read_balance(config: Config, named: str) -> Balance | None
```

**Purpose**: Reads the current balance record for the selected workspace. It is a thin bridge between the CLI and the balance subsystem.

**Data flow**: It enters `_balance_scope`, receives a database connection and workspace id, asks `read_balance` for the balance, and returns either a balance object or `None`.

**Call relations**: `balance_show` calls this and handles printing the returned information.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_show); 1 external calls (read_balance).


##### `_credit_balance`  (lines 624–630)

```
async def _credit_balance(config: Config, named: str, granted_micro_usd: int, charged_micro_usd: int, reference: str) -> bool
```

**Purpose**: Applies a balance credit for the selected workspace. It keeps the workspace-selection details out of the visible CLI command.

**Data flow**: It enters `_balance_scope`, passes the connection, workspace id, amounts, and reference key to the balance subsystem, and returns `True` if a new credit was applied or `False` if the reference was already used.

**Call relations**: `balance_credit` calls this after validating command input.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_credit); 1 external calls (credit).


##### `_set_reserve`  (lines 633–635)

```
async def _set_reserve(config: Config, named: str, reserve_micro_usd: int) -> bool
```

**Purpose**: Updates the reserve amount for the selected workspace. The reserve is the required spending headroom before work can start.

**Data flow**: It enters `_balance_scope`, passes the connection, workspace id, and reserve amount to the balance subsystem, and returns whether a balance row existed to update.

**Call relations**: `balance_reserve` calls this and turns the boolean result into either a success message or a command error.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_reserve); 1 external calls (set_reserve).


##### `spend`  (lines 643–666)

```
def spend(window_seconds: int) -> None
```

**Purpose**: Prints a spend report for a recent time window. It shows the total and several breakdowns so operators can see where money was used.

**Data flow**: It loads config, reads a spend report for the requested window, converts micro-dollars to dollars, and prints totals by dimension, member, agent, origin, and price digest.

**Call relations**: This command delegates calculation and database access to `_read_spend`; it handles only option reading and terminal formatting.

*Call graph*: calls 1 internal fn (_read_spend); 3 external calls (run, echo, load_config).


##### `_read_spend`  (lines 669–676)

```
async def _read_spend(config: Config, window_seconds: int) -> SpendReport
```

**Purpose**: Builds the spend rollup for the current workspace and time window. A rollup is a summary made from detailed ledger entries.

**Data flow**: It initializes the database, finds the workspace id, asks `SpendRollup` to read the report for the time window, returns that report, and closes the database.

**Call relations**: `spend` calls this before printing the report sections.

*Call graph*: called by 1 (spend); 5 external calls (__init__, select, dispose_db, init_db, workspace_tx).


##### `transcript_reads`  (lines 684–697)

```
def transcript_reads(limit: int) -> None
```

**Purpose**: Lists recent recorded disclosures where an admin read another member's private transcript. This gives operators an audit view of sensitive transcript access.

**Data flow**: It validates that the limit is positive, loads config, reads disclosure rows, and prints either a no-records message or one line per access with time, reader, subject, and conversation id.

**Call relations**: It delegates the database query to `_read_transcript_accesses` and formats the results for humans.

*Call graph*: calls 1 internal fn (_read_transcript_accesses); 4 external calls (run, ClickException, echo, load_config).


##### `_read_transcript_accesses`  (lines 700–734)

```
async def _read_transcript_accesses(config: Config, limit: int) -> list[tuple[str, str, UUID, datetime]]
```

**Purpose**: Reads the newest transcript-access disclosure records for the current workspace. It joins member records so the CLI can show email addresses instead of only ids.

**Data flow**: It opens the database, aliases the member table as reader and subject, selects disclosure rows for the workspace ordered newest first, limits the result count, converts rows to tuples, and closes the database.

**Call relations**: `transcript_reads` calls this after validating the requested limit.

*Call graph*: called by 1 (transcript_reads); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `grants`  (lines 738–750)

```
def grants() -> None
```

**Purpose**: Lists OAuth accounts that have been granted to agents. OAuth is the common web sign-in permission flow used to let an app access an account without seeing the password.

**Data flow**: It loads config, reads grant summaries, and prints either `no grants` or a table-like list showing agent, provider, account, whether it is shared or private, and grant date.

**Call relations**: It calls `_read_grants`, which gathers the workspace id and asks the grants subsystem for summaries.

*Call graph*: calls 1 internal fn (_read_grants); 3 external calls (run, echo, load_config).


##### `_read_grants`  (lines 753–760)

```
async def _read_grants(config: Config) -> tuple[GrantSummary, ...]
```

**Purpose**: Fetches grant summaries for the current workspace. It returns the compact records needed by the CLI output.

**Data flow**: It initializes the database, reads the workspace id, then asks `workspace_grant_summaries` for that workspace. It returns the summaries and closes database connections.

**Call relations**: `grants` calls this and handles the display.

*Call graph*: called by 1 (grants); 5 external calls (select, dispose_db, init_db, workspace_tx, workspace_grant_summaries).


##### `credential`  (lines 764–766)

```
def credential() -> None
```

**Purpose**: Defines the `credential` command group for encrypted bring-your-own-key credential slots. These slots are secrets that extensions declare they need.

**Data flow**: It performs no credential operation itself. It groups the set and list subcommands for Click.

**Call relations**: `credential_set` and `credential_list` are attached under this group.


##### `credential_set`  (lines 771–794)

```
def credential_set(slot: str) -> None
```

**Purpose**: Stores one declared credential secret, such as an API key, without exposing it in the command line. It only allows slots that extensions say a member or operator may fill.

**Data flow**: It loads config, checks that the slot is declared and fillable, requires the encryption key environment variable, reads the secret from a hidden prompt or stdin, rejects empty values, writes the encrypted credential, and prints confirmation.

**Call relations**: It uses `_declared_slots` and `_fillable_slots` for validation, then delegates encrypted storage to `_write_credential`.

*Call graph*: calls 3 internal fn (_declared_slots, _fillable_slots, _write_credential); 5 external calls (run, ClickException, echo, prompt, load_config).


##### `credential_list`  (lines 798–808)

```
def credential_list() -> None
```

**Purpose**: Shows each declared credential slot and whether it has been set. It never reads or prints the secret values.

**Data flow**: It loads config, gets declared slots from extension manifests, reads stored slot names from the database, and prints each slot with its owning extension and set/unset status.

**Call relations**: It combines `_declared_slots` with `_read_stored_slots` to produce a safe inventory.

*Call graph*: calls 2 internal fn (_declared_slots, _read_stored_slots); 3 external calls (run, echo, load_config).


##### `_declared_slots`  (lines 811–816)

```
def _declared_slots(config: Config) -> dict[str, str]
```

**Purpose**: Builds a map of credential slot names to the extension that declared them. This is the authority for which credential names are valid.

**Data flow**: It loads manifests for the configured pack, walks their credential declarations, and returns a dictionary from slot name to manifest name. Manifest loading errors become Click-friendly errors.

**Call relations**: Both credential commands use this so listing and setting refer to the same extension declarations.

*Call graph*: called by 2 (credential_list, credential_set); 2 external calls (ClickException, load_manifests).


##### `_fillable_slots`  (lines 819–828)

```
def _fillable_slots(config: Config) -> frozenset[str]
```

**Purpose**: Finds which declared credential slots may be typed in by an operator or member. Some slots are written by the deploy itself and should not accept manual input.

**Data flow**: It loads extension manifests, filters credential declarations to those marked member-fillable, and returns their names as an immutable set. Manifest errors become command errors.

**Call relations**: `credential_set` calls this after confirming a slot exists, preventing manual writes to non-fillable slots.

*Call graph*: called by 1 (credential_set); 2 external calls (ClickException, load_manifests).


##### `_write_credential`  (lines 831–838)

```
async def _write_credential(config: Config, key: str, slot: str, value: str) -> None
```

**Purpose**: Encrypts and stores one credential value for the current workspace. Encryption at rest means the database does not hold the plain secret.

**Data flow**: It initializes the database, finds the workspace id, creates a `CredentialStore` with a Fernet encryption key, writes the slot value, and closes the database.

**Call relations**: `credential_set` calls this after reading and validating the secret value.

*Call graph*: called by 1 (credential_set); 6 external calls (__init__, Fernet, select, dispose_db, init_db, workspace_tx).


##### `_read_stored_slots`  (lines 841–855)

```
async def _read_stored_slots(config: Config) -> frozenset[str]
```

**Purpose**: Reads which credential slots currently have stored values. It returns only slot names, not secret contents.

**Data flow**: It opens the database, finds the workspace id, selects credential slot names for that workspace, converts them to an immutable set, and closes the database.

**Call relations**: `credential_list` calls this to decide whether each declared slot should be shown as set or unset.

*Call graph*: called by 1 (credential_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `ext`  (lines 859–860)

```
def ext() -> None
```

**Purpose**: Defines the `ext` command group for extension discovery and installation. Extensions add features to a pack.

**Data flow**: It performs no store operation itself. It groups search, install, and remove commands.

**Call relations**: `ext_search`, `ext_install`, and `ext_remove` are registered under this group.


##### `_store`  (lines 863–866)

```
def _store(config: Config) -> ExtensionStore
```

**Purpose**: Creates an extension store object for the current config. The store knows the catalog of available extensions and the lockfile of installed ones.

**Data flow**: It checks that extension-store config is enabled, reads the catalog, locates the lockfile, and returns an `ExtensionStore`. If no store is configured, it raises a command error.

**Call relations**: All extension subcommands call this before searching or changing installed pins.

*Call graph*: called by 3 (ext_install, ext_remove, ext_search); 4 external calls (__init__, ClickException, lockfile_path, read_catalog).


##### `ext_search`  (lines 871–884)

```
def ext_search(query: str) -> None
```

**Purpose**: Searches the configured extension catalog and shows matching extensions. It marks whether each one is installed, available, or bundle-only.

**Data flow**: It loads config, creates the extension store, searches with the query text, and prints either no matches or one formatted line per listing.

**Call relations**: It relies on `_store` for catalog and lockfile access, then only formats store results.

*Call graph*: calls 1 internal fn (_store); 2 external calls (echo, load_config).


##### `ext_install`  (lines 889–895)

```
def ext_install(name: str) -> None
```

**Purpose**: Pins an extension from the store into the deploy lockfile. The next server run will load the pinned extension.

**Data flow**: It loads config, creates the extension store, asks it to install the named extension, catches store errors as command errors, and prints the installed name, version, and digest.

**Call relations**: It uses `_store` to reach the extension subsystem, which performs the actual lockfile update.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `ext_remove`  (lines 900–906)

```
def ext_remove(name: str) -> None
```

**Purpose**: Removes an extension pin from the lockfile. After the next restart, the deploy stops loading that extension.

**Data flow**: It loads config, creates the extension store, asks it to remove the named extension, converts expected failures into Click errors, and prints confirmation.

**Call relations**: It delegates lockfile editing to the store returned by `_store`.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `_ufo_project_dir`  (lines 912–923)

```
def _ufo_project_dir() -> Path
```

**Purpose**: Finds the source project directory needed to build the UFO Python wheel for a bundle. It works from the location of this file, not the user's current shell directory.

**Data flow**: It walks upward through parent folders looking for a `pyproject.toml` whose project name is `ufo`. It returns that directory or raises a command error if this is not a source checkout.

**Call relations**: `bundle` calls this before running `uv build`, so bundling from any working directory still builds the correct project.

*Call graph*: called by 1 (bundle); 3 external calls (ClickException, Path, loads).


##### `bundle`  (lines 930–946)

```
def bundle(out: Path) -> None
```

**Purpose**: Creates a runnable deployment bundle containing pinned config, extension lock information, image recipe material, and a built UFO wheel. It freezes the current deploy into something portable.

**Data flow**: It loads config, optionally reads the extension catalog, asks `Bundle` to build bundle files, runs `uv build` to produce a wheel into the bundle output directory, verifies the wheel exists, and prints the bundle path plus pinned extensions.

**Call relations**: This command ties together the bundle subsystem, project-directory discovery through `_ufo_project_dir`, and the external wheel build tool.

*Call graph*: calls 1 internal fn (_ufo_project_dir); 8 external calls (__init__, ClickException, echo, run, wheel_name, config_path, load_config, read_catalog).


##### `turn`  (lines 950–951)

```
def turn() -> None
```

**Purpose**: Defines the `turn` command group for actions on a single turn. A turn is one unit of agent work in a conversation.

**Data flow**: It does not act on data itself. It groups turn-specific operator commands.

**Call relations**: `turn_cancel` is registered under this group.


##### `turn_cancel`  (lines 956–966)

```
def turn_cancel(turn_id: str) -> None
```

**Purpose**: Cancels one turn by id, useful when work is stuck or keeps recovering without completing. It leaves already finished turns unchanged.

**Data flow**: It loads config, converts the provided turn id to a UUID, calls the async cancel helper, and prints whether the turn was cancelled or was already terminal.

**Call relations**: It delegates the careful workspace lookup and workflow cancellation to `_cancel_turn`.

*Call graph*: calls 1 internal fn (_cancel_turn); 4 external calls (run, echo, load_config, UUID).


##### `_cancel_turn`  (lines 969–991)

```
async def _cancel_turn(config: Config, turn_id: UUID) -> bool
```

**Purpose**: Cancels a turn in the workspace that owns it. It first finds the owning workspace with owner-level access, then performs the cancel inside that workspace's normal safety context.

**Data flow**: It initializes database pools, reads the turn's workspace id, raises an error if the turn is unknown, creates a replay-safe durable-workflow client, binds the workspace context, calls the cancellation subsystem, returns whether anything was cancelled, and closes database connections.

**Call relations**: `turn_cancel` calls this. It bridges database lookup, workspace scoping, durable workflow access, and the cancellation subsystem.

*Call graph*: called by 1 (turn_cancel); 9 external calls (ClickException, select, cancel_one_turn, dispose_db, init_db, init_owner_db, owner_tx, replay_safe_client, ws).


##### `seed`  (lines 995–996)

```
def seed() -> None
```

**Purpose**: Defines the `seed` command group for writing demonstration content. Seed data helps people review or develop the portal with known examples.

**Data flow**: It performs no seeding itself. It groups seed subcommands.

**Call relations**: `seed_kitchen_sink` is attached under this group.


##### `seed_kitchen_sink`  (lines 1001–1010)

```
def seed_kitchen_sink(workspace_id: str) -> None
```

**Purpose**: Writes a comprehensive demo conversation that exercises many shapes the portal can display. It prints the browser route where the conversation can be viewed.

**Data flow**: It loads config, runs the async seed helper for the optional workspace id, receives the new conversation id, and prints a portal path for it.

**Call relations**: It delegates setup and writing to `_seed_kitchen_sink`.

*Call graph*: calls 1 internal fn (_seed_kitchen_sink); 3 external calls (run, echo, load_config).


##### `_seed_kitchen_sink`  (lines 1013–1024)

```
async def _seed_kitchen_sink(config: Config, named: str) -> UUID
```

**Purpose**: Prepares database and blob storage access for writing the kitchen-sink demo conversation. Blob storage is where larger attached content can live outside normal table rows.

**Data flow**: It initializes the app database, optionally initializes the owner database, creates a workspace blob store from blob config, calls `_seed_target`, returns the conversation id, and disposes database connections.

**Call relations**: `seed_kitchen_sink` calls this. It wraps `_seed_target` with the infrastructure needed for hosted workspace lookup and blob writing.

*Call graph*: calls 1 internal fn (_seed_target); called by 1 (seed_kitchen_sink); 5 external calls (__init__, blob_store_for, dispose_db, init_db, init_owner_db).


##### `_seed_target`  (lines 1027–1058)

```
async def _seed_target(blob: WorkspaceBlobStore, named: str) -> UUID
```

**Purpose**: Writes the actual kitchen-sink demo into a resolved workspace. It finds the main agent and first member, then asks the seed subsystem to create the conversation.

**Data flow**: It resolves the target workspace, binds that workspace context, reads the main agent id and earliest member id/email, errors if no member exists, constructs a `KitchenSink` writer, and returns the conversation id it creates.

**Call relations**: `_seed_kitchen_sink` calls this after database and blob setup. It uses `_target_workspace` for safe workspace selection and hands final content creation to `KitchenSink.write`.

*Call graph*: calls 1 internal fn (_target_workspace); called by 1 (_seed_kitchen_sink); 5 external calls (__init__, ClickException, select, workspace_tx, ws).


### `core/src/ufo/bundle.py`

`orchestration` · `bundle/build time`

This file supports the `ufoctl bundle` command. A bundle is like packing a moving box for a service: it copies the current configuration, writes down the exact extensions that must be present, and creates instructions for building a container image that can run it later.

The important idea is pinning. A pin records an extension by name and verified digest, so the bundle does not just say “use this extension”; it says “use this exact extension content.” That prevents a future machine from accidentally running a different version. If there is already a lockfile, the bundle starts from the extensions listed there. If there is no lockfile, it uses all extensions currently discovered in the environment. If an extension catalog is available, it also adds entries marked as disabled or “bundle-only,” meaning they are installed into the bundle rather than loaded dynamically at runtime.

`Bundle.build` creates the output directory, copies the user config into it, writes a new lockfile with the pinned extensions and current UFO version, and writes a Dockerfile. The Dockerfile installs a locally built UFO wheel file, copies in the config and lockfile, and starts `ufoctl serve` by default. Without this file, deployments would be harder to reproduce: another machine might miss an extension, use a different one, or lack the exact startup files needed.

#### Function details

##### `wheel_name`  (lines 25–28)

```
def wheel_name() -> str
```

**Purpose**: Builds the expected filename of the UFO Python wheel that the Dockerfile will install. A wheel is a packaged Python distribution, and here it is expected to sit next to the Docker build context because UFO is not fetched from a public package index.

**Data flow**: It reads the current UFO version from the extension store helper, places that version into a standard wheel filename pattern, and returns the resulting string, such as a versioned `.whl` filename.

**Call relations**: When `Bundle._dockerfile` writes the Dockerfile text, it asks `wheel_name` for the exact file to copy and install. This keeps the Dockerfile aligned with the same UFO version used elsewhere in the bundle.

*Call graph*: called by 1 (_dockerfile); 1 external calls (ufo_version).


##### `Bundle.build`  (lines 49–62)

```
def build(self) -> BundleResult
```

**Purpose**: Creates the complete bundle folder on disk. It gathers extension pins, copies the current config, writes a lockfile, writes a Dockerfile, and returns a summary of what it produced.

**Data flow**: It starts with the bundle settings: the source config path, optional extension catalog, and output folder. It asks `_pins` for the exact extensions to freeze, creates the output directory, copies the config text, writes a JSON lockfile containing the current UFO version and pins, writes Dockerfile text from `_dockerfile`, and returns a `BundleResult` containing the paths and pins.

**Call relations**: This is the main worker for the bundling flow. It calls `_pins` first so the artifact knows exactly what extensions it depends on, then calls `_dockerfile` so the artifact knows how to become a runnable container image. The returned `BundleResult` gives the caller a simple record of the files that were created.

*Call graph*: calls 2 internal fn (_dockerfile, _pins); 3 external calls (__init__, __init__, ufo_version).


##### `Bundle._pins`  (lines 64–78)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: Decides which extensions must be frozen into the bundle and verifies each one by turning its name into a pin. This is what makes the bundle reproducible instead of relying on whatever extensions happen to exist later.

**Data flow**: It looks at the currently discovered installed extensions and checks whether a lockfile already exists. If a lockfile exists, it starts from the extension names already locked; otherwise it starts from all discovered extensions. If a catalog is available, it adds catalog entries marked disabled, because those are meant to be included at bundle time. It removes duplicate names while keeping order, then asks `pin_for` to create a verified pin for each name, and returns all pins as a tuple.

**Call relations**: `Bundle.build` calls this before writing the lockfile. This function relies on the extension loader to discover installed extensions and read any existing lockfile, then relies on the store to produce verified pins. If an extension cannot be pinned, the bundle cannot honestly claim it contains a reproducible setup.

*Call graph*: called by 1 (build); 4 external calls (discovered, lockfile_path, read_lockfile, pin_for).


##### `Bundle._dockerfile`  (lines 80–94)

```
def _dockerfile(self) -> str
```

**Purpose**: Creates the text of the Dockerfile that will build a runnable UFO container image from the bundle folder.

**Data flow**: It uses fixed bundle filenames, the configured Python base image, and the wheel filename from `wheel_name`. It returns a multi-line Dockerfile string that sets the working directory, points UFO at the bundled config and lockfile, copies and installs the UFO wheel, copies the config and lockfile into the image, and sets the default command to run `ufoctl serve`.

**Call relations**: `Bundle.build` calls this when it is ready to write the Dockerfile into the output directory. Inside the generated Dockerfile, the wheel name supplied by `wheel_name` connects the container build step to the exact UFO package produced beside the bundle context.

*Call graph*: calls 1 internal fn (wheel_name); called by 1 (build).


### `core/src/ufo/onboarding.py`

`orchestration` · `startup / first-run initialization`

This file is the safe “first day” checklist for the system. When someone runs the initial setup command, UFO needs to create durable things that should exist exactly once: a workspace, an admin member, and the main assistant agent. If this file did not guard that process, setup could accidentally create duplicate workspaces or users, or create a half-working system without the model or credential keys needed later.

The flow is deliberately split into two parts. First, the core setup checks that the selected AI model has its required environment variable, meaning a secret value supplied outside the program, such as an API key. It also checks whether extension setup steps need a credential store key. Only after these checks pass does it open a database transaction, which is a “do all of this or none of it” database boundary. Inside it, the code refuses to continue if any member already exists, then creates the workspace, the first admin member, and the default main agent.

After the core workspace exists, extension-related setup runs. Each extension gets its own scoped context, like giving each add-on its own labeled toolbox. If an extension step fails, the failure is logged but does not undo the core workspace or block other extensions. That makes first-run setup sturdy: the essential system is created once, while optional add-ons get a chance to prepare themselves.

#### Function details

##### `run_onboarding_steps`  (lines 47–75)

```
async def run_onboarding_steps(manifests: tuple[Manifest, ...], workspace_id: UUID, credentials: CredentialStore | None) -> None
```

**Purpose**: Runs the setup steps supplied by installed extensions after the core workspace has already been created. It gives each extension its own context and makes sure one failing extension does not stop the rest.

**Data flow**: It receives the installed extension manifests, the new workspace id, and an optional credential store. It enters the workspace context, checks each manifest for onboarding steps, creates a scoped extension context when credentials are available, and calls each step. It returns nothing, but it may cause extension setup work to happen and logs skipped or failed steps.

**Call relations**: This is called by Onboarding.run_steps after the main workspace and agent setup has succeeded. It uses the workspace context so the steps run for the right workspace, asks context_for to build each extension’s limited view of credentials, and sends problems to the logging system instead of raising them further.

*Call graph*: called by 1 (run_steps); 3 external calls (context_for, log, ws).


##### `Onboarding.run`  (lines 90–93)

```
async def run(self) -> Onboarded
```

**Purpose**: Runs the whole first-run onboarding process from start to finish. It creates the core workspace first, then runs provisioning and extension setup.

**Data flow**: It starts with the Onboarding object’s stored config, email, model, credentials, and extension manifests. It calls create to build the core database records, then passes the resulting workspace and member ids to run_steps. It returns an Onboarded value containing the new workspace id and admin member id.

**Call relations**: This is the top-level method for this file’s flow. It delegates the durable core setup to Onboarding.create, then delegates add-on and provisioning work to Onboarding.run_steps, keeping the overall order clear and safe.

*Call graph*: calls 2 internal fn (create, run_steps).


##### `Onboarding.create`  (lines 95–101)

```
async def create(self) -> Onboarded
```

**Purpose**: Creates the essential first-run database state, but only after checking that required secrets are available. This is the part that must succeed before the command can bind the new admin identity.

**Data flow**: It reads the configured model, credentials, manifests, and config from the Onboarding object. It first checks for the model key, then checks whether extension steps require a credential key, and finally creates the workspace records in the database. It returns an Onboarded object with the newly created workspace and admin member ids.

**Call relations**: Onboarding.run calls this before any extension setup. It uses _require_model_key and _require_credentials_for_steps as early safety checks, then calls _create_workspace to do the actual database creation.

*Call graph*: calls 3 internal fn (_create_workspace, _require_credentials_for_steps, _require_model_key); called by 1 (run).


##### `Onboarding.run_steps`  (lines 103–105)

```
async def run_steps(self, onboarded: Onboarded) -> None
```

**Purpose**: Runs the post-creation setup that depends on an existing workspace. This includes provisioning agents for extensions and running each extension’s onboarding steps.

**Data flow**: It receives an Onboarded object that contains the workspace id. It applies agent provisioning for the installed manifests, then passes the manifests, workspace id, and credential store to run_onboarding_steps. It returns nothing, but it can create extension-related agent records and perform extension setup actions.

**Call relations**: Onboarding.run calls this after Onboarding.create has returned a valid workspace. It hands off to AgentProvisioning for extension agent setup, then to run_onboarding_steps for the extensions’ own custom first-run work.

*Call graph*: calls 1 internal fn (run_onboarding_steps); called by 1 (run); 1 external calls (__init__).


##### `Onboarding._require_credentials_for_steps`  (lines 107–118)

```
def _require_credentials_for_steps(self) -> None
```

**Purpose**: Checks whether extension onboarding steps need a credential store key before any database records are created. This prevents a half-created workspace when an extension step would later need secrets it cannot store.

**Data flow**: It looks at the Onboarding object’s credential store and installed manifests. If a credential store exists, it allows setup to continue. If no credential store exists but any extension has onboarding steps, it raises an error explaining which environment setting is needed; otherwise it returns without changing anything.

**Call relations**: Onboarding.create calls this as an early guard before _create_workspace. It does not call other project functions; its job is to stop the flow early when extension setup would be impossible.

*Call graph*: called by 1 (create).


##### `Onboarding._require_model_key`  (lines 120–127)

```
def _require_model_key(self) -> None
```

**Purpose**: Checks that the selected AI model has its required environment variable set before the first workspace is created. This avoids creating a system that cannot run its first assistant turn.

**Data flow**: It asks _model_key_env which environment variable, if any, the selected model needs. If no variable is required, it lets setup continue. If a variable name is returned but that variable is missing or empty in the process environment, it raises an error; otherwise it returns normally.

**Call relations**: Onboarding.create calls this before touching the database. It relies on _model_key_env to find the correct key name, then uses the operating system environment as the source of truth.

*Call graph*: calls 1 internal fn (_model_key_env); called by 1 (create).


##### `Onboarding._model_key_env`  (lines 129–132)

```
def _model_key_env(self) -> str | None
```

**Purpose**: Finds the name of the environment variable that should hold the API key for the chosen model, when UFO can know that name during setup.

**Data flow**: It reads the Onboarding object’s config, installed manifests, and chosen model name. It builds or consults the model registry, which is the system’s catalog of available model providers, and asks it for the key environment variable. It returns that variable name, or None if setup cannot or does not need to check one eagerly.

**Call relations**: Onboarding._require_model_key calls this as its lookup step. It delegates the provider-specific knowledge to model_registry instead of hard-coding model key rules in onboarding.

*Call graph*: called by 1 (_require_model_key); 1 external calls (model_registry).


##### `Onboarding._create_workspace`  (lines 134–160)

```
async def _create_workspace(self) -> Onboarded
```

**Purpose**: Creates the actual first workspace, first admin member, and default main agent in the database. It also protects against running initialization twice.

**Data flow**: It opens a workspace database transaction, then checks whether any member already exists. If one does, it raises AlreadyInitialized. If not, it generates new ids, inserts a workspace row, creates the first admin member using the supplied email, inserts the main agent with the default name, prompt, icon, and chosen model, and returns an Onboarded object with the workspace and member ids.

**Call relations**: Onboarding.create calls this only after the required-key checks have passed. It uses workspace_tx for the database transaction, SQLAlchemy insert and select helpers for database statements, create_member for the first admin seat, uuid4 for new identifiers, and returns the result that Onboarding.run later passes into run_steps.

*Call graph*: called by 1 (create); 7 external calls (__init__, __init__, insert, select, workspace_tx, create_member, uuid4).


### Service process launch
The server entrypoint loads configuration, validates prerequisites, wires runtime components, and starts the FastAPI service.

### `core/src/ufo/serve.py`

`entrypoint` · `startup, request handling, background work, shutdown`

This is the service’s main assembly room. A single running process can serve many workspaces, so the biggest danger is mixing one workspace’s data with another’s. This file prevents that by choosing all shared components at startup, then making each incoming request prove which workspace it belongs to before database or credential access happens. Think of it like a hotel front desk: the building is shared, but every guest must show the right key before reaching a room.

The `run` function loads configuration and extension manifests, opens the database, creates the encrypted credential store, records this server instance as available, starts a heartbeat, and builds the main runtime: blob storage, model registry, memory search, connectors, sandboxes, live update hub, and DBOS workflow client. DBOS is the durable workflow system used for long-running jobs that must survive process crashes.

The file also mounts web routes for surfaces and extensions, installs OAuth connection support, registers background jobs, and sets up egress control, which decides what sandboxed code may contact on the network. During the server lifetime, a lifespan hook runs recovery and delivery background tasks. On shutdown, the file carefully drains workflows before retiring the instance, so another process does not accidentally run the same work at the same time.

#### Function details

##### `_assert_no_reserved_routes`  (lines 184–200)

```
def _assert_no_reserved_routes(app: FastAPI) -> None
```

**Purpose**: Checks that this service has not mounted routes under URL prefixes reserved for the separate onboarding and login gateway. This prevents a confusing setup where routes appear to exist in this app but are hidden by the front-door router.

**Data flow**: It reads the FastAPI app’s route list, looks for paths beginning with reserved prefixes, and either does nothing if all is safe or raises an error listing the conflicts.

**Call relations**: Near the end of `run`, after all routers have been mounted, this function acts as a final safety inspection before the server starts accepting traffic.

*Call graph*: called by 1 (run).


##### `run`  (lines 203–384)

```
def run() -> None
```

**Purpose**: Starts the shared UFO fleet process from scratch. It is the top-level function that turns configuration and installed extensions into a running web server plus workflow workers.

**Data flow**: It reads config, environment variables, extension manifests, database settings, and credentials; builds shared services such as storage, models, jobs, sandboxes, connectors, and routes; starts DBOS and Uvicorn; and on exit shuts down workflow execution safely.

**Call relations**: This is the hub of the file. It calls the selection, validation, mounting, job-launching, proxy, and shutdown helpers in the order needed to turn an empty process into a serving instance.

*Call graph*: calls 18 internal fn (from_env, _assert_no_reserved_routes, _connect_flow, _connector_registry, _launch_jobs, _mount_ext_routes, _mount_shared_surfaces, _one_shot, _proxy_endpoint, _select_cdp_provider (+8 more)); 52 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


##### `run.invoker_for`  (lines 249–250)

```
def invoker_for(workspace_id: UUID) -> AdmissionInvoker
```

**Purpose**: Creates an admission invoker tied to one workspace. Other code uses it when it needs to admit or resume work for a specific workspace.

**Data flow**: It takes a workspace ID, combines it with the already-built shared admission object, and returns a workspace-specific invoker.

**Call relations**: Defined inside `run` so it can close over the shared admission setup. `run` passes it into runtime construction and job launching so background and surface code can create correctly scoped work.

*Call graph*: 1 external calls (__init__).


##### `_one_shot`  (lines 387–399)

```
def _one_shot(coro: Coroutine[Any, Any, T]) -> T
```

**Purpose**: Runs one asynchronous database-related operation on a temporary event loop and cleans up that loop’s database engines afterward. This avoids leaving pooled database connections attached to a loop that has already closed.

**Data flow**: It receives a coroutine, runs it to completion with `asyncio.run`, ensures loop-local database resources are disposed, and returns the coroutine’s result.

**Call relations**: `run`, `_proxy_endpoint`, and `_stop_executor` use this when they need a single async step during otherwise synchronous startup or shutdown.

*Call graph*: called by 3 (_proxy_endpoint, _stop_executor, run); 1 external calls (run).


##### `_one_shot.step`  (lines 393–397)

```
async def step() -> T
```

**Purpose**: Performs the actual awaited work for `_one_shot` and guarantees cleanup afterward. It is the small wrapper that makes the cleanup happen even if the operation fails.

**Data flow**: It awaits the coroutine passed to `_one_shot`; after success or failure, it asks the database layer to dispose engines tied to the temporary loop; on success it returns the original result.

**Call relations**: This nested helper exists only inside `_one_shot`, so all callers get the same safe cleanup behavior without repeating it.

*Call graph*: 1 external calls (dispose_loop_engines).


##### `_stop_executor`  (lines 402–416)

```
def _stop_executor(dbos: DBOS, heartbeat: Heartbeat, graceful_shutdown_seconds: int) -> None
```

**Purpose**: Shuts down DBOS workflow execution without creating duplicate work on another server. It only retires this instance’s fleet seat if no workflow is still active.

**Data flow**: It asks DBOS to drain and stop workflows, inspects the remaining active workflow set, logs and keeps the seat if work is still running, or retires the heartbeat seat if the executor is empty.

**Call relations**: `run` calls this in its `finally` block after Uvicorn stops. It hands retirement through `_one_shot` because retiring the heartbeat is asynchronous database work.

*Call graph*: calls 2 internal fn (retire, _one_shot); called by 1 (run); 2 external calls (destroy, log).


##### `_shared_owner_dsn`  (lines 419–433)

```
def _shared_owner_dsn(config: Config) -> str
```

**Purpose**: Finds the special database connection string used for owner-level cross-workspace reads. This is needed for sweeps that must first list work across all workspaces before rebinding each item safely.

**Data flow**: It reads the owner database URL from an environment variable or config; if neither exists, it raises a detailed startup error; otherwise it returns the chosen DSN string.

**Call relations**: `run` uses this before initializing the owner database engine, so missing owner access fails during startup rather than during a later background sweep.

*Call graph*: called by 1 (run).


##### `_launch_jobs`  (lines 436–501)

```
def _launch_jobs(runtime: Runtime, invoker_for: InvokerFactory, sync_driver: SyncDriver, page_feed: CorePageFeed) -> None
```

**Purpose**: Registers and starts the background jobs used by the core system and installed extensions. These jobs sync sources, dispatch turns, process page changes, recover delivery, and optionally render previews.

**Data flow**: It receives the built runtime plus sync and page-feed objects, builds probe and runner helpers, gathers core and extension job bindings, and launches a `JobRunner` with the resources jobs need.

**Call relations**: `run` calls this after runtime setup and before serving routes. It connects the durable workflow world to the runtime services such as models, blob storage, sandboxes, and credentials.

*Call graph*: called by 1 (run); 13 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, connector_clis (+3 more)).


##### `_source_backends`  (lines 504–518)

```
def _source_backends(manifests: tuple[Manifest, ...]) -> dict[str, SourceBackend]
```

**Purpose**: Builds the table of source-sync backends available to the sync driver. A source backend is the code that knows how to read from a particular kind of external source.

**Data flow**: It starts with the built-in folder source, then reads each manifest’s declared source providers, builds them with access only to that extension’s declared credentials, and returns a name-to-backend map. Duplicate backend names cause startup failure.

**Call relations**: `run` passes the returned map into `SyncDriver`, so later sync jobs can turn a source row’s backend name into the right implementation.

*Call graph*: called by 1 (run); 2 external calls (__init__, __init__).


##### `_source_identity_resolvers`  (lines 521–562)

```
def _source_identity_resolvers(manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: WorkspaceBlobStore) -> dict[str, SourceIdentityResolver]
```

**Purpose**: Builds helpers that can ask a surface extension who the current user is for source-sync purposes. This lets imported content be linked to the right external identity.

**Data flow**: It scans manifests for surfaces that declare a self-user lookup, wraps each lookup with workspace binding and safe credential access, and returns a surface-name-to-resolver map.

**Call relations**: `run` gives this map to `SyncDriver`. The nested resolver functions are called later by sync code when it needs identity information for a workspace and surface.

*Call graph*: called by 1 (run).


##### `_source_identity_resolvers.resolve`  (lines 535–559)

```
async def resolve(workspace_id: UUID, handler=surface.self_user_id, slots=declared, store=credentials) -> str | None
```

**Purpose**: Runs one surface’s self-user identity lookup inside the correct workspace. It protects the lookup so it can only read credentials the extension declared.

**Data flow**: It receives a workspace ID, creates a credential-reading helper, binds the workspace for database access, builds a `SurfaceIdentityContext`, awaits the extension’s handler, and returns the user ID or `None`.

**Call relations**: This function is created by `_source_identity_resolvers` for each eligible surface and later used by the sync driver when resolving source identity.

*Call graph*: 2 external calls (__init__, ws).


##### `_source_identity_resolvers.resolve.credential`  (lines 541–550)

```
async def credential(credential_slot: str) -> str
```

**Purpose**: Fetches one credential for the identity resolver while enforcing the extension’s declared credential list. It prevents a surface from reading arbitrary secret slots.

**Data flow**: It receives a credential slot name, checks that the slot was declared and that a credential store exists, then reads and returns the secret value for the workspace.

**Call relations**: This helper is passed indirectly through `SurfaceIdentityContext` to the surface’s identity handler, so the extension gets controlled credential access rather than direct store access.


##### `_select_hub`  (lines 565–583)

```
def _select_hub(config: Config, manifests: tuple[Manifest, ...]) -> Hub
```

**Purpose**: Chooses the live-update hub backend for this process. The hub is the channel used to move live frames or events between parts of the system.

**Data flow**: It starts with the built-in in-process hub option, adds hub builders declared by extensions, rejects duplicate names, looks up the backend named in config, and returns the built hub or raises an error if missing.

**Call relations**: `run` calls this during startup and later passes the chosen hub into the runtime, surface tailing, stop handling, and app state.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_terminal_transport`  (lines 586–628)

```
def _select_terminal_transport(config: Config, manifests: tuple[Manifest, ...], blob: FleetBlobStore) -> TerminalTransport
```

**Purpose**: Chooses how terminal sessions are connected between browsers, turns, and sandboxes. It prevents unsafe combinations where a multi-process deployment would use a terminal transport that only works inside one process.

**Data flow**: It reads terminal and hub backend config, rejects an in-process terminal transport with a cross-process hub, gathers extension-provided transport builders, checks for duplicates or unknown names, and returns the selected transport.

**Call relations**: `run` uses this while constructing `ConversationSandbox`, so sandbox terminal operations get a transport that matches the deployment shape.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_cdp_provider`  (lines 631–659)

```
def _select_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> CdpProvider | None
```

**Purpose**: Chooses the browser automation provider, if one is installed and selected. CDP means Chrome DevTools Protocol, a way for software to control a browser.

**Data flow**: It scans manifests for CDP provider specs, rejects duplicate backend names, finds the configured provider, checks that credentials are available if needed, and returns a built provider or `None`.

**Call relations**: `run` uses it to populate the runtime. `_require_cdp_provider` also calls it during extension requirement checks so missing browser support fails at startup.

*Call graph*: called by 2 (_require_cdp_provider, run); 1 external calls (__init__).


##### `_validate_requires`  (lines 662–686)

```
def _validate_requires(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Checks every active extension’s declared required seams before the server starts. A seam is a plug-in point, such as search or browser automation, that an extension depends on.

**Data flow**: It reads each manifest’s requirements, finds the corresponding check function, runs it with config, manifests, and credentials, and raises a clear error naming the extension if the requirement cannot be satisfied.

**Call relations**: `run` calls this after loading manifests and credentials. It delegates to the specific `_require_*` helpers listed in `_REQUIRED_SEAM_CHECKS`.

*Call graph*: called by 1 (run).


##### `_require_cdp_provider`  (lines 689–703)

```
def _require_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Ensures a browser automation provider is actually available when an extension requires one. Without this, the first browser tool call would fail later and less clearly.

**Data flow**: It asks `_select_cdp_provider` to resolve the configured provider and raises an error if the result is `None`.

**Call relations**: `_validate_requires` calls this for extensions that require the `cdp_providers` seam.

*Call graph*: calls 1 internal fn (_select_cdp_provider).


##### `_select_search_provider`  (lines 706–741)

```
def _select_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> SearchProvider | None
```

**Purpose**: Chooses the web or research search provider for the deployment. It can also return no provider when search is optional and not configured.

**Data flow**: It gathers search provider specs from manifests, rejects duplicates, checks the configured provider name if present, verifies credentials are available, builds the provider with scoped credential access, and returns it or `None`.

**Call relations**: `run` uses it to put search capability into the runtime. `_require_search_provider` calls it when an extension declares that search is mandatory.

*Call graph*: called by 2 (_require_search_provider, run); 2 external calls (__init__, __init__).


##### `_require_search_provider`  (lines 744–757)

```
def _require_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Ensures research search is configured and resolvable when an extension needs it. This turns a missing config value into an immediate startup error.

**Data flow**: It checks that the search-provider config value is set, then delegates to `_select_search_provider` to validate and build the selected backend.

**Call relations**: `_validate_requires` calls this for extensions that require the `search_providers` seam.

*Call graph*: calls 1 internal fn (_select_search_provider).


##### `_require_memory_search`  (lines 760–786)

```
def _require_memory_search(_config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Ensures there is exactly one usable default memory-search provider when an extension requires memory search. Memory search is the feature that retrieves stored conversation or knowledge snippets.

**Data flow**: It scans manifests for the default memory-search provider name, rejects none or more than one, checks whether the provider needs credentials, and raises if the credential store is missing.

**Call relations**: `_validate_requires` calls this for extensions that require the `memory_search` seam.


##### `_select_auth_proxy`  (lines 798–835)

```
def _select_auth_proxy(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> AuthProxy | None
```

**Purpose**: Chooses the fallback authentication proxy used by connector feed sync when a connector does not have its own broker. This proxy can safely obtain needed credentials on the host side.

**Data flow**: It gathers auth-proxy specs from manifests, handles automatic selection when there is only one, rejects ambiguity, unknown names, duplicates, or missing credential keys, and returns a built proxy or `None`.

**Call relations**: `_connector_registry` calls this while building the central connector registry, so connector sync has a fallback credential path when appropriate.

*Call graph*: called by 1 (_connector_registry); 2 external calls (__init__, __init__).


##### `_mount_ext_routes`  (lines 838–876)

```
def _mount_ext_routes(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, index: IndexBackend, embed: EmbedClient) -> None
```

**Purpose**: Adds extension-defined HTTP routes to the FastAPI app under `/ext/<extension>/...`. Each route must identify its workspace before its handler can run.

**Data flow**: It scans manifests for routes, requires a credential key if routes exist, builds an extension context, wraps each handler with an authorization and workspace-binding endpoint, and adds the route to the app.

**Call relations**: `run` calls this after core services are ready. The nested endpoint performs the per-request safety check whenever one of these extension routes is hit.

*Call graph*: called by 1 (run); 2 external calls (add_route, context_for).


##### `_mount_ext_routes.endpoint`  (lines 860–870)

```
async def endpoint(request: Request, handler=spec.handler, identify=spec.identify, extension_context=context) -> Response
```

**Purpose**: Runs one extension route after confirming which workspace the request belongs to. It blocks unauthorized requests before extension code can touch data.

**Data flow**: It receives a web request, calls the route’s identify function, returns a 401 response if identification fails, otherwise binds the workspace and awaits the extension handler with its context.

**Call relations**: Created by `_mount_ext_routes` for each extension route and then invoked by FastAPI during request handling.

*Call graph*: 2 external calls (Response, ws).


##### `WorkspaceScopeBoundary.__call__`  (lines 897–905)

```
async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None
```

**Purpose**: Clears workspace state at the start and end of every HTTP request. This prevents one request’s workspace identity from leaking into another request.

**Data flow**: For non-HTTP traffic it simply passes through. For HTTP traffic it sets the current workspace to `None`, calls the downstream app, and always resets it to `None` again after the response finishes or fails.

**Call relations**: `_mount_shared_surfaces` installs this as middleware. Surface endpoints then set the workspace for a request, and this boundary guarantees cleanup afterward.

*Call graph*: 1 external calls (set).


##### `_mount_shared_surfaces`  (lines 908–1083)

```
def _mount_shared_surfaces(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: WorkspaceBlobStore, sandboxes: ConversationSandbox, hub: Hub, dbos_client: DBOSClie
```

**Purpose**: Mounts the shared fleet’s surface routes, such as user-facing chat or app surfaces, in a way that resolves the workspace per request. It also starts shared writeback and mid-turn reply pollers when durable surfaces need them.

**Data flow**: It installs workspace cleanup middleware, builds admission, tailing, stopping, auth, deployment metadata, slot, object, and preview context pieces, then loops through surface specs to add routes, listeners, and pollers.

**Call relations**: `run` calls this to expose installed surfaces. It uses `_mount_home` for the root redirect and creates nested helpers that surface handlers use during requests.

*Call graph*: calls 1 internal fn (_mount_home); called by 1 (run); 20 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, add_middleware (+10 more)).


##### `_mount_shared_surfaces.context_for`  (lines 989–1018)

```
def context_for(workspace_id: UUID, surface: str) -> SurfaceContext
```

**Purpose**: Builds the `SurfaceContext` for one workspace and surface. This context is the bundle of safe tools a surface handler needs: storage, admission, credentials, models, skills, memory, objects, preview settings, and more.

**Data flow**: It receives a workspace ID and surface name, combines them with shared services captured from `_mount_shared_surfaces`, and returns a ready-to-use context object.

**Call relations**: Surface endpoints, listeners, writeback pollers, and mid-turn reply pollers use this helper so they all get a consistent workspace-scoped view of the system.

*Call graph*: calls 1 internal fn (home_surface); 2 external calls (__init__, __init__).


##### `_mount_shared_surfaces.endpoint`  (lines 1046–1060)

```
async def endpoint(request: Request, handler=route.handler, identify=resolver, surface=spec.name, surface_auth=auth) -> Response
```

**Purpose**: Handles one mounted surface HTTP route. It verifies the request, binds the resolved workspace, and then lets the surface route handler run.

**Data flow**: It receives a request, asks the surface’s identify function to resolve it, returns a provided response or 401 on failure, sets the current workspace on success, builds a surface context, and awaits the route handler.

**Call relations**: Created inside `_mount_shared_surfaces` for each surface route and invoked by FastAPI during normal user request handling.

*Call graph*: 2 external calls (Response, set).


##### `home_surface`  (lines 1086–1093)

```
def home_surface(manifests: tuple[Manifest, ...]) -> str | None
```

**Purpose**: Finds which installed surface should be treated as the browser home page. It refuses startup if more than one surface claims that role.

**Data flow**: It scans all surface specs for the `home` marker, returns the single home surface name, returns `None` if there is no home surface, or raises an error if there are several.

**Call relations**: `_mount_home` uses this to decide whether `/` should redirect. `context_for` also includes the result in each surface context so surfaces know the deployment’s home surface.

*Call graph*: called by 2 (_mount_home, context_for).


##### `_mount_home`  (lines 1096–1108)

```
def _mount_home(app: FastAPI, manifests: tuple[Manifest, ...]) -> None
```

**Purpose**: Adds a simple `GET /` route that redirects browsers to the configured home surface. This makes the bare service URL useful instead of returning a not-found page.

**Data flow**: It asks `home_surface` for the home surface name, does nothing if none exists, otherwise creates a redirect endpoint and adds it to the FastAPI app.

**Call relations**: `_mount_shared_surfaces` calls this after mounting surface routes, so the redirect points to a surface route that already exists.

*Call graph*: calls 1 internal fn (home_surface); called by 1 (_mount_shared_surfaces); 1 external calls (add_route).


##### `_mount_home.home`  (lines 1105–1106)

```
async def home(_request: Request) -> Response
```

**Purpose**: Returns the redirect response for the root path. It sends the browser to the selected surface entry point.

**Data flow**: It ignores the incoming request details and returns an HTTP 303 redirect response pointing at `/surface/<home-surface>`.

**Call relations**: Created by `_mount_home` and called by FastAPI when a browser requests `GET /`.

*Call graph*: 1 external calls (RedirectResponse).


##### `_serve_lifespan`  (lines 1112–1139)

```
async def _serve_lifespan(app: FastAPI) -> AsyncIterator[None]
```

**Purpose**: Runs background tasks tied to the FastAPI app’s lifetime. These tasks recover stranded workflows, reconcile cancellations, clean up unreachable turns, and run surface delivery listeners or pollers.

**Data flow**: When the app starts, it creates a task group and starts recovery, cancellation, stranded-turn, poller, and listener tasks. When the app shuts down, it cancels those tasks.

**Call relations**: `run` passes this function as the FastAPI lifespan handler, so it becomes active while Uvicorn is serving requests.

*Call graph*: 4 external calls (__init__, __init__, __init__, TaskGroup).


##### `_proxy_endpoint`  (lines 1142–1218)

```
def _proxy_endpoint(app: FastAPI, config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, pricing: Pricing, run_tokens: RunTokenCodec, blob: FilesystemBlobStore | S3BlobS
```

**Purpose**: Sets up sandbox network egress control. Egress means outbound network access; this function decides how sandbox traffic is authorized, metered, and connected through the separate proxy process.

**Data flow**: It reads proxy, certificate, cache, preview, and token settings; creates temporary local defaults when allowed; builds per-agent network rules; mounts control and git-credential routers on the app; and returns the proxy endpoint information given to sandboxes.

**Call relations**: `run` calls this while constructing `ConversationSandbox`. It uses `_ephemeral_egress_ca` for local no-shared-CA boots and `_one_shot` to derive artifact-store rules.

*Call graph*: calls 2 internal fn (_ephemeral_egress_ca, _one_shot); called by 1 (run); 14 external calls (__init__, __init__, __init__, __init__, include_router, token_urlsafe, connector_transfer_hosts, derive_artifact_store_rules, derive_manifest_rules, connector_clis (+4 more)).


##### `_ephemeral_egress_ca`  (lines 1221–1240)

```
def _ephemeral_egress_ca() -> str
```

**Purpose**: Creates a temporary certificate authority certificate for local sandbox egress setup when no shared CA is configured. A certificate authority is a trust anchor used to validate proxy-created TLS certificates.

**Data flow**: It generates a private key, builds a self-signed CA certificate valid for a long period, serializes only the certificate as PEM text, and returns that text.

**Call relations**: `_proxy_endpoint` calls this only for local deployments without a provided egress CA. Hosted deployments must provide a real shared CA instead.

*Call graph*: called by 1 (_proxy_endpoint); 9 external calls (generate_private_key, SHA256, BasicConstraints, CertificateBuilder, Name, NameAttribute, random_serial_number, now, timedelta).


##### `_connector_registry`  (lines 1246–1269)

```
def _connector_registry(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> ConnectorRegistry
```

**Purpose**: Builds the central registry of connector providers installed by extensions. Connectors are integrations that can authenticate to external services and sync or act on their data.

**Data flow**: It scans manifests for connectors, rejects duplicate provider names, creates registry entries with labels and broker information, selects a fallback auth proxy, and returns a `ConnectorRegistry`.

**Call relations**: `run` uses the returned registry in the runtime and sync credential resolver. It calls `_select_auth_proxy` to attach the fallback path for unbrokered connectors.

*Call graph*: calls 1 internal fn (_select_auth_proxy); called by 1 (run); 3 external calls (__init__, __init__, open_connector_namespace).


##### `_connect_flow`  (lines 1272–1310)

```
def _connect_flow(credentials: CredentialStore | None, config: Config, manifests: tuple[Manifest, ...], index: IndexBackend | None=None, embed: EmbedClient | None=None, resumption: ConnectResume | Non
```

**Purpose**: Builds the OAuth connection flow used when members connect external accounts. OAuth is the common browser-based sign-in handoff used by services like Google or Slack.

**Data flow**: If there is no credential store, it returns `None`. Otherwise it gathers connector OAuth providers, rejects duplicate names, computes the callback URL, builds grant storage and connection hooks, and returns a `ConnectFlow`.

**Call relations**: `run` installs the result with the global connect-flow installer. It delegates URL validation to `_connect_redirect_uri`.

*Call graph*: calls 1 internal fn (_connect_redirect_uri); called by 1 (run); 4 external calls (__init__, __init__, connection_hooks, open_connector_namespace).


##### `_connect_redirect_uri`  (lines 1313–1339)

```
def _connect_redirect_uri(config: Config, providers: Mapping[str, OAuthProvider]) -> str
```

**Purpose**: Computes and validates the public OAuth callback URL for connector sign-in. The URL must be something the member’s browser can actually open.

**Data flow**: It reads `connect.public_base_url`, allows an empty result only when no connector providers exist, parses and validates scheme and host, rejects wildcard bind addresses, and returns the base URL plus the callback path.

**Call relations**: `_connect_flow` calls this while building the OAuth provider registry, so bad public URL configuration fails as soon as connectors are installed.

*Call graph*: called by 1 (_connect_flow); 1 external calls (urlparse).


### Skill diagnostics
A direct sample skill probe confirms that the skill environment can be reached and executed.

### `extensions/sample/skills/sample_skill/probe.py`

`entrypoint` · `startup or diagnostic check`

This file acts like a quick “is it alive?” test for the sample skill. It does not load data, call other project code, or make decisions. Its only job is to print the text `sample-skill-probe-ok` when Python runs the file.

That fixed message matters because it gives the surrounding system something predictable to look for. For example, a setup script, test runner, or developer may run this probe to confirm that the sample skill’s files are present, Python can execute them, and the basic extension wiring is not completely broken. It is similar to pressing a doorbell: the sound does not do much by itself, but it proves the button, wiring, and chime are connected well enough to respond.

If this file were missing or changed unexpectedly, any check that expects this exact output could fail, even though the larger skill might still contain other code. Its value is in being small, stable, and easy to verify.

## 📊 State Registers Touched

- `reg-effective-config` — The merged deployment settings that tell the process how to run, which services to use, and which safety options are enabled.
- `reg-extension-registry` — The loaded list of installed extensions, packs, routes, tools, skills, jobs, credentials, backends, and migrations.
- `reg-database-store` — The shared database connection and tables where workspaces, users, agents, turns, files, jobs, costs, and extension data are saved.
- `reg-workspace-principals` — The current workspace, members, agents, controlling users, and ownership identities used to decide who is acting.
- `reg-credential-vault` — The encrypted store of API keys, OAuth tokens, and other secrets that can be injected only into approved places.
- `reg-agent-settings` — The durable settings for each agent, including model choice, reasoning mode, internet access, sandbox size, tools, setup needs, icon, and visibility.
- `reg-runtime-fleet` — The records of running server or worker instances, their heartbeats, listener claims, and cleanup ownership.
- `reg-sandbox-runtime` — The remembered sandbox handles, workspace directories, terminals, command sessions, ports, and cleanup state used for safe code execution.
- `reg-egress-policy` — The network access rules and proxy authorization state that decide what sandboxed code may contact outside the system.
- `reg-observability-traces` — The shared trace, metric, log, and traceparent information that lets operators connect startup, turns, tools, subagents, and billing events.
- `reg-extension-install-state` — The persisted extension-store pins, install/remove choices, and update-check metadata used to decide which extension packages should be discovered and loaded.
- `reg-sandbox-image-cache` — The local or remote sandbox image/build cache and validation state used to choose, compare, and launch safe execution environments.
