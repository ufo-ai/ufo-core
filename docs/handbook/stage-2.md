# Process bootstrap, CLI commands, and application lifespan  `stage-2`

This stage is the system’s front door and power switch. It covers what happens when an operator starts UFO, runs an admin command, builds a deployable package, or shuts the service down cleanly. The main server path is in core/src/ufo/serve.py. It reads configuration, opens database connections, loads extensions, starts background workers, attaches web routes, and launches the HTTP server that clients talk to. core/src/ufo/cli.py provides ufoctl, the local command-line tool for setup, running, inspection, packaging, repair, and development tasks. control/src/ufo_control/main.py plays a similar role for the hosted control service, including database setup, invitations, Slack retry work, and access-policy setup. core/src/ufo/bundle.py freezes a deployment into a repeatable Docker build folder, like packing a machine with its exact parts list. core/src/ufo/proxy_serve.py starts the shared network proxy used by sandboxes, applying the right workspace rules based on each request’s run token. Together these pieces start services, prepare their dependencies, expose commands and routes, and support orderly shutdown.

## Files in this stage

### Operator command lines
Command-line entrypoints expose local deployment operations and hosted-control administration workflows.

### `core/src/ufo/cli.py`

`entrypoint` · `operator command execution, from startup through admin maintenance`

This file turns many parts of the UFO system into human-friendly terminal commands. Without it, a new user would have to manually create config files, set secrets, migrate databases, create the first workspace, start servers, and poke database tables by hand. The file is like a control panel: each button is a command, and the code behind the button loads configuration, checks inputs, opens the right database connection, calls the subsystem that does the real work, then prints a short result.

The startup path loads a `.env` file beside `ufo.toml`, so local secrets work without the user typing `export` commands. The `init` command creates a default config, generates development secrets, migrates the database, onboards the first owner and workspace, and stores a long-lived CLI token. The `serve`, `proxy`, and `ingress` commands start runtime services. Other command groups let operators set spend caps, credit balances, inspect spend, view transcript-read disclosures, manage encrypted credentials, install extensions, build deployable bundles, cancel stuck turns, and seed demo content.

A notable piece is `BrowserHandoff`, which signs the user into the browser portal without putting the token in the URL. It briefly starts a local one-use web page that posts the token as a form, then closes.

#### Function details

##### `_ufoctl_dir`  (lines 78–80)

```
def _ufoctl_dir() -> Path
```

**Purpose**: Finds the private directory where this machine stores `ufoctl` state, such as the CLI token. It lets tests or advanced users override the location with an environment variable.

**Data flow**: It reads the `UFOCTL_DIR` environment variable. If it is set, it turns that value into a path; otherwise it uses a `.ufoctl` folder in the current user’s home directory. It returns that path without creating it.

**Call relations**: `init` calls this before writing the CLI token, and `portal` calls it before reading that token back for browser sign-in.

*Call graph*: called by 2 (init, portal); 2 external calls (Path, home).


##### `_dotenv_path`  (lines 83–84)

```
def _dotenv_path() -> Path
```

**Purpose**: Locates the `.env` file that sits beside the main UFO config file. This gives secrets a predictable local home.

**Data flow**: It asks the config system where `ufo.toml` lives, takes that file’s folder, and returns the path to `.env` inside it.

**Call relations**: The environment-loading and secret-writing helpers call this whenever they need to read or update local secret values. `init` also uses it when telling the user where secrets were written.

*Call graph*: called by 4 (_load_dotenv, _missing_deploy_keys, _write_dev_secrets, init); 1 external calls (config_path).


##### `_dotenv_pairs`  (lines 87–121)

```
def _dotenv_pairs(text: str) -> list[tuple[str, str]]
```

**Purpose**: Reads simple `.env` text and turns it into key-value pairs. It supports ordinary one-line secrets and quoted multi-line secrets such as private keys.

**Data flow**: It receives raw text, skips blank lines and comments, removes an optional `export`, strips matching quotes, and preserves quoted multi-line values. It returns a list of `(name, value)` pairs, or raises an error if a quoted value never closes.

**Call relations**: `_load_dotenv` uses it to fill environment variables, `_write_dev_secrets` uses it to avoid overwriting existing secrets, and `_missing_deploy_keys` uses it to see which required keys are already present.

*Call graph*: called by 3 (_load_dotenv, _missing_deploy_keys, _write_dev_secrets).


##### `_load_dotenv`  (lines 124–133)

```
def _load_dotenv() -> None
```

**Purpose**: Loads local secret values from `.env` into the process environment before any command runs. Existing environment variables win, so explicit shell settings are not overwritten.

**Data flow**: It finds the `.env` file, returns immediately if it does not exist, parses its contents, and copies each missing variable into `os.environ`.

**Call relations**: The top-level `main` command calls this first. That means later commands such as `init`, `serve`, and credential commands can read secrets in the normal environment-variable way.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (main).


##### `main`  (lines 137–139)

```
def main() -> None
```

**Purpose**: Defines the root `ufoctl` command group. It is the entry point that Click, the command-line framework, uses to attach all subcommands.

**Data flow**: When any `ufoctl` command starts, this function runs and loads `.env` values into the environment. It does not return user data; it prepares the process for the selected subcommand.

**Call relations**: All other commands in this file hang under this command group. Its main handoff is to `_load_dotenv`, which prepares secrets before command-specific work begins.

*Call graph*: calls 1 internal fn (_load_dotenv).


##### `_one_address`  (lines 142–147)

```
def _one_address(_ctx: click.Context, _param: click.Parameter, value: str) -> str
```

**Purpose**: Validates that the owner email passed to `init` looks like exactly one local-address-at-domain email. This catches a common setup typo early.

**Data flow**: It receives the Click callback inputs and the proposed email string. It asks the seat/email helper whether the value has a domain; if not, it raises a friendly command-line error. Otherwise it returns the email unchanged.

**Call relations**: Click calls this while parsing the `--email` option for `init`, before the onboarding code tries to write the owner member to the database.

*Call graph*: 2 external calls (BadParameter, email_domain).


##### `init`  (lines 153–185)

```
def init(email: str, model: str) -> None
```

**Purpose**: Creates a working UFO installation for a new deploy or local checkout. It writes default config when needed, creates secrets, migrates the database, creates the first workspace and owner, and stores a CLI token.

**Data flow**: It reads or creates config, writes missing development secrets, may create a PostgreSQL system database, applies migrations, onboards the workspace, mints a signed bearer token, saves it under the `ufoctl` directory, and prints next steps.

**Call relations**: This is the first command most users run. It orchestrates helpers such as `_write_dev_secrets`, `_create_postgres_system_database`, `_onboard`, `_ufoctl_dir`, and `_missing_deploy_keys` so the rest of the CLI and server have a valid workspace to use.

*Call graph*: calls 6 internal fn (_create_postgres_system_database, _dotenv_path, _missing_deploy_keys, _onboard, _ufoctl_dir, _write_dev_secrets); 7 external calls (run, ClickException, echo, mint_token, config_path, load_config, apply_migrations).


##### `_missing_deploy_keys`  (lines 188–203)

```
def _missing_deploy_keys(config: Config) -> tuple[str, ...]
```

**Purpose**: Reports provider API keys that installed extensions say they need but that are not currently available. It warns rather than blocks startup, so optional features can be configured later.

**Data flow**: It loads extension manifests, collects their declared deployment keys, reads names already present in `.env` and the process environment, then returns the sorted missing names.

**Call relations**: `init` calls this after onboarding so it can print useful setup reminders while the user is already looking at configuration output.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 1 external calls (load_manifests).


##### `_write_dev_secrets`  (lines 206–228)

```
def _write_dev_secrets(config: Config) -> tuple[str, ...]
```

**Purpose**: Creates local development secrets needed for a zero-service setup. These include encryption and token-signing secrets used by credentials, artifacts, and CLI/member authentication.

**Data flow**: It generates candidate secrets, reads existing `.env` values and environment variables, writes only the missing ones to `.env`, also adds them to the current process environment, and returns the names it added.

**Call relations**: `init` calls this before onboarding and token minting. It uses `_dotenv_path` and `_dotenv_pairs` so it can merge safely instead of replacing a user’s existing secrets.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 2 external calls (generate_key, token_urlsafe).


##### `_onboard`  (lines 231–250)

```
async def _onboard(config: Config, email: str, model: str) -> Onboarded
```

**Purpose**: Creates the initial workspace, owner member, default agent, model setup, and extension onboarding data. It keeps the database open only for this onboarding window.

**Data flow**: It initializes database access, optionally builds an encrypted credential store from the configured key, creates an `Onboarding` object with config, email, model, credentials, and extension manifests, runs core creation and extension steps, then closes database resources.

**Call relations**: `init` calls this after config, secrets, and migrations are ready. It hands off the detailed creation work to the onboarding subsystem and returns the workspace information needed to mint the CLI token.

*Call graph*: called by 1 (init); 6 external calls (__init__, __init__, Fernet, dispose_db, init_db, load_manifests).


##### `_create_postgres_system_database`  (lines 253–264)

```
async def _create_postgres_system_database(config: Config) -> None
```

**Purpose**: Ensures the separate PostgreSQL system database exists when the app is configured for PostgreSQL. This helps local setup create the database before migrations need it.

**Data flow**: It derives a plain PostgreSQL connection string, connects, checks whether the configured system database name exists, creates it if missing, and closes the connection.

**Call relations**: `init` calls this only for PostgreSQL-style database URLs, before applying migrations.

*Call graph*: called by 1 (init); 1 external calls (connect).


##### `migrate`  (lines 268–285)

```
def migrate() -> None
```

**Purpose**: Brings the database schema up to date. A schema is the set of tables and columns the application expects.

**Data flow**: It loads config, chooses either an owner database URL from the environment or the normal config URL, runs migrations for the active pack, and prints success.

**Call relations**: Operators run this after installing extensions or during deploy jobs. It delegates the real schema work to `apply_migrations`.

*Call graph*: 3 external calls (echo, load_config, apply_migrations).


##### `serve`  (lines 289–298)

```
def serve() -> None
```

**Purpose**: Starts the main UFO runtime: user surfaces, workers, and jobs. It also tells the user the portal URL when the active pack has a browser surface.

**Data flow**: It loads config, loads extension manifests, asks which browser surface is the home surface, prints a helpful URL if one exists, then starts the server runtime.

**Call relations**: This is the command that moves a configured deploy into normal operation. It uses `_serve_base` for the local URL and hands execution to the serving subsystem.

*Call graph*: calls 1 internal fn (_serve_base); 5 external calls (echo, load_config, load_manifests, home_surface, run).


##### `portal`  (lines 302–320)

```
def portal() -> None
```

**Purpose**: Opens the browser portal and signs in using the CLI token stored by `init`. It saves the user from copying and pasting a token manually.

**Data flow**: It loads config, finds the home browser surface, reads the local CLI token, checks that the server answers, creates a `BrowserHandoff`, opens the browser, and prints the portal URL.

**Call relations**: This command depends on `serve` already running and on `init` having written a token. It uses `_serve_base`, `_ufoctl_dir`, and `BrowserHandoff` to bridge from terminal authentication to browser session.

*Call graph*: calls 2 internal fn (_serve_base, _ufoctl_dir); 7 external calls (__init__, ClickException, echo, get, load_config, load_manifests, home_surface).


##### `_serve_base`  (lines 323–324)

```
def _serve_base(config: Config) -> str
```

**Purpose**: Builds the base local HTTP address for the running UFO server. It keeps URL formatting in one small helper.

**Data flow**: It reads the configured host and port and returns a string like `http://host:port`.

**Call relations**: `serve` uses it when printing the portal address, and `portal` uses it when checking and opening the browser surface.

*Call graph*: called by 2 (portal, serve).


##### `BrowserHandoff.open`  (lines 339–347)

```
def open(self) -> None
```

**Purpose**: Performs a one-time, local browser sign-in handoff. It serves a temporary web page on localhost that submits the CLI token to the portal.

**Data flow**: It creates an unguessable path, starts a local HTTP server on `127.0.0.1` with a random free port, opens the browser to that path, and handles requests until the page has been delivered.

**Call relations**: `portal` creates a `BrowserHandoff` and calls this. This method uses `_responder` to build the temporary request handler and `_page` indirectly to create the form page.

*Call graph*: calls 1 internal fn (_responder); 5 external calls (echo, HTTPServer, token_urlsafe, Event, open).


##### `BrowserHandoff._responder`  (lines 349–366)

```
def _responder(self, path: str, delivered: threading.Event) -> type[BaseHTTPRequestHandler]
```

**Purpose**: Builds the tiny HTTP request handler used during browser handoff. The handler only serves the secret handoff path.

**Data flow**: It receives the expected path and an event flag, generates the HTML page once, and returns a request-handler class that can serve it.

**Call relations**: `BrowserHandoff.open` calls this when setting up the local HTTP server. The returned handler’s `do_GET` method is what the browser actually reaches.

*Call graph*: calls 1 internal fn (_page); called by 1 (open).


##### `BrowserHandoff._responder.do_GET`  (lines 353–362)

```
def do_GET(self) -> None
```

**Purpose**: Serves the one handoff page to the browser, but only at the exact random path. Any other path gets a not-found response.

**Data flow**: It reads the incoming request path. If the path is wrong, it sends a 404 error; if it matches, it sends the HTML page, writes the bytes to the browser, and marks the handoff as delivered.

**Call relations**: The local HTTP server created by `BrowserHandoff.open` calls this when the browser connects. Setting the delivered flag lets `open` stop listening after the token handoff page is served.


##### `BrowserHandoff._responder.log_message`  (lines 364–364)

```
def log_message(self, *args: object) -> None
```

**Purpose**: Silences the default HTTP server logging for the temporary handoff server. This avoids printing noisy request lines during a normal portal open.

**Data flow**: It receives log arguments from the HTTP server and intentionally does nothing.

**Call relations**: The standard library HTTP server calls this during handoff requests. It exists only inside the responder class returned by `_responder`.


##### `BrowserHandoff._page`  (lines 368–375)

```
def _page(self) -> str
```

**Purpose**: Creates the HTML form page that posts the CLI token to the portal. The token is placed in the form body, not in the URL.

**Data flow**: It reads the portal URL and token from the `BrowserHandoff`, safely escapes them for HTML, and returns a small page with an auto-submitting form and a fallback button.

**Call relations**: `_responder` calls this before serving the local handoff page. The browser then submits the form to the normal portal sign-in endpoint.

*Call graph*: called by 1 (_responder); 1 external calls (escape).


##### `proxy`  (lines 379–381)

```
def proxy() -> None
```

**Purpose**: Starts the shared egress proxy, which is the network service used by workspace sandboxes to reach outward through one controlled front door.

**Data flow**: It takes no command arguments and hands execution to the proxy serving subsystem.

**Call relations**: Operators run this as its own service command. The actual network loop lives in `ufo.proxy_serve.run`.

*Call graph*: 1 external calls (run).


##### `ingress`  (lines 385–387)

```
def ingress() -> None
```

**Purpose**: Starts the sandbox ingress service, a token-protected reverse proxy into conversation sandbox ports. A reverse proxy accepts a request and forwards it to another local service.

**Data flow**: It takes no command arguments and starts the ingress serving subsystem.

**Call relations**: Operators run this when sandbox access from outside needs to be available. The actual serving behavior lives in `ufo.ingress_serve.run`.

*Call graph*: 1 external calls (run).


##### `spend_cap`  (lines 391–392)

```
def spend_cap() -> None
```

**Purpose**: Defines the `spend-cap` command group for reading and changing limits on how much a workspace, member, or agent may spend in a time window.

**Data flow**: It does not process data itself; it gives Click a parent command under which `set` and `list` are registered.

**Call relations**: Click uses this group to route `ufoctl spend-cap set` and `ufoctl spend-cap list` to their specific functions.


##### `spend_cap_set`  (lines 403–421)

```
def spend_cap_set(scope: str, subject_id: str, window_seconds: int, limit_micro_usd: int, on_breach: str) -> None
```

**Purpose**: Creates or updates a spending limit. This lets an operator cap cost over a period and choose whether excess work is parked or rejected.

**Data flow**: It validates the scope and subject ID, converts a subject ID to a UUID when present, loads config, writes the cap through `_write_spend_cap`, converts micro-dollars to dollars for display, and prints the result.

**Call relations**: Click calls this for `ufoctl spend-cap set`. It uses `_write_spend_cap` for the database change and reports the cap in human-readable money.

*Call graph*: calls 1 internal fn (_write_spend_cap); 5 external calls (run, ClickException, echo, load_config, UUID).


##### `spend_cap_list`  (lines 425–435)

```
def spend_cap_list() -> None
```

**Purpose**: Shows all spend caps configured for the workspace. This gives operators a quick view of current cost guardrails.

**Data flow**: It loads config, reads cap rows through `_read_spend_caps`, prints `no spend caps set` if empty, otherwise formats each cap with its scope, target, time window, amount, and breach behavior.

**Call relations**: Click calls this for `ufoctl spend-cap list`. It delegates database reading to `_read_spend_caps`.

*Call graph*: calls 1 internal fn (_read_spend_caps); 3 external calls (run, echo, load_config).


##### `_write_spend_cap`  (lines 438–492)

```
async def _write_spend_cap(config: Config, scope: str, subject: UUID | None, window_seconds: int, limit_micro_usd: int, on_breach: str) -> UUID
```

**Purpose**: Writes the database row for a spend cap, updating an existing matching cap when one already exists. Matching is based on workspace, scope, subject, and time window.

**Data flow**: It opens the workspace database, finds the workspace ID, searches for an existing cap with the same target and window, updates the amount and breach behavior if found, or inserts a new cap with a fresh UUID. It returns the cap ID and closes the database.

**Call relations**: `spend_cap_set` calls this after validating command-line inputs. It performs the actual insert or update through the workspace transaction.

*Call graph*: called by 1 (spend_cap_set); 7 external calls (insert, select, update, dispose_db, init_db, workspace_tx, uuid4).


##### `_read_spend_caps`  (lines 495–521)

```
async def _read_spend_caps(config: Config) -> list[tuple[UUID, str, UUID | None, int, int, str]]
```

**Purpose**: Reads spend cap records for the current workspace. It provides the raw data that the CLI formats for humans.

**Data flow**: It opens the database, finds the workspace ID, selects cap fields for that workspace ordered by scope, converts rows into tuples, and closes the database.

**Call relations**: `spend_cap_list` calls this and then decides how to display the returned list.

*Call graph*: called by 1 (spend_cap_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `balance`  (lines 525–526)

```
def balance() -> None
```

**Purpose**: Defines the `balance` command group for prepaid workspace funds. Operators use its subcommands to show, credit, or reserve balance.

**Data flow**: It does not read or write balance data itself; it registers a parent command for the balance subcommands.

**Call relations**: Click uses this group to route `balance show`, `balance credit`, and `balance reserve`.


##### `balance_show`  (lines 531–546)

```
def balance_show(workspace_id: str) -> None
```

**Purpose**: Displays the current prepaid balance, required reserve, and lifetime granted and charged totals. This helps an operator understand whether turns can start.

**Data flow**: It loads config, reads a balance for the named or only workspace through `_read_balance`, prints `no balance` if missing, otherwise formats micro-dollars as dollars and prints totals.

**Call relations**: Click calls this for `ufoctl balance show`. It relies on `_read_balance`, which sets up the correct workspace scope before reading.

*Call graph*: calls 1 internal fn (_read_balance); 3 external calls (run, echo, load_config).


##### `balance_credit`  (lines 554–567)

```
def balance_credit(granted_micro_usd: int, charged_micro_usd: int, reference: str, workspace_id: str) -> None
```

**Purpose**: Adds money to a workspace balance once per reference key. The reference makes the operation safe to retry without double-crediting.

**Data flow**: It rejects a zero grant, loads config, calls `_credit_balance` with granted amount, charged amount, reference, and optional workspace ID, then prints whether a new credit was applied or had already been recorded.

**Call relations**: Click calls this for `ufoctl balance credit`. It delegates idempotent accounting to the balance subsystem through `_credit_balance`.

*Call graph*: calls 1 internal fn (_credit_balance); 4 external calls (run, ClickException, echo, load_config).


##### `balance_reserve`  (lines 573–581)

```
def balance_reserve(micro_usd: int, workspace_id: str) -> None
```

**Purpose**: Sets the minimum balance headroom required before a turn may begin. This prevents starting work when there is too little prepaid balance.

**Data flow**: It rejects negative reserve values, loads config, calls `_set_reserve`, prints the new reserve if successful, or raises an error if no balance row exists yet.

**Call relations**: Click calls this for `ufoctl balance reserve`. It uses `_set_reserve`, which opens the right workspace and calls the balance subsystem.

*Call graph*: calls 1 internal fn (_set_reserve); 4 external calls (run, ClickException, echo, load_config).


##### `_target_workspace`  (lines 584–606)

```
async def _target_workspace(named: str) -> UUID
```

**Purpose**: Chooses which workspace an administrative command should affect. If a workspace ID is supplied it verifies it; if not, it only chooses automatically when there is exactly one workspace.

**Data flow**: It reads across workspaces through an owner-level transaction. With a supplied ID, it checks that ID exists and returns it; without one, it returns the sole workspace ID or raises a clear error if there are none or many.

**Call relations**: `_balance_scope` and `_seed_target` call this before doing workspace-specific work. It prevents hosted multi-workspace deploys from accidentally changing the wrong workspace.

*Call graph*: called by 2 (_balance_scope, _seed_target); 4 external calls (ClickException, select, owner_tx, UUID).


##### `_balance_scope`  (lines 610–627)

```
async def _balance_scope(config: Config, named: str) -> AsyncIterator[tuple[AsyncConnection, UUID]]
```

**Purpose**: Creates a safe database scope for balance operations on one workspace. A scope here means the code is temporarily bound to the selected workspace for row-level security.

**Data flow**: It initializes app and owner database pools, resolves the target workspace, enters the workspace context, opens a workspace transaction, yields the connection and workspace ID, then disposes database resources afterward.

**Call relations**: `_read_balance`, `_credit_balance`, and `_set_reserve` all use this shared setup so they act on the same kind of correctly selected workspace.

*Call graph*: calls 1 internal fn (_target_workspace); called by 3 (_credit_balance, _read_balance, _set_reserve); 5 external calls (dispose_db, init_db, init_owner_db, workspace_tx, ws).


##### `_read_balance`  (lines 630–632)

```
async def _read_balance(config: Config, named: str) -> Balance | None
```

**Purpose**: Reads the balance object for a chosen workspace. It is the async helper behind the human-facing `balance show` command.

**Data flow**: It opens `_balance_scope`, passes the scoped connection and workspace ID to `read_balance`, and returns either a `Balance` object or `None`.

**Call relations**: `balance_show` calls this. It hands the actual accounting read to `ufo.balance.read_balance` after the workspace has been selected.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_show); 1 external calls (read_balance).


##### `_credit_balance`  (lines 635–641)

```
async def _credit_balance(config: Config, named: str, granted_micro_usd: int, charged_micro_usd: int, reference: str) -> bool
```

**Purpose**: Applies an idempotent balance credit to a chosen workspace. Idempotent means repeating the same reference does not apply the credit twice.

**Data flow**: It opens `_balance_scope`, passes the connection, workspace ID, amounts, and reference to the balance credit function, and returns a boolean saying whether a new credit was recorded.

**Call relations**: `balance_credit` calls this after validating command-line input. It hands the money-accounting rules to `ufo.balance.credit`.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_credit); 1 external calls (credit).


##### `_set_reserve`  (lines 644–646)

```
async def _set_reserve(config: Config, named: str, reserve_micro_usd: int) -> bool
```

**Purpose**: Updates the reserve amount for a chosen workspace’s balance. The reserve is the minimum headroom needed before work can start.

**Data flow**: It opens `_balance_scope`, passes the connection, workspace ID, and reserve amount to the balance subsystem, and returns whether the update succeeded.

**Call relations**: `balance_reserve` calls this. It delegates the actual database update to `ufo.balance.set_reserve`.

*Call graph*: calls 1 internal fn (_balance_scope); called by 1 (balance_reserve); 1 external calls (set_reserve).


##### `spend`  (lines 654–677)

```
def spend(window_seconds: int) -> None
```

**Purpose**: Prints a spending report over a recent time window. It breaks total cost down by dimensions such as member, agent, origin, and price version.

**Data flow**: It loads config, reads a `SpendReport` through `_read_spend`, converts micro-dollars to dollars, and prints totals and breakdown lines.

**Call relations**: Click calls this for `ufoctl spend`. It relies on `_read_spend` to do the database rollup and focuses on readable terminal output.

*Call graph*: calls 1 internal fn (_read_spend); 3 external calls (run, echo, load_config).


##### `_read_spend`  (lines 680–687)

```
async def _read_spend(config: Config, window_seconds: int) -> SpendReport
```

**Purpose**: Builds the spending report for the current workspace and requested time window. This is the database-reading half of the `spend` command.

**Data flow**: It initializes the database, finds the workspace ID, creates a spend rollup reader for that workspace, asks it to read the window, returns the report, and disposes database resources.

**Call relations**: `spend` calls this and then formats the returned report for display. The detailed accounting logic lives in `SpendRollup`.

*Call graph*: called by 1 (spend); 5 external calls (__init__, select, dispose_db, init_db, workspace_tx).


##### `transcript_reads`  (lines 695–708)

```
def transcript_reads(limit: int) -> None
```

**Purpose**: Lists administrator disclosures for reading another member’s private transcript. This gives operators an audit trail of sensitive transcript access.

**Data flow**: It validates that the limit is at least one, loads config, reads recent access records through `_read_transcript_accesses`, and prints either an empty message or rows with time, reader, subject, and conversation ID.

**Call relations**: Click calls this for `ufoctl transcript-reads`. It delegates the database query to `_read_transcript_accesses`.

*Call graph*: calls 1 internal fn (_read_transcript_accesses); 4 external calls (run, ClickException, echo, load_config).


##### `_read_transcript_accesses`  (lines 711–745)

```
async def _read_transcript_accesses(config: Config, limit: int) -> list[tuple[str, str, UUID, datetime]]
```

**Purpose**: Reads recent transcript-access disclosure records for the current workspace. It joins member records so the CLI can show emails instead of only IDs.

**Data flow**: It initializes the database, aliases the member table for reader and subject, finds the workspace ID, selects recent transcript access rows with joined emails, limits the result, returns tuples, and closes the database.

**Call relations**: `transcript_reads` calls this and formats the returned records for the terminal.

*Call graph*: called by 1 (transcript_reads); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `grants`  (lines 749–761)

```
def grants() -> None
```

**Purpose**: Lists OAuth account grants available to agents. OAuth is a standard way for users to give an app limited access to an outside account.

**Data flow**: It loads config, reads grant summaries through `_read_grants`, prints `no grants` if none exist, otherwise prints agent, provider, account ID, sharing status, and grant date.

**Call relations**: Click calls this for `ufoctl grants`. It uses `_read_grants` to ask the grants subsystem for workspace-level summaries.

*Call graph*: calls 1 internal fn (_read_grants); 3 external calls (run, echo, load_config).


##### `_read_grants`  (lines 764–771)

```
async def _read_grants(config: Config) -> tuple[GrantSummary, ...]
```

**Purpose**: Finds the current workspace and asks the grants subsystem for its OAuth grant summaries.

**Data flow**: It initializes the database, reads the workspace ID inside a workspace transaction, calls `workspace_grant_summaries`, returns the summaries, and disposes database resources.

**Call relations**: `grants` calls this before printing. The more detailed grant lookup is handled outside this CLI file.

*Call graph*: called by 1 (grants); 5 external calls (select, dispose_db, init_db, workspace_tx, workspace_grant_summaries).


##### `credential`  (lines 775–777)

```
def credential() -> None
```

**Purpose**: Defines the `credential` command group for encrypted bring-your-own-key credential slots. These are secrets required by extensions.

**Data flow**: It does not inspect credentials itself; it registers a parent command for setting and listing slots.

**Call relations**: Click routes `credential set` and `credential list` through this group.


##### `credential_set`  (lines 782–805)

```
def credential_set(slot: str) -> None
```

**Purpose**: Stores a secret value for a declared extension credential slot. It avoids accepting secrets as command arguments so they do not appear in shell history.

**Data flow**: It loads config, checks that the slot is declared and user-fillable, checks that the encryption key environment variable exists, reads the secret from a hidden prompt or standard input, rejects empty values, writes it through `_write_credential`, and prints confirmation.

**Call relations**: Click calls this for `ufoctl credential set`. It uses `_declared_slots`, `_fillable_slots`, and `_write_credential` to validate and store the secret safely.

*Call graph*: calls 3 internal fn (_declared_slots, _fillable_slots, _write_credential); 5 external calls (run, ClickException, echo, prompt, load_config).


##### `credential_list`  (lines 809–819)

```
def credential_list() -> None
```

**Purpose**: Shows which declared credential slots are set or unset without revealing their values. This helps operators see what still needs configuration.

**Data flow**: It loads config, reads declared slots, reads stored slot names, and prints each slot with its owning extension and set/unset status.

**Call relations**: Click calls this for `ufoctl credential list`. It combines extension declarations from `_declared_slots` with database state from `_read_stored_slots`.

*Call graph*: calls 2 internal fn (_declared_slots, _read_stored_slots); 3 external calls (run, echo, load_config).


##### `_declared_slots`  (lines 822–827)

```
def _declared_slots(config: Config) -> dict[str, str]
```

**Purpose**: Collects all credential slots declared by the active extension manifests. It maps each slot name to the extension that owns it.

**Data flow**: It loads manifests for the configured pack, converts manifest credential declarations into a dictionary, and turns manifest-loading errors into user-friendly CLI errors.

**Call relations**: `credential_set` uses this to reject unknown slots, and `credential_list` uses it to know what slots should be shown.

*Call graph*: called by 2 (credential_list, credential_set); 2 external calls (ClickException, load_manifests).


##### `_fillable_slots`  (lines 830–839)

```
def _fillable_slots(config: Config) -> frozenset[str]
```

**Purpose**: Finds the credential slots an operator or member is allowed to type manually. Some slots are written by the deployment itself and should not accept typed values.

**Data flow**: It loads extension manifests, filters credential declarations to those marked as member-fillable, returns their names as a frozen set, and reports loading errors as CLI errors.

**Call relations**: `credential_set` calls this after checking that a slot exists, so it can reject slots that are not meant for manual entry.

*Call graph*: called by 1 (credential_set); 2 external calls (ClickException, load_manifests).


##### `_write_credential`  (lines 842–849)

```
async def _write_credential(config: Config, key: str, slot: str, value: str) -> None
```

**Purpose**: Encrypts and stores one credential value for the current workspace. Encryption at rest means the database stores sealed text rather than the raw secret.

**Data flow**: It initializes the database, finds the workspace ID, creates a `CredentialStore` using the supplied Fernet encryption key, stores the slot value, and disposes database resources.

**Call relations**: `credential_set` calls this after reading and validating the secret. The actual storage is delegated to `CredentialStore.put`.

*Call graph*: called by 1 (credential_set); 6 external calls (__init__, Fernet, select, dispose_db, init_db, workspace_tx).


##### `_read_stored_slots`  (lines 852–866)

```
async def _read_stored_slots(config: Config) -> frozenset[str]
```

**Purpose**: Reads which credential slots currently have stored values for the workspace. It does not read or return the secret values themselves.

**Data flow**: It initializes the database, finds the workspace ID, selects credential slot names for that workspace, returns them as a frozen set, and closes database resources.

**Call relations**: `credential_list` calls this to decide whether each declared slot should be printed as set or unset.

*Call graph*: called by 1 (credential_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `ext`  (lines 870–871)

```
def ext() -> None
```

**Purpose**: Defines the `ext` command group for searching, installing, and removing extensions. Extensions add features to a UFO pack.

**Data flow**: It does no extension work directly; it registers the parent command for extension subcommands.

**Call relations**: Click routes `ext search`, `ext install`, and `ext remove` through this group.


##### `_store`  (lines 874–877)

```
def _store(config: Config) -> ExtensionStore
```

**Purpose**: Creates an extension-store object from configuration. The store knows the available catalog and the local lockfile of installed extensions.

**Data flow**: It checks that extension store configuration exists, reads the catalog, finds the lockfile path, builds an `ExtensionStore`, and returns it. If the store is disabled, it raises a CLI error.

**Call relations**: `ext_search`, `ext_install`, and `ext_remove` call this before doing store operations.

*Call graph*: called by 3 (ext_install, ext_remove, ext_search); 4 external calls (__init__, ClickException, lockfile_path, read_catalog).


##### `ext_search`  (lines 882–895)

```
def ext_search(query: str) -> None
```

**Purpose**: Searches the configured extension catalog and shows matching extensions. It also marks whether each one is installed, available, or bundle-only.

**Data flow**: It loads config, creates the store with `_store`, searches using the query string, prints an empty message if there are no matches, otherwise prints name, version, and state.

**Call relations**: Click calls this for `ufoctl ext search`. The catalog lookup is handled by the `ExtensionStore` returned by `_store`.

*Call graph*: calls 1 internal fn (_store); 2 external calls (echo, load_config).


##### `ext_install`  (lines 900–906)

```
def ext_install(name: str) -> None
```

**Purpose**: Pins an extension into the deployment lockfile so the next server run loads it. Pinning records the exact version and digest.

**Data flow**: It loads config, creates the store, asks it to install the named extension, catches store errors as CLI errors, and prints the installed pin details.

**Call relations**: Click calls this for `ufoctl ext install`. After this command, operators usually run migrations if the extension owns tables, then restart `serve`.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `ext_remove`  (lines 911–917)

```
def ext_remove(name: str) -> None
```

**Purpose**: Removes an extension pin from the lockfile so future runs stop loading it.

**Data flow**: It loads config, creates the store, asks it to remove the named extension, reports any store error as a CLI error, and prints confirmation.

**Call relations**: Click calls this for `ufoctl ext remove`. It delegates lockfile editing to `ExtensionStore.remove`.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `_ufo_project_dir`  (lines 923–934)

```
def _ufo_project_dir() -> Path
```

**Purpose**: Finds the source project directory needed to build the UFO Python wheel for a bundle. A wheel is a packaged Python distribution file.

**Data flow**: It walks upward from this file, looks for `pyproject.toml`, parses it, and returns the first ancestor whose project name is `ufo`. If none is found, it raises a clear CLI error.

**Call relations**: `bundle` calls this before running `uv build`, so the wheel is built from the actual UFO source project rather than the user’s current directory.

*Call graph*: called by 1 (bundle); 3 external calls (ClickException, Path, loads).


##### `bundle`  (lines 941–957)

```
def bundle(out: Path) -> None
```

**Purpose**: Builds a deployable bundle containing pinned config, extension locks, image recipe material, and a UFO wheel. This freezes a deploy into a repeatable artifact.

**Data flow**: It loads config, optionally reads the extension catalog, asks `Bundle` to build files into the output directory, runs `uv build` to create a wheel, verifies the wheel exists, and prints bundle contents and pinned extensions.

**Call relations**: Click calls this for `ufoctl bundle`. It uses `_ufo_project_dir` to locate source code and the bundle subsystem to assemble deployment files.

*Call graph*: calls 1 internal fn (_ufo_project_dir); 8 external calls (__init__, ClickException, echo, run, wheel_name, config_path, load_config, read_catalog).


##### `turn`  (lines 961–962)

```
def turn() -> None
```

**Purpose**: Defines the `turn` command group for actions on a single conversation turn. A turn is one unit of agent work in a conversation.

**Data flow**: It does not act on a turn itself; it registers the parent command for turn subcommands.

**Call relations**: Click routes `turn cancel` through this group.


##### `turn_cancel`  (lines 967–977)

```
def turn_cancel(turn_id: str) -> None
```

**Purpose**: Cancels one stuck or unwanted turn by ID. This is an operator repair tool for work that cannot be ended normally through the user surface.

**Data flow**: It loads config, converts the supplied turn ID into a UUID, calls `_cancel_turn`, and prints whether it was cancelled or already terminal.

**Call relations**: Click calls this for `ufoctl turn cancel`. It hands the actual durable-workflow cancellation to `_cancel_turn`.

*Call graph*: calls 1 internal fn (_cancel_turn); 4 external calls (run, echo, load_config, UUID).


##### `_cancel_turn`  (lines 980–1002)

```
async def _cancel_turn(config: Config, turn_id: UUID) -> bool
```

**Purpose**: Finds the workspace that owns a turn, then cancels that turn while bound to the correct workspace. This keeps tenant boundaries intact during an operator repair.

**Data flow**: It initializes app and possibly owner database access, reads the turn’s workspace ID through an owner transaction, raises if the turn does not exist, creates a replay-safe durable client, enters the workspace context, calls `cancel_one_turn`, returns whether cancellation happened, and closes database resources.

**Call relations**: `turn_cancel` calls this. It first uses owner-level access only to locate the tenant, then hands the cancellation to the normal cancellation subsystem under that workspace.

*Call graph*: called by 1 (turn_cancel); 9 external calls (ClickException, select, cancel_one_turn, dispose_db, init_db, init_owner_db, owner_tx, replay_safe_client, ws).


##### `seed`  (lines 1006–1007)

```
def seed() -> None
```

**Purpose**: Defines the `seed` command group for writing demonstration content into a workspace.

**Data flow**: It performs no seeding itself; it registers a parent command for seed subcommands.

**Call relations**: Click routes `seed kitchen-sink` through this group.


##### `seed_kitchen_sink`  (lines 1012–1021)

```
def seed_kitchen_sink(workspace_id: str) -> None
```

**Purpose**: Writes a comprehensive demo conversation that exercises the portal’s display shapes. Designers and reviewers can use it as stable sample content.

**Data flow**: It loads config, calls `_seed_kitchen_sink` with an optional workspace ID, receives the created conversation ID, and prints the browser route for opening it.

**Call relations**: Click calls this for `ufoctl seed kitchen-sink`. It delegates database setup and content writing to `_seed_kitchen_sink`.

*Call graph*: calls 1 internal fn (_seed_kitchen_sink); 3 external calls (run, echo, load_config).


##### `_seed_kitchen_sink`  (lines 1024–1035)

```
async def _seed_kitchen_sink(config: Config, named: str) -> UUID
```

**Purpose**: Sets up the database and blob store needed to write the kitchen-sink demo conversation. A blob store is where larger file-like content is saved.

**Data flow**: It initializes app and possibly owner database access, creates a blob store from config, calls `_seed_target`, returns the conversation ID, and disposes database resources.

**Call relations**: `seed_kitchen_sink` calls this. It prepares shared resources, while `_seed_target` chooses the workspace and writes the actual sample content.

*Call graph*: calls 1 internal fn (_seed_target); called by 1 (seed_kitchen_sink); 4 external calls (blob_store_for, dispose_db, init_db, init_owner_db).


##### `_seed_target`  (lines 1038–1069)

```
async def _seed_target(blob: BlobStore, named: str) -> UUID
```

**Purpose**: Writes the kitchen-sink demo conversation into one selected workspace. It finds the main agent and an existing member to author the sample.

**Data flow**: It resolves the workspace with `_target_workspace`, enters that workspace context, reads the main agent ID and earliest member, raises if no member exists, creates a `KitchenSink` writer with blob, workspace, agent, member, and email, and returns the conversation ID it writes.

**Call relations**: `_seed_kitchen_sink` calls this after setting up database and blob access. It hands the actual demo-content creation to the `KitchenSink` seed helper.

*Call graph*: calls 1 internal fn (_target_workspace); called by 1 (_seed_kitchen_sink); 5 external calls (__init__, ClickException, select, workspace_tx, ws).


### `control/src/ufo_control/main.py`

`entrypoint` · `startup and administrator command execution`

This file is the place an operator goes when they need to run the hosted shared-workspace service or prepare its database. It uses Click, a Python command-line tool library, to define several commands under one main program. Think of it like a control panel with buttons: one button starts the gateway web server, another reshapes the database schema, another grants and emails an invitation, and others repair or bootstrap specific platform state.

At startup, the main command sets up normal logging. If the environment provides an OpenTelemetry log endpoint, it also sends logs to a central collector. OpenTelemetry is a standard way to collect service logs and telemetry from running systems.

The gateway command starts the FastAPI-style application through Uvicorn, the web server. The migration and bootstrap commands connect as the database owner and make sure the control database has the expected tables, roles, and row-level security policies. Row-level security means the database itself helps prevent one workspace from seeing another workspace’s data.

The invitation command is careful: it checks the schema, builds the email sender, creates a one-use grant in the database, and then sends the email. If the email send fails after the grant is created, it tells the operator that the grant still exists instead of silently undoing it.

#### Function details

##### `main`  (lines 38–41)

```
def main() -> None
```

**Purpose**: This is the top-level command group for operating the hosted service. It prepares logging before any specific subcommand runs, so operators get useful output no matter which command they choose.

**Data flow**: It reads the log-export endpoint from the process environment and configures basic console logging. If a remote log endpoint is present, it passes that value onward so logs can also be shipped to the platform collector. It does not return data; it prepares the process for the selected command.

**Call relations**: Click calls this group when the command-line program starts. During that setup, it calls `_export_logs` after configuring standard logging, so every later command benefits from the same logging setup.

*Call graph*: calls 1 internal fn (_export_logs); 1 external calls (basicConfig).


##### `_export_logs`  (lines 44–54)

```
def _export_logs(otlp_endpoint: str | None) -> None
```

**Purpose**: This function optionally sends the program’s logs to an OpenTelemetry collector, which is a central place for gathering service logs. If no endpoint is configured, it deliberately does nothing and leaves logs on standard output only.

**Data flow**: It receives either a log collector URL or `None`. With `None`, it exits immediately. With a URL, it creates an OpenTelemetry logger provider, points an exporter at the collector’s log path, wraps that exporter in a batch processor so logs are sent efficiently, and then asks `_install_root_handler` to connect Python logging to that provider.

**Call relations**: `main` calls this during command startup. When log exporting is enabled, `_export_logs` builds the OpenTelemetry pieces and hands them to `_install_root_handler`, which attaches them to the normal Python logging system.

*Call graph*: calls 1 internal fn (_install_root_handler); called by 1 (main); 4 external calls (OTLPLogExporter, LoggerProvider, BatchLogRecordProcessor, create).


##### `_install_root_handler`  (lines 57–62)

```
def _install_root_handler(logger_provider: LoggerProvider) -> None
```

**Purpose**: This connects ordinary Python log messages to the OpenTelemetry logging pipeline. It also avoids feeding OpenTelemetry’s own internal errors back into itself, which could otherwise create a noisy loop.

**Data flow**: It receives a configured OpenTelemetry logger provider. It creates a logging handler that forwards records to that provider, adds a filter that rejects records from loggers whose names start with `opentelemetry`, and attaches the handler to the root logger. The result is a changed global logging setup rather than a returned value.

**Call relations**: `_export_logs` calls this after it has built the remote logging pipeline. From then on, later commands and server code can log normally, and those messages will also flow to the collector when configured.

*Call graph*: called by 1 (_export_logs); 2 external calls (getLogger, LoggingHandler).


##### `gateway`  (lines 66–71)

```
def gateway() -> None
```

**Purpose**: This command starts the hosted gateway web service. That gateway serves onboarding, fleet count information, and the terminal client.

**Data flow**: It reads the gateway port from the environment, falling back to the default port if none is set. It then starts Uvicorn with the application named `ufo_control.gateway:app`, listening on all network interfaces. The function does not return while the server is running; the web server takes over the process.

**Call relations**: Click runs this when an operator chooses the `gateway` command. It hands control to `uvicorn.run`, which imports the gateway application and runs the web server.

*Call graph*: 1 external calls (run).


##### `migrate`  (lines 75–78)

```
def migrate() -> None
```

**Purpose**: This command brings the control database schema up to the expected shape. Operators use it when deploying or upgrading the hosted service so the database has the ledgers and structures the gateway expects.

**Data flow**: It asks for the database owner connection string, then runs the asynchronous schema-shaping work inside `asyncio.run`, which is the bridge from normal command-line code into async database code. When the schema update finishes, it prints a short confirmation message.

**Call relations**: Click runs this for the `migrate` command. It calls `owner_dsn` to get privileged database access, hands that to `shape_control_schema`, and uses `click.echo` to report success to the operator.

*Call graph*: 4 external calls (run, echo, owner_dsn, shape_control_schema).


##### `invite`  (lines 84–91)

```
def invite(object_number: int, email: str) -> None
```

**Purpose**: This command grants a waitlist object’s email domain one new workspace and emails the invitation to the given address. It gives the operator a safe, clear way to issue onboarding invites.

**Data flow**: It receives an object number and an email address from the command line. It calls `_mint_invite` through `asyncio.run` to do the database and email work. If known invitation or work-email errors occur, it turns them into a Click-friendly command error; otherwise it prints who was granted access and when the invite expires.

**Call relations**: Click calls this when an operator runs the invite command. The real work is delegated to `_mint_invite`; this wrapper translates results and errors into command-line messages.

*Call graph*: calls 1 internal fn (_mint_invite); 3 external calls (run, ClickException, echo).


##### `_mint_invite`  (lines 94–116)

```
async def _mint_invite(object_number: int, email: str) -> MintedInvite
```

**Purpose**: This performs the actual invitation workflow: verify the system is ready, create the grant in the database, and send the invitation email. It is careful to avoid spending a grant if email configuration is missing before the grant is created.

**Data flow**: It receives the waitlist object number and target email address. It reads the public host name and owner database connection string, confirms the control schema exists, builds the configured email sender, opens a small database connection pool, and mints the invite through `InviteCodes`. After closing the pool, it builds the invitation email and sends it. On success it returns the minted invitation details; if sending fails after the grant exists, it raises a command error that explains the grant still stands.

**Call relations**: `invite` calls this as the asynchronous worker behind the command. It relies on gateway email helpers for sender setup and message text, schema helpers for database safety checks, and `InviteCodes` for the database grant itself.

*Call graph*: called by 1 (invite); 8 external calls (__init__, create_pool, ClickException, email_sender_from_env, invite_email, public_apex_host, owner_dsn, require_control_schema).


##### `slack_connect_retry`  (lines 121–127)

```
def slack_connect_retry(onboard_claim_id: uuid.UUID) -> None
```

**Purpose**: This command re-arms a failed Slack Connect delivery for one onboarding claim after an operator has fixed the underlying problem. Slack Connect is Slack’s way to connect separate workspaces through a shared channel.

**Data flow**: It receives an onboarding claim ID from the command line. It calls `_rearm_slack_connect` through `asyncio.run`. If no failed delivery is found, it raises a clear command error; if one is found, it prints when the delivery had been failing since.

**Call relations**: Click calls this for the `slack-connect-retry` command. It delegates the database change to `_rearm_slack_connect`, then turns the result into either an operator-facing error or a success message.

*Call graph*: calls 1 internal fn (_rearm_slack_connect); 3 external calls (run, ClickException, echo).


##### `_rearm_slack_connect`  (lines 130–137)

```
async def _rearm_slack_connect(onboard_claim_id: uuid.UUID) -> datetime | None
```

**Purpose**: This does the database work needed to mark one failed Slack Connect delivery as ready to try again. It returns the time the delivery originally failed, or nothing if there was no matching failed delivery.

**Data flow**: It receives an onboarding claim UUID. It gets the owner database connection string, checks that the control schema exists, opens a small database connection pool, and calls `rearm_failed_delivery` with that pool and claim ID. It closes the pool before returning the failure timestamp or `None`.

**Call relations**: `slack_connect_retry` calls this as its asynchronous helper. This function handles setup and cleanup around the lower-level `rearm_failed_delivery` operation, which performs the actual retry-state change.

*Call graph*: called by 1 (slack_connect_retry); 4 external calls (create_pool, rearm_failed_delivery, owner_dsn, require_control_schema).


##### `rls_bootstrap`  (lines 141–144)

```
def rls_bootstrap() -> None
```

**Purpose**: This command creates or refreshes the database role and row-level security policies needed by the shared service. Operators use it so the database enforces workspace boundaries correctly.

**Data flow**: It takes no command-line data. It runs `_bootstrap` through `asyncio.run`, then prints a confirmation once the role and policies are in place.

**Call relations**: Click calls this for the `rls-bootstrap` command. It keeps the command-line layer small and hands the actual database setup to `_bootstrap`.

*Call graph*: calls 1 internal fn (_bootstrap); 2 external calls (run, echo).


##### `_bootstrap`  (lines 147–150)

```
async def _bootstrap() -> None
```

**Purpose**: This applies the database access-policy setup needed by the service. It creates or updates the shared serve role and the workspace isolation policies.

**Data flow**: It gets the owner database connection string, then passes it to `bootstrap_policies` and `ensure_serve_role`. Those calls change database state; this function returns no value after they complete.

**Call relations**: `rls_bootstrap` calls this as the asynchronous worker behind the command. It coordinates the two lower-level security setup steps: policies first, then the serve role.

*Call graph*: called by 1 (rls_bootstrap); 3 external calls (bootstrap_policies, ensure_serve_role, owner_dsn).


### Deployment bundling
Bundle creation freezes the configured deployment into a reproducible Docker-based artifact.

### `core/src/ufo/bundle.py`

`domain_logic` · `bundle creation / packaging time`

This file is the packaging step behind `ufoctl bundle`. Its job is to turn a working UFO setup into a repeatable container build context. Think of it like packing a suitcase for a trip: it copies the needed configuration, writes a list of exactly which extensions are allowed, and adds instructions for Docker to build an image that can run the app later.

The important safety idea is “pinning.” A pin records an extension by name and verifies the installed version or digest, so the bundle does not accidentally depend on whatever happens to be installed at runtime. If there is already a lockfile, the bundle starts from the extensions named there. If not, it starts from the extensions currently discovered in the environment. It can also add catalog entries marked as “bundle-only,” meaning they are installed into the bundle but not enabled through the normal runtime store path.

The `Bundle.build` method creates the output directory, copies the config as `ufo.toml`, writes a fresh `ufo.lock`, and writes a Dockerfile. That Dockerfile installs the UFO wheel from the build context, points UFO at the bundled config and lockfile through environment variables, and defaults to running `ufoctl serve`.

#### Function details

##### `wheel_name`  (lines 25–28)

```
def wheel_name() -> str
```

**Purpose**: Builds the expected filename for the UFO Python wheel that will be copied into the Docker image. A wheel is Python’s installable package format, and this project expects the wheel to sit beside the generated Docker build files.

**Data flow**: It reads the current UFO version from the extension store helper, places that version into the standard wheel filename pattern, and returns the resulting string, such as a versioned `ufo-...-py3-none-any.whl` name. It does not change any files or state.

**Call relations**: When `Bundle._dockerfile` writes Docker instructions, it calls this function so the Dockerfile copies and installs the exact wheel name that matches the current UFO version.

*Call graph*: called by 1 (_dockerfile); 1 external calls (ufo_version).


##### `Bundle.build`  (lines 49–62)

```
def build(self) -> BundleResult
```

**Purpose**: Creates the actual bundle folder. It gathers the extension pins, copies the configuration, writes the lockfile, writes the Dockerfile, and returns a summary of what it created.

**Data flow**: It starts with a `Bundle` object containing the source config path, optional extension catalog, and output folder. It asks `_pins` for the frozen extension list, creates the output directory if needed, copies the config text into `ufo.toml`, writes a JSON lockfile using the current UFO version and pins, writes the Dockerfile text from `_dockerfile`, and returns a `BundleResult` containing the paths and pins.

**Call relations**: This is the main action for the file. A higher-level command such as `ufoctl bundle` would call it when the user wants a bundle. Inside, it delegates the two main pieces of thinking: `_pins` decides what extensions belong in the frozen bundle, and `_dockerfile` produces the container build instructions.

*Call graph*: calls 2 internal fn (_dockerfile, _pins); 3 external calls (__init__, __init__, ufo_version).


##### `Bundle._pins`  (lines 64–78)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: Decides which extensions must be pinned into the bundle. This is what makes the bundle repeatable instead of depending on whatever extensions happen to be available later.

**Data flow**: It first asks the extension loader what extensions are installed or discoverable now. Then it checks for an existing lockfile. If a lockfile exists, it uses the extension names already listed there; if not, it uses every discovered extension. If a catalog is available, it also adds extensions marked disabled in the catalog, because those are bundle-only additions. It removes duplicate names while keeping order, then turns each name into a verified pin and returns all pins as an immutable tuple.

**Call relations**: It is called by `Bundle.build` before any files are written. It relies on loader helpers to inspect the current environment and lockfile, then hands each chosen extension name to `pin_for`, which performs the pinning check. The resulting pins are later written into the generated lockfile.

*Call graph*: called by 1 (build); 4 external calls (discovered, lockfile_path, read_lockfile, pin_for).


##### `Bundle._dockerfile`  (lines 80–94)

```
def _dockerfile(self) -> str
```

**Purpose**: Creates the text of the Dockerfile used to build the runnable container image. The Dockerfile tells Docker which base Python image to use, how to install UFO, where to find the bundled config and lockfile, and what command to run by default.

**Data flow**: It uses fixed bundle filenames and asks `wheel_name` for the versioned UFO wheel filename. It then joins a set of Dockerfile lines into one string. The result is only text; writing it to disk is done later by `Bundle.build`.

**Call relations**: It is called by `Bundle.build` when the bundle folder is being written. It calls `wheel_name` so the Dockerfile’s `COPY`, `pip install`, and cleanup steps all refer to the same expected wheel file.

*Call graph*: calls 1 internal fn (wheel_name); called by 1 (build).


### Service runtimes
Runtime entrypoints start the shared proxy and assemble the main UFO web service with its databases, routes, and background workers.

### `core/src/ufo/proxy_serve.py`

`entrypoint` · `startup and main loop`

A sandbox needs controlled access to the outside world: model providers, artifact storage, connector services, and any approved internet destinations. This file is the startup point for the standalone shared proxy that provides that access. Without it, sandbox traffic would either be blocked, leak secrets, or bypass the project’s workspace boundaries.

The proxy is shared across workspaces, so it cannot rely on one fixed workspace setting. Instead, each request carries a run token, and that token tells the proxy which workspace it belongs to. The proxy then looks up only the grants, credentials, and rules for that workspace.

At startup, the file loads configuration, extension manifests, database access, certificate material, model pricing, and any credential-decryption key. It deliberately fails early if required secrets are missing. That is safer than starting a proxy that appears to work but cannot reach model providers, cannot decrypt connector credentials, or signs certificates that sandboxes will not trust.

The main `ProxyServe` class then opens the owner database connection, builds the shared base rules, adds per-workspace rule resolution, creates the actual `EgressProxy`, and waits until the process is asked to shut down. In simple terms, this file is the front desk for sandbox network access: it checks who is asking, finds what they are allowed to reach, swaps in the right secrets, and keeps the proxy running.

#### Function details

##### `model_rule_base`  (lines 43–62)

```
def model_rule_base(config: Config) -> tuple[Rule, ...]
```

**Purpose**: This builds the basic network rules that allow sandboxes to contact configured model providers, such as Anthropic or OpenAI. It only allows providers whose API keys are present in the process environment, and it refuses to continue if no model provider is usable.

**Data flow**: It takes the loaded configuration, reads the environment variable names where model API keys should live, and checks the real process environment for those keys. For each key it finds, it asks the model-rule builder to produce safe proxy rules, combines the allowed host names into one host rule, and returns the full set of rules. If no model hosts can be allowed, it raises an error instead of returning an empty rule set.

**Call relations**: During proxy startup, `ProxyServe.serve` calls this to create the model-provider part of the proxy’s base rule set. The helper delegates the provider-specific rule details to `derive_model_rules`, then hands the resulting rules back so they can be combined with artifact-store and workspace-specific rules.

*Call graph*: called by 1 (serve); 2 external calls (__init__, derive_model_rules).


##### `run`  (lines 65–86)

```
def run() -> None
```

**Purpose**: This is the top-level boot function for the standalone proxy service. It gathers all required settings and secrets, creates a `ProxyServe` instance, and runs it forever until shutdown.

**Data flow**: It starts by loading configuration and initializing observability, which is the system that sends logs and telemetry. It loads extension manifests, reads the shared certificate authority, chooses the owner database connection string, opens the credential store if needed, builds model pricing, and creates a shutdown event. Those pieces are packaged into `ProxyServe`, then `asyncio.run` starts the asynchronous serving loop.

**Call relations**: This function is the outer story for the whole file. It calls `_egress_ca`, `owner_dsn`, and `_credential_store` to validate important secrets before the server starts, then hands control to `ProxyServe.serve`, which performs the actual database setup and proxy listening.

*Call graph*: calls 3 internal fn (_credential_store, _egress_ca, owner_dsn); 9 external calls (__init__, Event, run, load_config, injecting_slots, load_manifests, model_registry, init_o11y, log).


##### `_egress_ca`  (lines 89–100)

```
def _egress_ca() -> tuple[str, str]
```

**Purpose**: This reads the shared certificate authority used by the proxy to sign temporary certificates for sandbox traffic. The shared certificate authority matters because sandboxes must trust the same signing chain across proxy restarts.

**Data flow**: It reads the certificate and private key from two environment variables. If both are present, it returns them as text. If either is missing, it raises an error explaining that the proxy cannot safely sign certificates that sandboxes will trust.

**Call relations**: `run` calls this during startup before creating `ProxyServe`. The returned certificate and key are later passed into `EgressProxy`, which uses them when it intercepts and protects outbound sandbox connections.

*Call graph*: called by 1 (run).


##### `owner_dsn`  (lines 103–116)

```
def owner_dsn(config: Config) -> str
```

**Purpose**: This chooses the database connection string for the shared proxy. It uses an owner-level database connection because one proxy serves all workspaces, while the proxy’s own queries still filter by the workspace ID from each run token.

**Data flow**: It first looks for the owner database URL in the `UFO_OWNER_DSN` environment variable, then falls back to the configured owner URL. If neither exists, it raises an error. If it finds a URL starting with `postgresql://`, it rewrites it to use the async PostgreSQL driver expected by this service, then returns the rewritten string.

**Call relations**: `run` calls this at startup and passes the result into `ProxyServe`. Later, `ProxyServe.serve` uses that string to initialize database access before the proxy begins listening.

*Call graph*: called by 1 (run).


##### `_credential_store`  (lines 119–133)

```
def _credential_store(config: Config, slots: tuple[CredentialSlot, ...]) -> CredentialStore | None
```

**Purpose**: This decides whether the proxy needs a credential store for connector secrets, and opens it if possible. The credential store decrypts per-workspace secrets that the proxy may need to inject into outbound requests.

**Data flow**: It receives the configuration and the list of credential slots that require injection. It reads the configured encryption key environment variable. If the key exists, it creates a Fernet encryptor/decryptor and wraps it in a `CredentialStore`. If no key exists but the active pack declares credential slots, it raises an error. If no key is needed, it returns `None`.

**Call relations**: `run` calls this after loading manifests and discovering which credential slots need injection. The returned store, or `None`, is passed into `ProxyServe`, and later into `PerAgentRules`, so workspace-specific secrets can be decrypted when a sandbox request needs them.

*Call graph*: called by 1 (run); 2 external calls (__init__, Fernet).


##### `ProxyServe.serve`  (lines 151–182)

```
async def serve(self) -> None
```

**Purpose**: This starts the actual shared proxy and keeps it alive until the process receives a shutdown signal. It prepares database access, builds rule resolution, starts listening on the configured port, and shuts down gracefully.

**Data flow**: It registers signal handlers so Ctrl-C or a termination signal sets the shutdown event. It initializes the database connection, verifies the database is reachable, builds artifact-store rules, builds model-provider rules, and creates a `PerAgentRules` resolver that can decide what each sandbox run may access. It then creates an `EgressProxy` with certificate material, run-token decoding, authorization checks, and pricing information. The proxy starts on the configured port, waits for shutdown, and finally stops with the configured grace period.

**Call relations**: `run` creates the `ProxyServe` object and then calls this method through the asynchronous event loop. Inside, it calls `model_rule_base` for shared model access, uses manifest-derived helpers for connector and internet rules, creates `PerAgentRules` to resolve per-run permissions, and hands that resolver into `EgressProxy`, which performs the live network proxy work.

*Call graph*: calls 2 internal fn (model_rule_base, from_env); 13 external calls (__init__, __init__, __init__, get_running_loop, blob_store_for, init_db, verify_db_reachable, connector_clis, injecting_slots, log (+3 more)).


### `core/src/ufo/serve.py`

`entrypoint` · `startup, request handling, background work, shutdown`

This file is the place where many separate parts of the system are plugged together into one running service. Think of it like the control room that turns on power, checks safety switches, connects the phone lines, starts the workers, and then opens the front door to users.

At startup, it loads configuration, sets up logging, initializes the main and owner databases, verifies encryption keys for stored credentials, loads extension manifests, creates the blob store for large files, chooses providers such as the live-message hub, model registry, search, memory, browser control, connector authentication, and sandbox egress proxy, then registers durable jobs. It also starts a heartbeat so other service instances know this one is alive.

The file mounts two kinds of web routes. Extension routes live under `/ext/...` and are only allowed to run after the request is tied to a workspace. Shared surface routes live under `/surface/...` and are the member-facing API or UI entry points. A workspace is the tenant boundary: every database read and credential lookup must happen with the right workspace selected. The `WorkspaceScopeBoundary` middleware makes sure that no stale workspace leaks between requests.

Finally, it runs Uvicorn, the web server. On shutdown it carefully drains or preserves durable work so another process does not accidentally run the same workflow at the same time.

#### Function details

##### `_assert_no_reserved_routes`  (lines 154–170)

```
def _assert_no_reserved_routes(app: FastAPI) -> None
```

**Purpose**: This is a startup safety check. It makes sure this service has not mounted routes under URL prefixes that belong to the onboarding or login gateway.

**Data flow**: It reads the FastAPI app's registered routes, looks for paths starting with reserved prefixes such as login and onboarding paths, and raises an error if any conflict is found. If there are no conflicts, it changes nothing and returns normally.

**Call relations**: The main `run` function calls this after all routes have been mounted. It is the final guard before the server starts accepting traffic.

*Call graph*: called by 1 (run).


##### `run`  (lines 173–340)

```
def run() -> None
```

**Purpose**: This is the main entry point for the shared service process. It builds every major runtime dependency, starts durable workers, mounts web endpoints, and launches the HTTP server.

**Data flow**: It begins with configuration and environment variables, then turns those into live objects: database connections, credential encryption, extension registries, blob storage, sandboxes, hubs, model and memory backends, connector registries, job runners, and a FastAPI app. The result is a running web server plus background workers; on exit it shuts down the DBOS executor and heartbeat carefully.

**Call relations**: This function is the top-level conductor. It calls the helper functions in this file to choose backends, mount routes, register jobs, configure proxy access, and clean up during shutdown.

*Call graph*: calls 18 internal fn (from_env, _assert_no_reserved_routes, _connect_flow, _connector_registry, _launch_jobs, _mount_ext_routes, _mount_shared_surfaces, _one_shot, _proxy_endpoint, _select_cdp_provider (+8 more)); 49 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


##### `run.invoker_for`  (lines 215–216)

```
def invoker_for(workspace_id: UUID) -> AdmissionInvoker
```

**Purpose**: This small helper creates an admission invoker for one workspace. An admission invoker is the object that lets work be admitted into the durable workflow system for that workspace.

**Data flow**: It receives a workspace ID, combines it with the already-created shared `Admission` object, and returns an `AdmissionInvoker` tied to that workspace.

**Call relations**: It is defined inside `run` so it can reuse the shared admission setup. `run` passes it into the runtime and job-launch code so later work can be admitted under the correct workspace.

*Call graph*: 1 external calls (__init__).


##### `_one_shot`  (lines 343–355)

```
def _one_shot(coro: Coroutine[Any, Any, T]) -> T
```

**Purpose**: This helper runs one asynchronous database-touching operation on a temporary event loop, then cleans up database engines attached to that loop. It prevents database connection pools from being stranded on a loop that has already closed.

**Data flow**: It receives a coroutine, runs it to completion with `asyncio.run`, and returns the coroutine's result. Before the temporary loop closes, it disposes the loop's database engines.

**Call relations**: `run` uses it for one-time startup database checks and records. `_stop_executor` uses it during shutdown to retire the heartbeat seat safely.

*Call graph*: called by 2 (_stop_executor, run); 1 external calls (run).


##### `_one_shot.step`  (lines 349–353)

```
async def step() -> T
```

**Purpose**: This inner async function wraps the actual one-shot operation with cleanup. Its purpose is to guarantee database engine cleanup even if the operation fails.

**Data flow**: It awaits the supplied coroutine, returns its result if successful, and always calls database engine disposal afterward. If the coroutine raises an error, cleanup still runs before the error continues outward.

**Call relations**: It is only used by `_one_shot`. It is the part that actually performs the before-and-after cleanup promise.

*Call graph*: 1 external calls (dispose_loop_engines).


##### `_stop_executor`  (lines 358–372)

```
def _stop_executor(dbos: DBOS, heartbeat: Heartbeat, graceful_shutdown_seconds: int) -> None
```

**Purpose**: This shuts down the durable workflow executor without allowing duplicate workflow execution. It retires this process's worker seat only if no workflows are still active.

**Data flow**: It receives the DBOS executor, heartbeat object, and allowed graceful shutdown time. It asks DBOS to drain work, checks whether any workflow tasks are still active, logs and keeps the seat if work remains, or retires the heartbeat seat if the executor is empty.

**Call relations**: `run` calls this in its final shutdown block. It uses `_one_shot` to run the asynchronous heartbeat retirement in a safe temporary loop.

*Call graph*: calls 2 internal fn (retire, _one_shot); called by 1 (run); 2 external calls (destroy, log).


##### `_shared_owner_dsn`  (lines 375–389)

```
def _shared_owner_dsn(config: Config) -> str
```

**Purpose**: This finds the special database connection string used for owner-level cross-workspace reads. That owner connection is needed for sweep jobs that first list workspaces before rebinding each item to its own workspace.

**Data flow**: It reads either the owner database URL environment variable or the configured owner URL. If neither exists, it raises a clear startup error; otherwise it returns the chosen connection string.

**Call relations**: `run` calls this before initializing the owner database. Without this value, shared-fleet background sweeps could not safely enumerate work across all workspaces.

*Call graph*: called by 1 (run).


##### `_launch_jobs`  (lines 392–446)

```
def _launch_jobs(runtime: Runtime, invoker_for: InvokerFactory, sync_driver: SyncDriver, page_feed: CorePageFeed) -> None
```

**Purpose**: This registers and starts the durable background jobs for core and installed extensions. These jobs synchronize sources, dispatch queued turns, process page changes, and deliver delegated work.

**Data flow**: It receives the already-built runtime, an invoker factory, a sync driver, and a page feed. It builds probe helpers, page-change runners, core job bindings, and finally launches a `JobRunner` that registers the jobs with DBOS.

**Call relations**: `run` calls this after the runtime and DBOS client are ready. It hands off to the job system so background work can continue independently of individual web requests.

*Call graph*: called by 1 (run); 12 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, connector_clis, injecting_slots (+2 more)).


##### `_source_backends`  (lines 449–463)

```
def _source_backends(manifests: tuple[Manifest, ...]) -> dict[str, SourceBackend]
```

**Purpose**: This builds the list of source-sync backends available to the sync driver. A source backend is the code that knows how to read one kind of external content source.

**Data flow**: It starts with the built-in folder source backend, then reads every extension manifest for additional source providers. It gives each provider credential access limited to that extension's declared credential slots and returns a name-to-backend map, raising an error if two providers claim the same name.

**Call relations**: `run` uses this when creating the `SyncDriver`. The returned map tells source synchronization which implementation to use for each stored source row.

*Call graph*: called by 1 (run); 2 external calls (__init__, __init__).


##### `_source_identity_resolvers`  (lines 466–506)

```
def _source_identity_resolvers(manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: BlobStore) -> dict[str, SourceIdentityResolver]
```

**Purpose**: This prepares optional functions that can identify the current user for a source-connected surface. It lets surfaces say, for example, which external account belongs to a workspace.

**Data flow**: It scans extension surfaces for a `self_user_id` hook. For each one, it creates a resolver that can read only the credentials declared by that surface's extension and returns a map from surface name to resolver.

**Call relations**: `run` gives these resolvers to the sync driver. The nested resolver functions are later called when source synchronization needs to connect workspace identity to a surface.

*Call graph*: called by 1 (run).


##### `_source_identity_resolvers.resolve`  (lines 480–503)

```
async def resolve(workspace_id: UUID, handler=surface.self_user_id, slots=declared, store=credentials) -> str | None
```

**Purpose**: This per-surface resolver calls an extension's identity hook for one workspace. It packages the workspace, blob store, and safe credential reader into a context the extension can use.

**Data flow**: It receives a workspace ID, builds a `SurfaceIdentityContext`, and calls the surface's identity handler. The result is either an external user ID string or no value if the surface cannot identify one.

**Call relations**: It is created by `_source_identity_resolvers` and later used by source synchronization. It delegates credential reads to its nested `credential` function so extensions cannot read undeclared secrets.

*Call graph*: 1 external calls (__init__).


##### `_source_identity_resolvers.resolve.credential`  (lines 486–495)

```
async def credential(credential_slot: str) -> str
```

**Purpose**: This nested helper safely reads one credential slot for a source identity lookup. It enforces that the extension only asks for credentials it declared.

**Data flow**: It receives a credential slot name, checks that the slot is allowed, checks that a credential store exists, then fetches the secret for the current workspace. It returns the credential value or raises a clear error if access is invalid.

**Call relations**: It is used inside `_source_identity_resolvers.resolve`. It is the guardrail between extension identity code and stored workspace credentials.


##### `_select_hub`  (lines 509–527)

```
def _select_hub(config: Config, manifests: tuple[Manifest, ...]) -> Hub
```

**Purpose**: This chooses the live-message hub backend for the process. The hub is the channel used to move live updates between workflows and connected clients.

**Data flow**: It starts with the built-in in-process hub option, adds hub builders registered by extensions, checks for duplicate names, and selects the configured backend. It returns the built hub or raises an error if the configured name is unknown.

**Call relations**: `run` calls this while assembling the runtime. The chosen hub is later used by surfaces, tailers, and stopping logic to communicate live state.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_terminal_transport`  (lines 530–572)

```
def _select_terminal_transport(config: Config, manifests: tuple[Manifest, ...], blob: BlobStore) -> TerminalTransport
```

**Purpose**: This chooses how terminal sessions are connected between users and running work. It also prevents an unsafe setup where multiple service instances use a process-local terminal transport.

**Data flow**: It reads terminal and hub configuration, rejects an in-process terminal transport when the hub is cross-process, gathers terminal transport builders from extensions, and returns the selected transport. Duplicate or unknown backend names cause startup errors.

**Call relations**: `run` uses this when constructing sandbox support. The selected transport lets a user's held terminal connection find the workflow that owns the terminal.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_cdp_provider`  (lines 575–603)

```
def _select_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> CdpProvider | None
```

**Purpose**: This chooses the browser automation provider, if one is installed and selected. CDP means Chrome DevTools Protocol, a way for software to control a browser.

**Data flow**: It scans extension manifests for CDP provider registrations, checks for duplicate backend names, looks up the configured provider, verifies credentials are available if needed, and returns a built provider or `None` if no active extension supplies it.

**Call relations**: `run` calls this while building the runtime. `_require_cdp_provider` also calls it during extension requirement checks to fail early when a browser extension needs it.

*Call graph*: called by 2 (_require_cdp_provider, run); 1 external calls (__init__).


##### `_validate_requires`  (lines 606–630)

```
def _validate_requires(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: This checks every extension's declared runtime requirements at startup. It turns missing providers into clear boot errors instead of surprising failures during the first user action.

**Data flow**: It reads each manifest's `requires` list, looks up the matching checker, and runs it against the current config, manifests, and credential store. If a seam is unknown or unavailable, it raises an error naming the extension and missing requirement.

**Call relations**: `run` calls this after loading manifests and credentials. It dispatches to requirement helpers such as `_require_cdp_provider`, `_require_search_provider`, and `_require_memory_search`.

*Call graph*: called by 1 (run).


##### `_require_cdp_provider`  (lines 633–647)

```
def _require_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: This enforces that a browser-control provider is available when an extension requires one. It makes browser features fail at startup if the provider is missing.

**Data flow**: It asks `_select_cdp_provider` to resolve the configured provider. If that returns `None`, it raises an error explaining that the required provider is not registered.

**Call relations**: `_validate_requires` calls this when an extension declares the `cdp_providers` requirement. It reuses the same selection logic that `run` uses for the actual runtime.

*Call graph*: calls 1 internal fn (_select_cdp_provider).


##### `_select_search_provider`  (lines 650–685)

```
def _select_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> SearchProvider | None
```

**Purpose**: This chooses the research search backend, if configured. A search provider is the service used by research tools to query the web or another search system.

**Data flow**: It scans extension manifests for search providers, checks for duplicate names, returns `None` if search is not configured, or builds the selected provider with scoped credential access. Unknown names or missing credential support raise startup errors.

**Call relations**: `run` calls this to put search capability into the runtime. `_require_search_provider` calls it when an extension says search is mandatory.

*Call graph*: called by 2 (_require_search_provider, run); 2 external calls (__init__, __init__).


##### `_require_search_provider`  (lines 688–701)

```
def _require_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: This enforces that research tools have a configured and working search provider. It prevents a research extension from loading without the backend it needs.

**Data flow**: It checks that the search provider setting is present, then calls `_select_search_provider` to verify and build it. If anything is missing or invalid, it raises a startup error.

**Call relations**: `_validate_requires` calls this for extensions that require `search_providers`. It uses the normal search selection helper so validation and runtime behavior match.

*Call graph*: calls 1 internal fn (_select_search_provider).


##### `_require_memory_search`  (lines 704–730)

```
def _require_memory_search(_config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: This enforces that exactly one default memory-search provider is available when required. Memory search is the feature that lets the system retrieve stored memories or indexed content.

**Data flow**: It scans manifests for the default memory-search provider name, rejects none or multiple matches, and checks that credentials exist if the provider's extension declares credential slots. It returns nothing if the requirement is satisfied.

**Call relations**: `_validate_requires` calls this when an extension declares the `memory_search` requirement. It is a startup-only readiness check.


##### `_select_auth_proxy`  (lines 742–779)

```
def _select_auth_proxy(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> AuthProxy | None
```

**Purpose**: This chooses the fallback authentication proxy for connectors. That proxy helps connector sync read credentials in a controlled host-side way when a connector does not have its own broker.

**Data flow**: It gathers auth proxy registrations from manifests, checks duplicates, chooses the configured backend or the only available backend, verifies a credential store exists, and builds the proxy with scoped credential access. Ambiguous, unknown, or unkeyed choices raise errors.

**Call relations**: `_connector_registry` calls this while building the connector registry. The returned proxy becomes the fallback path for connector credential resolution.

*Call graph*: called by 1 (_connector_registry); 2 external calls (__init__, __init__).


##### `_mount_ext_routes`  (lines 782–820)

```
def _mount_ext_routes(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, index: IndexBackend, embed: EmbedClient) -> None
```

**Purpose**: This mounts extension-provided HTTP routes under `/ext/<extension>/...`. It makes sure each request is identified and bound to a workspace before extension code can run.

**Data flow**: It reads extension route declarations, creates an extension context for each manifest, and adds FastAPI routes. If an extension serves routes but credentials are not configured, it raises an error.

**Call relations**: `run` calls this after core setup and before the server starts. The nested `endpoint` function is the actual request wrapper for each mounted extension route.

*Call graph*: called by 1 (run); 2 external calls (add_route, context_for).


##### `_mount_ext_routes.endpoint`  (lines 804–814)

```
async def endpoint(request: Request, handler=spec.handler, identify=spec.identify, extension_context=context) -> Response
```

**Purpose**: This is the per-request wrapper for one extension route. It verifies which workspace the request belongs to before calling the extension's handler.

**Data flow**: It receives a web request, asks the route's identify function for a workspace, returns a 401 response if identification fails, otherwise binds that workspace and calls the extension handler with its context and request. The handler's response is returned to the client.

**Call relations**: FastAPI calls this when a matching `/ext/...` route receives a request. It is created by `_mount_ext_routes` and uses the workspace binding helper so downstream code reads the right tenant data.

*Call graph*: 2 external calls (Response, ws).


##### `WorkspaceScopeBoundary.__call__`  (lines 841–849)

```
async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None
```

**Purpose**: This middleware clears workspace state at the start and end of every HTTP request. It protects one workspace's data from leaking into another request through leftover context.

**Data flow**: It receives the ASGI request scope and send/receive functions. For non-HTTP traffic it passes through unchanged; for HTTP it clears the current workspace, lets the app handle the full response, and clears the workspace again in a final cleanup step.

**Call relations**: `_mount_shared_surfaces` installs this middleware on the FastAPI app. Shared surface endpoints set the workspace during a request, and this boundary guarantees cleanup after streaming or errors.

*Call graph*: 1 external calls (set).


##### `_mount_shared_surfaces`  (lines 852–1008)

```
def _mount_shared_surfaces(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: BlobStore, sandboxes: ConversationSandbox, hub: Hub, dbos_client: DBOSClient, artif
```

**Purpose**: This mounts the shared member-facing surface routes under `/surface/...`. A surface is a user-facing integration point, such as a web UI or chat-like entry point, and each request must resolve to a workspace.

**Data flow**: It receives the app plus runtime services such as credentials, blob storage, sandboxes, hub, DBOS client, models, skills, memory, and objects. It prepares shared context builders, registers routes for every surface, installs workspace cleanup middleware, mounts the home redirect, and creates a writeback poller when durable surfaces need one.

**Call relations**: `run` calls this after the runtime is ready. It creates nested `context_for` and `endpoint` helpers; those are used later whenever a surface request arrives.

*Call graph*: calls 2 internal fn (_mount_home, index); called by 1 (run); 19 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, add_middleware, add_route (+9 more)).


##### `_mount_shared_surfaces.context_for`  (lines 936–963)

```
def context_for(workspace_id: UUID, surface: str) -> SurfaceContext
```

**Purpose**: This builds the `SurfaceContext` handed to a surface handler for one workspace. The context is the surface's toolbox: admission, tailing, stopping, credentials, blob storage, sandboxes, models, skills, memory, and object schemas.

**Data flow**: It receives a workspace ID and surface name, combines them with the services captured by `_mount_shared_surfaces`, and returns a fully populated `SurfaceContext`. It also includes deployment details such as home surface, available models, sandbox sizes, and extension metadata.

**Call relations**: The nested surface `endpoint` calls this after identifying a workspace. The writeback poller also uses it to perform durable writeback work under the correct workspace.

*Call graph*: calls 1 internal fn (home_surface); 2 external calls (__init__, __init__).


##### `_mount_shared_surfaces.endpoint`  (lines 979–993)

```
async def endpoint(request: Request, handler=route.handler, identify=resolver, surface=spec.name, surface_auth=auth) -> Response
```

**Purpose**: This is the per-request wrapper for a shared surface route. It authenticates or identifies the request, binds the workspace, and then calls the surface's real handler.

**Data flow**: It receives a request, calls the surface's identify function with surface authentication helpers, and handles three outcomes: a ready-made response, unauthorized/no workspace, or a workspace ID. For a workspace ID, it stores that workspace in the current request context and returns the handler's response.

**Call relations**: FastAPI calls this for matching `/surface/...` routes. It is created by `_mount_shared_surfaces` and relies on `WorkspaceScopeBoundary` to clear the workspace after the response is done.

*Call graph*: 2 external calls (Response, set).


##### `home_surface`  (lines 1011–1018)

```
def home_surface(manifests: tuple[Manifest, ...]) -> str | None
```

**Purpose**: This finds the one surface that should act as the browser home page. It prevents a deployment from claiming two different default front doors.

**Data flow**: It scans all surface declarations for those marked as home. If more than one is found, it raises an error; if exactly one is found, it returns that surface name; otherwise it returns `None`.

**Call relations**: `_mount_home` uses this to decide whether `/` should redirect anywhere. `_mount_shared_surfaces.context_for` also includes the home surface name in the context it gives to handlers.

*Call graph*: called by 2 (_mount_home, context_for).


##### `_mount_home`  (lines 1021–1033)

```
def _mount_home(app: FastAPI, manifests: tuple[Manifest, ...]) -> None
```

**Purpose**: This makes the bare root URL `/` redirect to the configured home surface. Without it, visiting the service host directly would show a missing page when a home surface exists.

**Data flow**: It asks `home_surface` for the home surface name. If there is none, it does nothing; otherwise it creates a small route that redirects browsers to `/surface/<home>`.

**Call relations**: `_mount_shared_surfaces` calls this after mounting surface routes. Its nested `home` function handles the actual `GET /` request.

*Call graph*: calls 1 internal fn (home_surface); called by 1 (_mount_shared_surfaces); 1 external calls (add_route).


##### `_mount_home.home`  (lines 1030–1031)

```
async def home(_request: Request) -> Response
```

**Purpose**: This route handler redirects a browser from `/` to the selected home surface. It is a simple doorway into the real surface UI.

**Data flow**: It receives the incoming request, ignores its details, and returns a 303 redirect response pointing to the home surface path.

**Call relations**: FastAPI calls it for `GET /` after `_mount_home` registers it. The destination surface then decides whether to show content or sign-in.

*Call graph*: 1 external calls (RedirectResponse).


##### `_serve_lifespan`  (lines 1037–1060)

```
async def _serve_lifespan(app: FastAPI) -> AsyncIterator[None]
```

**Purpose**: This runs app-loop background tasks while the FastAPI server is alive. These tasks recover stranded workflows, reconcile cancellation, and optionally poll durable surface writebacks.

**Data flow**: When the app starts, it creates a task group and starts recovery, cancel reconciliation, and possibly writeback polling. When the app shuts down, it cancels those tasks before leaving the lifespan block.

**Call relations**: `run` passes this as the FastAPI lifespan handler. It does not run the heartbeat; the heartbeat is started earlier in `run` on a separate thread so worker liveness is independent of the web app loop.

*Call graph*: 3 external calls (__init__, __init__, TaskGroup).


##### `_proxy_endpoint`  (lines 1063–1091)

```
def _proxy_endpoint(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, pricing: Pricing, run_tokens: RunTokenCodec, blob: BlobStore) -> ProxyEndpoint
```

**Purpose**: This decides how sandboxes reach the outside world through an egress proxy. The proxy is the controlled exit gate that enforces network and credential rules for sandboxed code.

**Data flow**: It reads sandbox proxy configuration. If no public proxy URL is configured, it starts a local in-process proxy and returns its endpoint; otherwise it reads the shared proxy certificate from the environment and returns an endpoint pointing at the external proxy.

**Call relations**: `run` calls this while constructing the conversation sandbox system. If local mode is needed, it delegates to `_local_egress_proxy`.

*Call graph*: calls 1 internal fn (_local_egress_proxy); called by 1 (run); 1 external calls (__init__).


##### `_local_egress_proxy`  (lines 1094–1132)

```
def _local_egress_proxy(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, pricing: Pricing, run_tokens: RunTokenCodec, blob: BlobStore) -> ProxyEndpoint
```

**Purpose**: This starts a local egress proxy inside the service process for single-node deployments. It gives sandboxes one controlled route to models, artifacts, connectors, and allowed internet hosts.

**Data flow**: It creates a new event loop on a daemon thread, boots the proxy asynchronously there, waits up to the startup timeout, and returns the resulting proxy endpoint. The proxy uses generated certificate authority material because it is local and temporary.

**Call relations**: `_proxy_endpoint` calls this when there is no external proxy URL. Its nested `_boot` coroutine builds the actual proxy rules and starts the proxy server.

*Call graph*: called by 1 (_proxy_endpoint); 3 external calls (new_event_loop, run_coroutine_threadsafe, Thread).


##### `_local_egress_proxy._boot`  (lines 1112–1130)

```
async def _boot() -> ProxyEndpoint
```

**Purpose**: This asynchronous boot step builds the local proxy's rules and starts the proxy server. It defines what sandbox traffic is allowed and how per-agent authorization is checked.

**Data flow**: It builds rule resolvers from model rules, artifact-store rules, manifest network rules, connector transfer hosts, credentials, grants, and connector command-line tools. It generates a certificate authority, starts an `EgressProxy` on the configured port, and returns the endpoint the sandbox should use.

**Call relations**: It is scheduled by `_local_egress_proxy` onto the proxy's dedicated event loop. It hands the finished endpoint back to `_local_egress_proxy`, which returns it to `_proxy_endpoint` and ultimately to the sandbox runtime.

*Call graph*: 10 external calls (__init__, __init__, __init__, connector_clis, injecting_slots, model_rule_base, connector_transfer_hosts, derive_artifact_store_rules, derive_manifest_rules, generate_ca).


##### `_connector_registry`  (lines 1138–1161)

```
def _connector_registry(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> ConnectorRegistry
```

**Purpose**: This builds the registry of installed connector providers. Connectors are integrations that can authorize with outside services and sync or transfer data.

**Data flow**: It scans manifests for connector declarations, rejects duplicate provider names, creates registry entries, opens the connector namespace resolver, selects the fallback auth proxy, and returns a `ConnectorRegistry`.

**Call relations**: `run` calls this during runtime setup. The runtime and sync system later use the registry to route connector tools and resolve feed-sync credentials.

*Call graph*: calls 1 internal fn (_select_auth_proxy); called by 1 (run); 3 external calls (__init__, __init__, open_connector_namespace).


##### `_connect_flow`  (lines 1164–1189)

```
def _connect_flow(credentials: CredentialStore | None, config: Config, manifests: tuple[Manifest, ...]) -> ConnectFlow | None
```

**Purpose**: This builds the OAuth connect flow for connector authorization. OAuth is the common browser-based process where a user grants this app access to another service.

**Data flow**: If no credential store exists, it returns `None` because grants cannot be safely stored. Otherwise it gathers connector OAuth providers, checks for duplicate provider names, derives the redirect URI, and returns a `ConnectFlow` with encryption and grant storage.

**Call relations**: `run` installs the result so tools, surfaces, and the OAuth callback route can all use the same connect flow. It calls `_connect_redirect_uri` to validate the externally visible callback address.

*Call graph*: calls 1 internal fn (_connect_redirect_uri); called by 1 (run); 3 external calls (__init__, __init__, open_connector_namespace).


##### `_connect_redirect_uri`  (lines 1192–1218)

```
def _connect_redirect_uri(config: Config, providers: Mapping[str, OAuthProvider]) -> str
```

**Purpose**: This computes and validates the public OAuth callback URL. The callback URL must be something the user's browser and the external provider can actually open.

**Data flow**: It reads `connect.public_base_url` from configuration and the set of registered providers. If no providers exist, it may return an empty or simple callback value; if providers exist, it requires a real HTTP or HTTPS URL with a host and rejects wildcard bind addresses like `0.0.0.0`.

**Call relations**: `_connect_flow` calls this before constructing the connect flow. Its return value is the redirect URI that both the outbound OAuth request and inbound callback must agree on.

*Call graph*: called by 1 (_connect_flow); 1 external calls (urlparse).

## 📊 State Registers Touched

- `reg-effective-config` — The deployment’s active settings, such as enabled services, limits, paths, providers, and safety options.
- `reg-database-schema` — The agreed database layout and migration version that all stored records must follow.
- `reg-onboarding-claims` — Temporary signup, email-verification, invitation, and workspace-claim records used while a user joins.
- `reg-extension-pack-registry` — The selected packs and loaded extensions that decide which features, tools, routes, jobs, and backends exist.
- `reg-extension-store` — Per-workspace extension pins and extension-owned settings saved so enabled add-ons survive restarts.
- `reg-credential-vault` — Encrypted workspace secrets and short-lived brokered credentials used without exposing raw secrets to tools.
- `reg-runtime-fleet` — The sign-in sheet of running server and worker instances used to detect active work, crashes, and abandoned turns.
- `reg-background-job-schedule` — The durable list of background jobs and dispatch rules used to retry and run work outside user requests.
- `reg-sandbox-workspace` — The per-conversation isolated workbench, including its handle, files, execution backend, and recorded file changes.
- `reg-sandbox-network-policy` — The per-run rules and proxy state that decide what sandboxed code may reach on the internet and when secrets may be injected.
- `reg-accounting-ledger` — Usage, cost, spend limits, prepaid balances, billing exports, and price versions for workspace spending.
- `reg-observability-context` — Trace IDs, metrics, logs, and sanitized operational events used to understand work across services and turns.
- `reg-database-connection-pool` — Per-process database engine/session pools and workspace-scoped connection context used by servers, workers, migrations, and persistence code.
- `reg-outbound-delivery-queue` — Pending outbound surface writebacks and retry state for messages or notifications sent back to external channels such as Slack.
- `reg-durable-workflow-state` — DBOS/workflow-runtime execution metadata for durable job and turn workflows, including workflow IDs, retries, scheduled starts, cancellation, and resume bookkeeping.
