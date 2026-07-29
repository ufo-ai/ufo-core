# HTTP application startup  `stage-3.1`

This stage is the system’s front door for starting the HTTP application, the web service that other tools and browsers talk to. It happens during startup, before the main request-handling work begins. The command-line file, core/src/ufo/cli.py, provides ufoctl, a human-friendly control panel. A user can use it to create or inspect a workspace, run the service, contact it, add extensions, or package it.

The main builder is core/src/ufo/serve.py. It reads settings, prepares shared pieces such as the database, stored credentials, extension support, sandbox access, and background workers, then starts the web server. During this assembly, the application lifespan code in the control service runs startup and shutdown setup, like opening and later closing a shop. Routes are registered so incoming web requests know where to go: surfaces, OAuth sign-in callbacks, artifact downloads, operator tools, mounted extension pages, and the sandbox proxy. Together, these parts turn local configuration into a running UFO service ready to receive HTTP requests.

## Files in this stage

### Command and service startup
The command-line entrypoint leads into the shared service assembly that configures and starts the UFO HTTP application.

### `core/src/ufo/cli.py`

`entrypoint` · `command invocation`

This file turns many backend pieces of UFO into simple terminal commands. Without it, a developer or operator would have to create config files, seed secrets, migrate the database, onboard the first user, store credentials, inspect spending, install extensions, and start services by hand.

The file is built around Click, a Python library for making command-line interfaces. The top-level `main` command loads a local `.env` file first, so secrets written during setup are available automatically. From there, subcommands do the real work. `init` creates a default local setup, prepares the database, onboards the owner and default agent, and writes a long-lived CLI token. `serve` and `proxy` start the runtime services. `chat` sends messages to the running UFO surface and streams back the agent’s response, including status updates and private credential prompts.

Other command groups act like maintenance panels. Spend commands show and set budget limits. Credential commands store extension secrets safely. Extension commands search, install, and remove add-ons. The bundle command freezes the current deployment into a runnable artifact.

A useful analogy is a building’s front desk: this file does not contain every machine room, but it knows which doors to open and what paperwork is needed for each common task.

#### Function details

##### `_ufoctl_dir`  (lines 63–65)

```
def _ufoctl_dir() -> Path
```

**Purpose**: Finds the private directory where this CLI stores its local files, such as the saved login token and chat session id. It uses an override environment variable when present, otherwise it falls back to `~/.ufoctl`.

**Data flow**: It reads the `UFOCTL_DIR` environment variable. If it is set, that value becomes a filesystem path; if not, the user’s home directory is combined with `.ufoctl`. The result is returned as a `Path` object.

**Call relations**: Setup uses it to write the CLI token, chat uses it to read that token, and session tracking uses it to remember which conversation should continue.

*Call graph*: called by 3 (_session, chat, init); 2 external calls (Path, home).


##### `_dotenv_path`  (lines 68–69)

```
def _dotenv_path() -> Path
```

**Purpose**: Calculates where the project’s `.env` file should live. This is the file used for local secrets beside the main UFO config file.

**Data flow**: It asks the config system for the config file path, takes that file’s parent directory, and appends `.env`. It returns that path without reading or writing it.

**Call relations**: Startup uses it to load secrets, initialization uses it to report where secrets were written, and secret-writing code uses it as the target file.

*Call graph*: called by 3 (_load_dotenv, _write_dev_secrets, init); 1 external calls (config_path).


##### `_dotenv_pairs`  (lines 72–88)

```
def _dotenv_pairs(text: str) -> list[tuple[str, str]]
```

**Purpose**: Parses simple `.env` file text into environment variable names and values. It supports the small format this CLI writes and reads.

**Data flow**: It receives raw text, skips blank lines and comments, accepts lines shaped like `KEY=VALUE`, removes an optional leading `export`, strips matching quotes around values, and returns a list of name-value pairs.

**Call relations**: The environment loader uses it to import saved secrets. The development-secret writer uses it to avoid overwriting values already present in `.env`.

*Call graph*: called by 2 (_load_dotenv, _write_dev_secrets).


##### `_load_dotenv`  (lines 91–100)

```
def _load_dotenv() -> None
```

**Purpose**: Loads local secrets from the `.env` file next to the config file into the current process. It only fills missing environment variables, so explicitly exported values win.

**Data flow**: It finds the `.env` path, exits quietly if the file does not exist, parses it into pairs, and sets each variable only if that name is not already in the environment. It changes process environment state and returns nothing.

**Call relations**: The top-level CLI command calls it before any subcommand runs, so commands like `serve`, `init`, and `chat` can see secrets without the user manually exporting them.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (main).


##### `main`  (lines 104–106)

```
def main() -> None
```

**Purpose**: Defines the root `ufoctl` command. It also performs the shared first step for every command: loading local environment variables.

**Data flow**: It receives no command-specific data itself. When Click enters the command group, it calls the dotenv loader, which may add secrets to the process environment. Then Click dispatches to the selected subcommand.

**Call relations**: Every CLI action begins here. It hands off to subcommands such as `init`, `serve`, `chat`, `spend`, `credential`, `ext`, and `bundle` after preparing the environment.

*Call graph*: calls 1 internal fn (_load_dotenv).


##### `init`  (lines 112–140)

```
def init(email: str, model: str) -> None
```

**Purpose**: Creates a working UFO workspace for first use. It writes a default config if needed, prepares the database, creates the owner and default agent, and stores a CLI token for this machine.

**Data flow**: It takes an owner email and model name from command-line options. It may write `ufo.toml`, write missing development secrets into `.env`, create a PostgreSQL system database if needed, run migrations, onboard workspace records, mint a bearer token, and save that token under the CLI directory. It prints what it created or raises a clear command error.

**Call relations**: This is usually the first command a local user runs. It calls the secret writer, optional PostgreSQL database creator, onboarding flow, migration system, token minting code, and local CLI directory helper.

*Call graph*: calls 5 internal fn (_create_postgres_system_database, _dotenv_path, _onboard, _ufoctl_dir, _write_dev_secrets); 7 external calls (run, ClickException, echo, mint_token, config_path, load_config, apply_migrations).


##### `_write_dev_secrets`  (lines 143–165)

```
def _write_dev_secrets(config: Config) -> tuple[str, ...]
```

**Purpose**: Creates local development secrets needed for a zero-configuration server run. It avoids replacing anything already supplied by the user.

**Data flow**: It receives the loaded config, generates values for the credential encryption key, artifact-token signing secret, and CLI-token signing secret, then compares them against existing `.env` entries and current environment variables. Missing names are appended to `.env` and also set in the current process. It returns the names it newly wrote.

**Call relations**: `init` uses this before onboarding so the same run can mint a CLI token and future `serve` runs can verify it. It relies on `.env` parsing and path helpers.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 2 external calls (generate_key, token_urlsafe).


##### `_onboard`  (lines 168–187)

```
async def _onboard(config: Config, email: str, model: str) -> Onboarded
```

**Purpose**: Creates the core workspace data and runs extension-specific onboarding steps. This is where the initial owner, workspace, default agent, and model setup are created.

**Data flow**: It receives the config, owner email, and model name. It opens the database connection layer, builds an optional encrypted credential store if the key is present, loads active extension manifests, runs the onboarding creator, then runs extension onboarding steps. It returns an `Onboarded` result and always closes database resources afterward.

**Call relations**: `init` calls this after migrations and secrets are ready. It hands work to the `Onboarding` subsystem and extension loader, while making sure the database is initialized and disposed around the operation.

*Call graph*: called by 1 (init); 6 external calls (__init__, __init__, Fernet, dispose_db, init_db, load_manifests).


##### `_create_postgres_system_database`  (lines 190–201)

```
async def _create_postgres_system_database(config: Config) -> None
```

**Purpose**: Ensures the extra PostgreSQL database named by the config exists. This supports deployments that need a separate system database.

**Data flow**: It converts the app database URL into a form accepted by `asyncpg`, extracts the target system database name, connects to PostgreSQL, checks whether that database exists, creates it if missing, and closes the connection.

**Call relations**: `init` calls this only for PostgreSQL configs before migrations run. It prepares the database server so the rest of setup can proceed.

*Call graph*: called by 1 (init); 1 external calls (connect).


##### `migrate`  (lines 205–222)

```
def migrate() -> None
```

**Purpose**: Updates the database schema to the current expected shape. This includes both UFO’s core tables and migrations from active extensions.

**Data flow**: It loads config, optionally replaces the database URL with an owner database string from the environment, applies migrations for the configured pack, and prints confirmation. It changes database structure but does not return data.

**Call relations**: Operators run this after setup or extension changes. In shared-schema deployments it can use the owner DSN supplied for privileged migration work; otherwise it uses the normal configured database URL.

*Call graph*: 3 external calls (echo, load_config, apply_migrations).


##### `serve`  (lines 226–228)

```
def serve() -> None
```

**Purpose**: Starts the main UFO runtime service. This is the command that runs surfaces, workers, and jobs.

**Data flow**: It takes no direct inputs from command options. It delegates to the serving subsystem, which reads its own configuration and starts the runtime. It returns only when the service stops or fails.

**Call relations**: This command is a thin front door into `ufo.serve.run`. Users typically run it after `init` and `migrate`.

*Call graph*: 1 external calls (run).


##### `proxy`  (lines 232–234)

```
def proxy() -> None
```

**Purpose**: Starts the shared egress proxy service. The proxy fronts sandbox network access for workspaces.

**Data flow**: It takes no command options here. It delegates to the proxy serving subsystem, which starts the proxy process and keeps it running.

**Call relations**: This is the CLI entry for `ufo.proxy_serve.run`. It is separate from `serve` for deployments where the proxy is its own service.

*Call graph*: 1 external calls (run).


##### `chat`  (lines 240–258)

```
def chat(message: str | None, new: bool) -> None
```

**Purpose**: Lets a user talk to the default UFO chat surface from the terminal. It can send one message or open a simple interactive prompt.

**Data flow**: It loads config, reads the saved CLI token, chooses an existing or new session channel, and sends each user message as a turn. If no message is passed on the command line, it repeatedly reads lines from standard input until the user exits.

**Call relations**: This command depends on `init` having written a token. It uses `_session` to choose the conversation and `_run_turn` to actually contact the running server.

*Call graph*: calls 3 internal fn (_run_turn, _session, _ufoctl_dir); 3 external calls (ClickException, echo, load_config).


##### `_session`  (lines 261–267)

```
def _session(new: bool) -> str
```

**Purpose**: Chooses the conversation id used by `ufoctl chat`. It keeps chat continuity across separate terminal invocations unless the user asks for a new session.

**Data flow**: It receives a boolean saying whether to force a new session. It reads or writes a `session` file inside the CLI directory; new sessions get a fresh random UUID string. It returns the session id text.

**Call relations**: `chat` calls it before sending messages. The returned value becomes part of the server path used by `_run_turn` and `_stream_turn`.

*Call graph*: calls 1 internal fn (_ufoctl_dir); called by 1 (chat); 1 external calls (uuid4).


##### `_run_turn`  (lines 270–284)

```
def _run_turn(config: Config, token: str, channel: str, message: str) -> None
```

**Purpose**: Runs one chat turn against the local UFO server and translates connection problems into friendly CLI errors. It also explains what happens if the user interrupts a still-running turn.

**Data flow**: It receives config, token, channel id, and message text. It builds the server base URL, runs the asynchronous streaming turn, and catches keyboard interruption or HTTP failures. It prints recovery guidance or raises a Click error when needed.

**Call relations**: `chat` calls this for each submitted message. It hands the network streaming work to `_stream_turn`.

*Call graph*: calls 1 internal fn (_stream_turn); called by 1 (chat); 3 external calls (run, ClickException, echo).


##### `_stream_turn`  (lines 296–304)

```
async def _stream_turn(base: str, token: str, channel: str, message: str) -> None
```

**Purpose**: Creates the network client and display object for a single chat turn. It connects terminal output, authentication headers, and the chat stream driver.

**Data flow**: It receives the server base URL, bearer token, channel id, and message. It builds HTTP headers, creates an `httpx` asynchronous client, creates a `_TurnDisplay`, and asks `_ChatStream` to run the message. It produces terminal output rather than returning a value.

**Call relations**: _run_turn calls it inside `asyncio.run`. It is the bridge between synchronous CLI code and the asynchronous `_ChatStream` conversation loop.

*Call graph*: called by 1 (_run_turn); 3 external calls (__init__, __init__, AsyncClient).


##### `_ChatStream.run`  (lines 318–329)

```
async def run(self, message: str) -> None
```

**Purpose**: Drives a full chat turn until the server says the turn is finished. If the server asks the client to reconnect later or provide secrets, this method performs those follow-up steps.

**Data flow**: It starts with the user message as the request body. It drains a server response stream, sleeps and reconnects with an empty body when told to poll, closes the display when the turn is done, and prompts for any requested secrets. It returns after all required follow-up actions complete.

**Call relations**: _stream_turn creates `_ChatStream` and calls this method. It depends on `_drain` to interpret server directives and on `_fulfill_secret` to answer private credential prompts.

*Call graph*: calls 2 internal fn (_drain, _fulfill_secret); 1 external calls (sleep).


##### `_ChatStream._drain`  (lines 331–371)

```
async def _drain(self, body: str) -> _Pending
```

**Purpose**: Reads one server response stream and turns its directive lines into terminal output or pending follow-up work. A directive is a small command from the server, such as text to print, status to show, or a request to poll again.

**Data flow**: It sends a POST request with the current body, checks for a successful response, then reads lines. It unescapes tab-separated fields, prints text/status/note directives through the display, records secret prompts, records poll timing, and raises an error for unknown directives. It returns a `_Pending` object describing what should happen next.

**Call relations**: _ChatStream.run calls this repeatedly. It hands display updates to `_TurnDisplay` and uses `_unescape` to decode the server’s wire format.

*Call graph*: calls 1 internal fn (_unescape); called by 1 (run); 2 external calls (__init__, ClickException).


##### `_ChatStream._fulfill_secret`  (lines 373–390)

```
async def _fulfill_secret(self, sealed: str, slot: str, prompt: str) -> None
```

**Purpose**: Privately collects a credential value requested during chat and sends it back outside the normal conversation transcript. This keeps secrets out of chat history.

**Data flow**: It receives a sealed prompt token, a credential slot name, and prompt text. It asks the user for hidden input, posts the value with special headers identifying the secret request, checks the response, and prints any acknowledgement line returned by the server.

**Call relations**: _ChatStream.run calls it after a turn finishes with secret prompts. It uses `_unescape` to read any returned directive text and `_TurnDisplay` to show acknowledgements.

*Call graph*: calls 1 internal fn (_unescape); called by 1 (run); 2 external calls (ClickException, prompt).


##### `_unescape`  (lines 396–408)

```
def _unescape(text: str) -> str
```

**Purpose**: Decodes escaped text from the UFO chat directive format. It turns sequences like `\t` and `\n` back into real tabs and newlines.

**Data flow**: It receives one encoded string, walks through it character by character, replaces known backslash escapes with their real characters, and keeps unknown escaped characters as their literal second character. It returns the decoded string.

**Call relations**: Chat stream parsing uses it for every directive field, both while draining normal output and while reading credential-store acknowledgements.

*Call graph*: called by 2 (_drain, _fulfill_secret).


##### `_TurnDisplay.text`  (lines 425–429)

```
def text(self, delta: str) -> None
```

**Purpose**: Prints streamed answer text exactly as it arrives. It also keeps track of whether the terminal is currently in the middle of a line.

**Data flow**: It receives a text fragment, erases any temporary status meter, writes the fragment to standard output without forcing a newline, and updates internal line state. It changes terminal output and display bookkeeping.

**Call relations**: _ChatStream._drain calls it for `txt` directives. It cooperates with meter erasing so status updates do not overwrite streamed text.

*Call graph*: calls 1 internal fn (_erase_meter); 1 external calls (echo).


##### `_TurnDisplay.line`  (lines 431–435)

```
def line(self, text: str) -> None
```

**Purpose**: Prints a complete line from the server, such as a final answer, failure message, link, or credential acknowledgement.

**Data flow**: It receives text, first closes any half-written streamed line cleanly, then writes the full line to standard output. It updates display state through `_close_line`.

**Call relations**: _ChatStream._drain and `_ChatStream._fulfill_secret` use it for `say` messages. It relies on `_close_line` to avoid messy terminal output.

*Call graph*: calls 1 internal fn (_close_line); 1 external calls (echo).


##### `_TurnDisplay.activity`  (lines 437–441)

```
def activity(self, note: str) -> None
```

**Purpose**: Prints a dim, separate activity note during a turn, such as a tool call or skill load. This gives the user progress context without mixing it into answer text.

**Data flow**: It receives a note string, closes any open streamed line, styles the note dimly, and writes it to standard output. It changes only terminal output and display bookkeeping.

**Call relations**: _ChatStream._drain calls it for `note` directives. It uses `_close_line` so progress messages appear between clean lines.

*Call graph*: calls 1 internal fn (_close_line); 2 external calls (echo, style).


##### `_TurnDisplay.meter`  (lines 443–452)

```
def meter(self, text: str) -> None
```

**Purpose**: Shows a temporary status meter on terminals that support it. The meter is meant to be overwritten or erased, like a live “working…” line.

**Data flow**: It receives status text. If output is not an interactive terminal, it does nothing. Otherwise it may first move to a new line, writes an erasable dim status line to standard error, and records that a meter is visible.

**Call relations**: _ChatStream._drain calls it for `status` directives. Later text, line, activity, or close operations erase the meter before printing permanent output.

*Call graph*: 2 external calls (echo, style).


##### `_TurnDisplay.close`  (lines 454–455)

```
def close(self) -> None
```

**Purpose**: Finishes terminal output for a turn cleanly. It removes any temporary meter and closes an unfinished line.

**Data flow**: It takes no input besides the display’s internal state. It delegates to `_close_line`, which may print a newline and clear meter state. It returns nothing.

**Call relations**: _ChatStream.run calls it when the server has finished the turn and before prompting for any requested secrets.

*Call graph*: calls 1 internal fn (_close_line).


##### `_TurnDisplay._erase_meter`  (lines 457–461)

```
def _erase_meter(self) -> None
```

**Purpose**: Removes the temporary status meter from the terminal if one is showing. This prevents status text from being mistaken for real chat output.

**Data flow**: It checks the display’s `meter_shown` flag. If a meter is visible, it writes the terminal erase sequence to standard error and marks the meter as gone. It returns nothing.

**Call relations**: Text printing and line closing call this before writing permanent output. It is an internal helper for keeping the terminal tidy.

*Call graph*: called by 2 (_close_line, text); 1 external calls (echo).


##### `_TurnDisplay._close_line`  (lines 463–467)

```
def _close_line(self) -> None
```

**Purpose**: Makes sure the terminal is at a clean line boundary. This is needed because streamed text may end without a newline.

**Data flow**: It erases any visible meter, checks whether streamed text left a line open, and if so prints a newline and marks the line closed. It returns nothing.

**Call relations**: Line, activity, and close operations call this before printing or finishing. It uses `_erase_meter` as the first cleanup step.

*Call graph*: calls 1 internal fn (_erase_meter); called by 3 (activity, close, line); 1 external calls (echo).


##### `spend_cap`  (lines 471–472)

```
def spend_cap() -> None
```

**Purpose**: Defines the `spend-cap` command group. This group contains commands for reading and setting budget limits.

**Data flow**: It does not process data directly. Click uses it as a parent command that routes to subcommands such as `set` and `list`.

**Call relations**: Users enter this group when they want to configure spending guardrails. The actual database work happens in `spend_cap_set`, `spend_cap_list`, and their helper functions.


##### `spend_cap_set`  (lines 483–501)

```
def spend_cap_set(scope: str, subject_id: str, window_seconds: int, limit_micro_usd: int, on_breach: str) -> None
```

**Purpose**: Creates or updates a spending cap for the workspace, a member, or an agent. A spend cap limits allowed cost over a time window.

**Data flow**: It receives scope, optional subject id, window length, dollar-limit value in micro-dollars, and breach behavior from command options. It validates that subject ids match the scope, loads config, writes the cap in the database, converts micro-dollars to dollars for display, and prints the result.

**Call relations**: This command is under the `spend-cap` group. It calls `_write_spend_cap` to perform the database insert-or-update.

*Call graph*: calls 1 internal fn (_write_spend_cap); 5 external calls (run, ClickException, echo, load_config, UUID).


##### `spend_cap_list`  (lines 505–515)

```
def spend_cap_list() -> None
```

**Purpose**: Shows all spending caps configured for the workspace. It gives operators a quick view of current budget limits.

**Data flow**: It loads config, reads cap rows from the database, and either prints `no spend caps set` or formats each cap with its scope, target, dollar amount, window, and breach behavior.

**Call relations**: This command is under the `spend-cap` group. It delegates database reading to `_read_spend_caps`.

*Call graph*: calls 1 internal fn (_read_spend_caps); 3 external calls (run, echo, load_config).


##### `_write_spend_cap`  (lines 518–572)

```
async def _write_spend_cap(config: Config, scope: str, subject: UUID | None, window_seconds: int, limit_micro_usd: int, on_breach: str) -> UUID
```

**Purpose**: Writes a spending cap to the database, updating an existing matching cap when one already exists. This prevents duplicate caps for the same scope, subject, and time window.

**Data flow**: It receives config and cap details, opens the database layer, finds the current workspace id, searches for an existing matching row, and either updates its limit and behavior or inserts a new row with a fresh id. It returns the cap id and always disposes database resources.

**Call relations**: spend_cap_set calls it after validating CLI input. It uses the shared workspace transaction helper and SQLAlchemy, a library for building database queries in Python.

*Call graph*: called by 1 (spend_cap_set); 7 external calls (insert, select, update, dispose_db, init_db, workspace_tx, uuid4).


##### `_read_spend_caps`  (lines 575–601)

```
async def _read_spend_caps(config: Config) -> list[tuple[UUID, str, UUID | None, int, int, str]]
```

**Purpose**: Reads the workspace’s spending caps from the database. It returns compact records ready for CLI formatting.

**Data flow**: It receives config, opens the database layer, finds the workspace id, selects cap fields for that workspace ordered by scope, converts rows into tuples, and closes database resources. The returned list contains ids, scopes, optional subjects, windows, limits, and breach behavior.

**Call relations**: spend_cap_list calls it and then formats the returned rows for humans.

*Call graph*: called by 1 (spend_cap_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `spend`  (lines 609–629)

```
def spend(window_seconds: int) -> None
```

**Purpose**: Prints a spending report for a recent time window. It shows total cost and breakdowns by dimension, member, agent, and price version.

**Data flow**: It receives a window length option, loads config, reads a spend report, converts micro-dollars into dollars for display, and prints summary sections. It does not change stored data.

**Call relations**: Operators use this as an accounting command. It delegates calculation and database access to `_read_spend`, which uses the spend rollup subsystem.

*Call graph*: calls 1 internal fn (_read_spend); 3 external calls (run, echo, load_config).


##### `_read_spend`  (lines 632–639)

```
async def _read_spend(config: Config, window_seconds: int) -> SpendReport
```

**Purpose**: Fetches the spending rollup for the current workspace and time window. A rollup is a summarized view of many ledger entries.

**Data flow**: It receives config and a window length, opens the database layer, finds the workspace id, asks `SpendRollup` to read the report, and closes database resources. It returns a `SpendReport` object.

**Call relations**: The `spend` command calls it before printing. It bridges the CLI to the accounting subsystem.

*Call graph*: called by 1 (spend); 5 external calls (__init__, select, dispose_db, init_db, workspace_tx).


##### `grants`  (lines 643–655)

```
def grants() -> None
```

**Purpose**: Lists OAuth account grants available to agents. OAuth is a standard way to let an app access an external account without storing the user’s password.

**Data flow**: It loads config, reads grant summaries, and either prints `no grants` or formats each grant with agent, provider, account id, sharing mode, and grant date. It does not change grants.

**Call relations**: This command calls `_read_grants`, which gets the workspace id and then asks the grants subsystem for summaries.

*Call graph*: calls 1 internal fn (_read_grants); 3 external calls (run, echo, load_config).


##### `_read_grants`  (lines 658–665)

```
async def _read_grants(config: Config) -> tuple[GrantSummary, ...]
```

**Purpose**: Reads grant summaries for the current workspace. These summaries describe which external accounts agents can use.

**Data flow**: It receives config, opens the database layer, finds the workspace id, exits the transaction, asks `workspace_grant_summaries` for grant data, and disposes database resources. It returns a tuple of grant summary objects.

**Call relations**: The `grants` command calls it and formats the result for terminal output.

*Call graph*: called by 1 (grants); 5 external calls (select, dispose_db, init_db, workspace_tx, workspace_grant_summaries).


##### `credential`  (lines 669–671)

```
def credential() -> None
```

**Purpose**: Defines the `credential` command group. This group is for storing and inspecting extension credential slots.

**Data flow**: It does not read or write credentials itself. Click uses it as a parent command and routes to `set` or `list` subcommands.

**Call relations**: Users enter this group when an installed extension needs a bring-your-own-key secret. The actual work happens in `credential_set`, `credential_list`, and their helpers.


##### `credential_set`  (lines 676–699)

```
def credential_set(slot: str) -> None
```

**Purpose**: Stores a secret value for one declared credential slot. It avoids putting secrets in command-line arguments, where they could be logged or seen in shell history.

**Data flow**: It receives a slot name, loads config, verifies the slot exists and is allowed to be filled by an operator, checks that the encryption key environment variable is set, reads the secret from a hidden prompt or standard input, rejects empty values, writes the encrypted credential, and prints confirmation.

**Call relations**: This command uses `_declared_slots` and `_fillable_slots` for validation, then calls `_write_credential` for encrypted storage.

*Call graph*: calls 3 internal fn (_declared_slots, _fillable_slots, _write_credential); 5 external calls (run, ClickException, echo, prompt, load_config).


##### `credential_list`  (lines 703–713)

```
def credential_list() -> None
```

**Purpose**: Shows which declared credential slots exist and whether each one has been stored. It never prints secret values.

**Data flow**: It loads config, reads declared slots from extension manifests, reads stored slot names from the database, and prints each slot with its owning extension and set/unset status. If no slots are declared, it says so.

**Call relations**: This command combines `_declared_slots` with `_read_stored_slots` to produce a safe inventory for operators.

*Call graph*: calls 2 internal fn (_declared_slots, _read_stored_slots); 3 external calls (run, echo, load_config).


##### `_declared_slots`  (lines 716–721)

```
def _declared_slots(config: Config) -> dict[str, str]
```

**Purpose**: Finds all credential slots declared by active extension manifests. A manifest is an extension’s description of what it provides and needs.

**Data flow**: It receives config, loads manifests for the configured pack, and builds a dictionary mapping each slot name to the extension that declared it. If manifests cannot be loaded, it turns that failure into a CLI-friendly error.

**Call relations**: credential_set uses it to reject unknown slots. credential_list uses it to know what should be shown.

*Call graph*: called by 2 (credential_list, credential_set); 2 external calls (ClickException, load_manifests).


##### `_fillable_slots`  (lines 724–733)

```
def _fillable_slots(config: Config) -> frozenset[str]
```

**Purpose**: Finds the credential slots that a human operator is allowed to type values into. Some slots are written by the deployment itself and should not be manually filled.

**Data flow**: It receives config, loads extension manifests, filters credential declarations to those marked as member-filled, and returns their names as a frozen set. Manifest loading errors become CLI errors.

**Call relations**: credential_set calls it after confirming a slot exists, so the CLI only accepts values for slots intended to be filled this way.

*Call graph*: called by 1 (credential_set); 2 external calls (ClickException, load_manifests).


##### `_write_credential`  (lines 736–743)

```
async def _write_credential(config: Config, key: str, slot: str, value: str) -> None
```

**Purpose**: Encrypts and stores one credential value for the current workspace. Encryption at rest means the database stores sealed data, not plain secret text.

**Data flow**: It receives config, encryption key, slot name, and secret value. It opens the database layer, finds the workspace id, builds a `CredentialStore` with a Fernet key, stores the value, and disposes database resources. It returns nothing.

**Call relations**: credential_set calls it after reading and validating the secret. It bridges the CLI to the encrypted credential store.

*Call graph*: called by 1 (credential_set); 6 external calls (__init__, Fernet, select, dispose_db, init_db, workspace_tx).


##### `_read_stored_slots`  (lines 746–760)

```
async def _read_stored_slots(config: Config) -> frozenset[str]
```

**Purpose**: Reads the names of credential slots that already have stored values. It does not decrypt or reveal the values.

**Data flow**: It receives config, opens the database layer, finds the workspace id, selects credential slot names for that workspace, converts them to a frozen set, and closes database resources.

**Call relations**: credential_list calls it to mark declared slots as set or unset without exposing secrets.

*Call graph*: called by 1 (credential_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `ext`  (lines 764–765)

```
def ext() -> None
```

**Purpose**: Defines the `ext` command group for extension store actions. Extensions are add-ons that can expand what UFO can do.

**Data flow**: It does not process extension data itself. Click uses it to route to `search`, `install`, and `remove` subcommands.

**Call relations**: The subcommands under this group use `_store` to access the configured extension catalog and lockfile.


##### `_store`  (lines 768–771)

```
def _store(config: Config) -> ExtensionStore
```

**Purpose**: Builds an `ExtensionStore` object from the configured catalog and the local lockfile. The lockfile records exactly which extensions are pinned for this deployment.

**Data flow**: It receives config, checks that an extension store is configured, reads the catalog, finds the lockfile path, and returns an `ExtensionStore`. If no store is configured, it raises a CLI error.

**Call relations**: ext_search, ext_install, and ext_remove all call this before doing store work. It centralizes the setup and error message for extension commands.

*Call graph*: called by 3 (ext_install, ext_remove, ext_search); 4 external calls (__init__, ClickException, lockfile_path, read_catalog).


##### `ext_search`  (lines 776–789)

```
def ext_search(query: str) -> None
```

**Purpose**: Searches the extension catalog and shows matching extensions. It also marks whether each match is already installed or only available through a bundle.

**Data flow**: It receives a query string, loads config, builds the store, asks it to search, and prints either no matches or a formatted list of name, version, and state.

**Call relations**: This is a read-only command under `ext`. It depends on `_store` for catalog and lockfile access.

*Call graph*: calls 1 internal fn (_store); 2 external calls (echo, load_config).


##### `ext_install`  (lines 794–800)

```
def ext_install(name: str) -> None
```

**Purpose**: Pins an extension from the store into the deployment lockfile. The next server run will load the installed extension.

**Data flow**: It receives an extension name, loads config, builds the store, asks it to install the extension, catches user-facing install errors, and prints the pinned name, version, and digest. The lockfile is changed by the store.

**Call relations**: This command sits under `ext`. After it succeeds, operators may need to run `migrate` before `serve` if the extension owns database tables.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `ext_remove`  (lines 805–811)

```
def ext_remove(name: str) -> None
```

**Purpose**: Removes an extension pin from the deployment lockfile. The next server run will stop loading that extension.

**Data flow**: It receives an extension name, loads config, builds the store, asks it to remove the pin, catches expected errors, and prints confirmation. The lockfile is changed by the store.

**Call relations**: This command sits under `ext` and shares `_store` setup with search and install.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `_ufo_project_dir`  (lines 817–828)

```
def _ufo_project_dir() -> Path
```

**Purpose**: Finds the source checkout of the UFO project so the bundle command can build a Python wheel from it. A wheel is a packaged Python artifact that can be installed elsewhere.

**Data flow**: It walks upward from this file’s location, looking for a `pyproject.toml` whose project name is `ufo`. If it finds one, it returns that directory; if not, it raises a clear CLI error saying bundling requires the source project.

**Call relations**: bundle calls this before running `uv build`. This lets bundling work no matter what directory the user ran the command from.

*Call graph*: called by 1 (bundle); 3 external calls (ClickException, Path, loads).


##### `bundle`  (lines 835–851)

```
def bundle(out: Path) -> None
```

**Purpose**: Freezes the current deployment into a runnable bundle. The bundle includes a recipe, pinned config, lockfile information, extensions, and a built UFO wheel.

**Data flow**: It receives an output directory option, loads config, optionally reads the extension catalog, asks `Bundle` to build the bundle contents, runs `uv build` to create the project wheel, verifies the wheel exists, and prints the bundle path plus pinned extensions.

**Call relations**: This command is the packaging entry point. It uses `_ufo_project_dir` to find source code, the bundle subsystem to assemble files, and the external `uv` tool to build the wheel.

*Call graph*: calls 1 internal fn (_ufo_project_dir); 8 external calls (__init__, ClickException, echo, run, wheel_name, config_path, load_config, read_catalog).


### `core/src/ufo/serve.py`

`entrypoint` · `startup, request handling, background work, shutdown`

This file is the place where many separate parts of the system become one running service. Think of it like opening a restaurant for the day: it checks the keys, turns on utilities, assigns staff, prepares the kitchen, opens the front door, and makes sure closing is safe.

The `run` function is the main entry. It loads settings, starts logging and databases, loads installed extensions, unlocks the credential store, records that this server instance is alive, and builds shared services such as blob storage, the live event hub, model registry, search, memory indexing, connector routing, and sandbox execution. It then creates a FastAPI web app, mounts built-in and extension routes, starts DBOS workers for durable background work, and launches Uvicorn, the web server.

A central theme is workspace safety. One process serves many workspaces, so each request or background job must be tied to the right workspace before it reads data. `WorkspaceScopeBoundary` clears that workspace marker before and after each HTTP request so one user’s context cannot leak into another’s.

The file also fails early when deployment choices are unsafe: missing credential keys, unknown extension backends, duplicate provider names, unreachable OAuth callback URLs, or remote sandboxes without a secure proxy. Without this file, the service would have pieces, but no reliable way to start them together safely.

#### Function details

##### `_assert_no_reserved_routes`  (lines 120–136)

```
def _assert_no_reserved_routes(app: FastAPI) -> None
```

**Purpose**: Checks that this service has not mounted web routes under URL prefixes reserved for the onboarding gateway. This prevents a route from being silently hidden by the shared front door that sends those paths somewhere else.

**Data flow**: It reads the FastAPI app’s route table, looks for paths starting with reserved prefixes such as `/login`, and collects any conflicts. If none are found, nothing changes; if conflicts exist, it raises an error so startup stops loudly.

**Call relations**: The main `run` flow calls this after all routers and surfaces have been mounted. It is a final safety check before Uvicorn starts accepting traffic.

*Call graph*: called by 1 (run).


##### `run`  (lines 139–275)

```
def run() -> None
```

**Purpose**: Starts the shared UFO service process. It builds every major dependency, registers workers and routes, starts the web server, and coordinates safe shutdown.

**Data flow**: It begins with configuration and environment variables, then creates databases, credentials, extension objects, storage, sandboxes, providers, registries, runtime state, DBOS workers, and a FastAPI app. The result is a live HTTP service plus background workers; on exit it asks the executor to drain and retires the instance if safe.

**Call relations**: This is the top-level orchestration function. It calls the selection helpers, route mounting helpers, job launcher, proxy setup, connector setup, and shutdown helper, tying together almost every subsystem used by the process.

*Call graph*: calls 17 internal fn (from_env, _assert_no_reserved_routes, _connect_flow, _connector_registry, _launch_jobs, _mount_ext_routes, _mount_shared_surfaces, _proxy_endpoint, _select_carrier, _select_cdp_provider (+7 more)); 40 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, run (+15 more)).


##### `_stop_executor`  (lines 278–292)

```
def _stop_executor(dbos: DBOS, heartbeat: Heartbeat, graceful_shutdown_seconds: int) -> None
```

**Purpose**: Stops DBOS workflow execution as safely as possible during shutdown. It only retires this server’s fleet seat if no workflows are still active, avoiding duplicate execution by another server.

**Data flow**: It receives the DBOS executor object, heartbeat object, and drain timeout. It asks DBOS to finish or cancel work, checks whether any workflows are still active, logs and keeps the seat if work remains, or retires the heartbeat seat if the executor is empty.

**Call relations**: `run` calls this in its `finally` block after the web server exits. It hands off to DBOS shutdown and the heartbeat retire operation so fleet recovery can make correct decisions.

*Call graph*: calls 1 internal fn (retire); called by 1 (run); 3 external calls (run, destroy, log).


##### `_shared_owner_dsn`  (lines 295–309)

```
def _shared_owner_dsn(config: Config) -> str
```

**Purpose**: Finds the database connection string used for cross-workspace owner-level reads. This is needed for background sweeps that must first list work across all workspaces before rebinding each item to its own workspace.

**Data flow**: It reads the owner database URL from an environment variable or configuration. If absent, it raises an error; if present, it normalizes a plain PostgreSQL URL into the async database-driver form used by the service.

**Call relations**: `run` calls this before initializing the owner database connection. Later cross-workspace jobs rely on that owner connection existing.

*Call graph*: called by 1 (run).


##### `_launch_jobs`  (lines 312–356)

```
def _launch_jobs(runtime: Runtime, sync_driver: SyncDriver, page_feed: CorePageFeed) -> None
```

**Purpose**: Registers and starts the service’s durable background jobs. These include source syncing, turn dispatching, recovery of parked work, and extension-provided indexing jobs.

**Data flow**: It receives the runtime plus source and page-feed helpers. It builds admission helpers, page-change runners, job bindings, and a job runner, then launches those jobs into DBOS so they can be scheduled and resumed durably.

**Call relations**: `run` calls this after the runtime and sync pieces are ready. It creates the job machinery used by DBOS and extension jobs during the service lifetime.

*Call graph*: called by 1 (run); 7 external calls (__init__, __init__, __init__, __init__, durable_surfaces, bindings_from, core_jobs).


##### `_launch_jobs.invoker_for`  (lines 326–327)

```
def invoker_for(workspace_id: UUID) -> AdmissionInvoker
```

**Purpose**: Creates a workspace-specific admission invoker for background jobs. An admission invoker is the object that lets a job admit or resume work inside one chosen workspace.

**Data flow**: It takes a workspace ID and combines it with the shared admission object. The result is an `AdmissionInvoker` bound to that workspace.

**Call relations**: _launch_jobs passes this small factory into runners that need to create workspace-scoped invokers on demand.

*Call graph*: 1 external calls (__init__).


##### `_select_carrier`  (lines 359–399)

```
def _select_carrier(config: Config, manifests: tuple[Manifest, ...]) -> tuple[Carrier, bool]
```

**Purpose**: Chooses the sandbox carrier, meaning the backend that actually runs isolated conversation sandboxes. It supports the built-in local carrier and carriers contributed by extensions.

**Data flow**: It reads the configured sandbox backend, builds a map of available carrier factories from core and manifests, checks for duplicate names and unknown selections, validates secure access for remote carriers, then returns the chosen carrier and whether it runs off-cluster.

**Call relations**: `run` calls this while building `ConversationSandbox`. Its output determines how all later sandboxed turns are physically run.

*Call graph*: called by 1 (run); 2 external calls (__init__, urlparse).


##### `_source_backends`  (lines 402–416)

```
def _source_backends(manifests: tuple[Manifest, ...]) -> dict[str, SourceBackend]
```

**Purpose**: Builds the list of source-sync backends. These are the implementations that know how to pull pages or files from different source systems.

**Data flow**: It starts with the built-in folder source, then reads each manifest for additional source providers. For each extension, it gives the provider credential access limited to that extension’s declared slots, and returns a backend-name-to-backend map.

**Call relations**: `run` passes this map into `SyncDriver`, which uses it when background sync jobs need to refresh sources.

*Call graph*: called by 1 (run); 2 external calls (__init__, __init__).


##### `_source_identity_resolvers`  (lines 419–459)

```
def _source_identity_resolvers(manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: BlobStore) -> dict[str, SourceIdentityResolver]
```

**Purpose**: Builds helpers that can identify the current user for each surface that supports source identity lookup. This lets synced source data be associated with the right external user identity.

**Data flow**: It reads manifests, finds surfaces with a `self_user_id` handler, and creates one resolver per surface. Each resolver receives a workspace ID later and can read only declared credentials for that surface’s extension.

**Call relations**: `run` passes these resolvers into `SyncDriver`. The nested resolver functions are used later by sync logic when it needs a surface-specific user identity.

*Call graph*: called by 1 (run).


##### `_source_identity_resolvers.resolve`  (lines 433–456)

```
async def resolve(workspace_id: UUID, handler=surface.self_user_id, slots=declared, store=credentials) -> str | None
```

**Purpose**: Runs one surface’s identity lookup inside a specific workspace. It prepares the context the extension needs to ask, “Who is the current external user for this workspace?”

**Data flow**: It receives a workspace ID, builds a credential-reading function and a `SurfaceIdentityContext` containing the workspace, blob store, and credential helper, then awaits the extension’s identity handler. It returns a user ID string or `None`.

**Call relations**: This function is created inside `_source_identity_resolvers` and stored in the resolver map consumed by the sync driver.

*Call graph*: 1 external calls (__init__).


##### `_source_identity_resolvers.resolve.credential`  (lines 439–448)

```
async def credential(credential_slot: str) -> str
```

**Purpose**: Safely reads one credential slot for a surface identity lookup. It blocks extensions from reading credential slots they did not declare.

**Data flow**: It receives a credential slot name, checks whether the slot is allowed, checks that a credential store exists, then fetches the secret for the current workspace. It returns the decrypted credential value or raises an error.

**Call relations**: The enclosing `resolve` function passes this helper into `SurfaceIdentityContext`, so extension identity handlers use it indirectly.


##### `_select_hub`  (lines 462–480)

```
def _select_hub(config: Config, manifests: tuple[Manifest, ...]) -> Hub
```

**Purpose**: Chooses the live event hub used to fan out live frames or updates. The default is in-process, but extensions can provide other hub backends.

**Data flow**: It reads the configured hub backend and URL, builds a map of known hub builders from core and manifests, rejects duplicate or unknown backend names, and returns one hub instance.

**Call relations**: `run` calls this early and stores the hub in runtime and app state. Shared surfaces and hub tailing later use the selected hub.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_cdp_provider`  (lines 483–511)

```
def _select_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> CdpProvider | None
```

**Purpose**: Chooses the browser automation provider, if one is installed and selected. CDP means Chrome DevTools Protocol, a way to control a browser programmatically.

**Data flow**: It reads manifests for CDP provider specs, checks for duplicate backend names, looks up the configured provider, verifies credentials are available if needed, and builds the provider with scoped credential access. If no active extension registers the configured name, it returns `None`.

**Call relations**: `run` uses this to put a browser provider into runtime. `_require_cdp_provider` also calls it during extension requirement checks.

*Call graph*: called by 2 (_require_cdp_provider, run); 1 external calls (__init__).


##### `_validate_requires`  (lines 514–538)

```
def _validate_requires(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Checks that every active extension’s declared required service is actually available. This makes missing support fail during startup instead of during the first user action.

**Data flow**: It reads each manifest’s `requires` entries, finds the matching checker, and runs it with the current config, manifests, and credential store. Unknown requirement names or failed checks become clear startup errors naming the extension and missing seam.

**Call relations**: `run` calls this after loading manifests and credentials. It dispatches to requirement checkers such as `_require_cdp_provider`, `_require_search_provider`, and `_require_memory_search`.

*Call graph*: called by 1 (run).


##### `_require_cdp_provider`  (lines 541–555)

```
def _require_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that a browser-capable extension has a usable browser automation provider. It turns an optional provider into a required startup condition when an extension says it needs one.

**Data flow**: It calls `_select_cdp_provider` with the current setup. If that returns `None`, it raises an error explaining that the configured provider is required but not registered.

**Call relations**: _validate_requires calls this when an extension declares the `cdp_providers` requirement.

*Call graph*: calls 1 internal fn (_select_cdp_provider).


##### `_select_search_provider`  (lines 558–593)

```
def _select_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> SearchProvider | None
```

**Purpose**: Chooses the research search provider, if configured. A search provider is the backend that actually performs web or document searches for research tools.

**Data flow**: It collects search provider specs from manifests, rejects duplicates, checks whether the configuration names a provider, validates that the named provider exists and credentials are available, then builds and returns it. If no search provider is configured, it returns `None`.

**Call relations**: `run` uses this to put a search provider into runtime. `_require_search_provider` calls it when an extension makes search mandatory.

*Call graph*: called by 2 (_require_search_provider, run); 2 external calls (__init__, __init__).


##### `_require_search_provider`  (lines 596–609)

```
def _require_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that research tools have a usable configured search backend. It prevents a research extension from booting into a broken state.

**Data flow**: It first checks whether the search provider setting is present. If not, it raises a clear error; otherwise it calls `_select_search_provider` to validate and build the selected backend.

**Call relations**: _validate_requires calls this for extensions that declare the `search_providers` requirement.

*Call graph*: calls 1 internal fn (_select_search_provider).


##### `_require_memory_search`  (lines 612–638)

```
def _require_memory_search(_config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Checks that the default memory-search provider exists exactly once and is usable. Memory search is the feature that lets the system search stored past context or indexed memory.

**Data flow**: It scans manifests for the default memory-search provider name, raises an error if none or more than one are found, and checks that credentials exist when the providing extension declares credential slots.

**Call relations**: _validate_requires calls this when an extension declares that memory search is required.


##### `_select_auth_proxy`  (lines 650–687)

```
def _select_auth_proxy(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> AuthProxy | None
```

**Purpose**: Chooses the fallback authentication proxy for connectors that do not have their own broker. This lets connector sync fetch credentials through one selected backend.

**Data flow**: It reads auth proxy specs from manifests, rejects duplicate backend names, chooses the configured backend or the sole available backend, requires explicit choice when several exist, verifies credentials are available, and builds the proxy with scoped credential access.

**Call relations**: _connector_registry calls this while building connector routing. The resulting proxy is used later when connector credentials must be resolved.

*Call graph*: called by 1 (_connector_registry); 2 external calls (__init__, __init__).


##### `_mount_ext_routes`  (lines 690–728)

```
def _mount_ext_routes(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, index: IndexBackend, embed: EmbedClient) -> None
```

**Purpose**: Adds extension-defined HTTP routes under `/ext/<extension>/<path>`. Each request must identify a workspace before the extension handler can run.

**Data flow**: It reads each manifest’s route specs, ensures a credential store exists for extensions serving routes, builds an extension context, and registers FastAPI endpoints. Each endpoint later checks the request identity, binds the workspace, and calls the extension handler.

**Call relations**: `run` calls this during web app setup. The nested endpoint function is what FastAPI invokes for matching extension requests.

*Call graph*: called by 1 (run); 2 external calls (add_route, context_for).


##### `_mount_ext_routes.endpoint`  (lines 712–722)

```
async def endpoint(request: Request, handler=spec.handler, identify=spec.identify, extension_context=context) -> Response
```

**Purpose**: Serves one extension route request after verifying which workspace it belongs to. Unauthorized requests are refused before extension code can read data.

**Data flow**: It receives a Starlette request, asks the route’s identify function for a workspace, returns a 401 response if identification fails, otherwise enters that workspace scope and awaits the extension handler. The handler’s response is returned to the client.

**Call relations**: _mount_ext_routes registers this endpoint with FastAPI. FastAPI calls it whenever the corresponding extension route is requested.

*Call graph*: 2 external calls (Response, ws).


##### `WorkspaceScopeBoundary.__call__`  (lines 749–757)

```
async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None
```

**Purpose**: Wraps every HTTP request to prevent workspace context from leaking between requests. A workspace context is the marker that tells database reads which workspace they are allowed to see.

**Data flow**: It receives the raw ASGI request scope, receive function, and send function. For non-HTTP traffic it passes through unchanged; for HTTP it clears `current_workspace`, runs the downstream app, and clears `current_workspace` again even if an error happens.

**Call relations**: _mount_shared_surfaces installs this as middleware. It surrounds shared-surface requests and any other HTTP route after middleware installation.

*Call graph*: 1 external calls (set).


##### `_mount_shared_surfaces`  (lines 760–846)

```
def _mount_shared_surfaces(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: BlobStore, sandboxes: ConversationSandbox, hub: Hub, dbos_client: DBOSClient, artif
```

**Purpose**: Adds shared surface routes, such as user-facing integrations, for a process that serves many workspaces. Each surface request identifies and binds its workspace at request time.

**Data flow**: It installs the workspace boundary middleware, builds admission and hub-tail helpers, gathers declared credential slots, creates per-workspace surface contexts, registers each surface route, and optionally creates a writeback poller for durable surfaces that can post results back later.

**Call relations**: `run` calls this during app setup. It creates the nested context factory and endpoint functions used by surface routes, and it stores the writeback poller in app state for `_serve_lifespan`.

*Call graph*: called by 1 (run); 11 external calls (__init__, __init__, __init__, __init__, add_middleware, add_route, durable_surfaces, declared_slots, writeback_workspaces, log (+1 more)).


##### `_mount_shared_surfaces.context_for`  (lines 790–802)

```
def context_for(workspace_id: UUID, surface: str) -> SurfaceContext
```

**Purpose**: Builds the context object passed to a surface handler for one workspace and one surface. This context is the surface’s safe toolbox: storage, sandbox access, credentials, admission, artifacts, and live tailing.

**Data flow**: It receives a workspace ID and surface name, combines them with shared services such as blob storage, sandboxes, credentials, and admission, and returns a `SurfaceContext` bound to that workspace.

**Call relations**: _mount_shared_surfaces.endpoint calls this for live requests. The writeback poller also receives this factory so delayed surface delivery can run under the correct workspace.

*Call graph*: 2 external calls (__init__, __init__).


##### `_mount_shared_surfaces.endpoint`  (lines 818–832)

```
async def endpoint(request: Request, handler=route.handler, identify=resolver, surface=spec.name, surface_auth=auth) -> Response
```

**Purpose**: Serves one shared surface route request after authenticating and resolving its workspace. It makes sure the surface handler runs under the workspace claimed by the request.

**Data flow**: It receives a request, asks the surface’s identify function to authenticate it, returns that response directly if identification produced one, returns 401 if no workspace was found, or sets `current_workspace` and calls the surface handler with a fresh `SurfaceContext`.

**Call relations**: _mount_shared_surfaces registers this endpoint with FastAPI. FastAPI calls it for `/surface/...` routes, and the workspace boundary later clears the workspace after the response has fully finished.

*Call graph*: 3 external calls (Response, set, context_for).


##### `_serve_lifespan`  (lines 850–873)

```
async def _serve_lifespan(app: FastAPI) -> AsyncIterator[None]
```

**Purpose**: Runs background tasks tied to the FastAPI app’s lifetime. These tasks recover stranded workflows, reconcile cancellations, and deliver durable surface writebacks when enabled.

**Data flow**: When the app starts, it creates an async task group and starts executor recovery, cancel reconciliation, and optionally the writeback poller. When the app shuts down, it cancels those tasks before leaving the lifespan block.

**Call relations**: `run` passes this function to FastAPI as the app lifespan. It runs while Uvicorn is serving requests, separate from the heartbeat thread started earlier.

*Call graph*: 3 external calls (__init__, __init__, TaskGroup).


##### `_proxy_endpoint`  (lines 876–904)

```
def _proxy_endpoint(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, pricing: Pricing, run_tokens: RunTokenCodec, blob: BlobStore) -> ProxyEndpoint
```

**Purpose**: Decides how sandboxes should reach the egress proxy, the controlled network doorway used for outbound sandbox traffic. It supports either an in-process local proxy or an external HTTPS proxy.

**Data flow**: It reads sandbox proxy configuration. If no public proxy URL is set, it starts a local proxy through `_local_egress_proxy`; otherwise it reads the shared proxy CA certificate from the environment and returns a `ProxyEndpoint` describing the external proxy.

**Call relations**: `run` calls this while creating `ConversationSandbox`. It hands either a local or external proxy endpoint to the sandbox carrier.

*Call graph*: calls 1 internal fn (_local_egress_proxy); called by 1 (run); 1 external calls (__init__).


##### `_local_egress_proxy`  (lines 907–945)

```
def _local_egress_proxy(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, pricing: Pricing, run_tokens: RunTokenCodec, blob: BlobStore) -> ProxyEndpoint
```

**Purpose**: Starts an in-process egress proxy for local, single-node sandbox use. This proxy is the sandbox’s controlled path to the outside network.

**Data flow**: It creates a new asyncio event loop on a daemon thread, schedules the nested `_boot` coroutine on that loop, waits for startup, and returns the resulting `ProxyEndpoint`. The proxy keeps running for the life of the process.

**Call relations**: _proxy_endpoint calls this when there is no external proxy URL. The nested `_boot` function builds the actual proxy and network rules.

*Call graph*: called by 1 (_proxy_endpoint); 3 external calls (new_event_loop, run_coroutine_threadsafe, Thread).


##### `_local_egress_proxy._boot`  (lines 925–943)

```
async def _boot() -> ProxyEndpoint
```

**Purpose**: Builds and starts the local egress proxy service. It creates the rules that decide which sandbox requests are allowed and how credentials may be injected.

**Data flow**: It gathers model, artifact, manifest, connector, CLI, credential, and grant rules, generates a temporary certificate authority, creates an `EgressProxy`, and starts it on the configured port. It returns the proxy endpoint details needed by sandboxes.

**Call relations**: _local_egress_proxy schedules this coroutine on the proxy’s private event loop. It hands rule resolution and live-turn authorization into `EgressProxy`.

*Call graph*: 10 external calls (__init__, __init__, __init__, connector_clis, injecting_slots, model_rule_base, connector_transfer_hosts, derive_artifact_store_rules, derive_manifest_rules, generate_ca).


##### `_connector_registry`  (lines 951–974)

```
def _connector_registry(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> ConnectorRegistry
```

**Purpose**: Builds the registry that routes connector-related work to the right provider. Connectors are integrations that can authenticate and sync with outside services.

**Data flow**: It scans manifests for connector definitions, rejects duplicate provider names, creates registry entries, opens the connector namespace resolver, selects the fallback auth proxy, and returns a `ConnectorRegistry`.

**Call relations**: `run` calls this while building runtime. Dynamic connector tools and source syncing later read this registry to resolve provider behavior and credentials.

*Call graph*: calls 1 internal fn (_select_auth_proxy); called by 1 (run); 3 external calls (__init__, __init__, open_connector_namespace).


##### `_connect_flow`  (lines 977–1002)

```
def _connect_flow(credentials: CredentialStore | None, config: Config, manifests: tuple[Manifest, ...]) -> ConnectFlow | None
```

**Purpose**: Builds the OAuth connect flow used to authorize external connectors. OAuth is the common browser-based handoff where a user grants access to another service.

**Data flow**: If no credential store exists, it returns `None` because grants cannot be safely saved. Otherwise it gathers OAuth providers from manifests, rejects duplicate names, computes the callback URL, and returns a `ConnectFlow` with encryption, grant storage, and provider resolution.

**Call relations**: `run` installs this flow globally with `install_connect_flow`. The connector tool, surfaces, and OAuth callback route later use it to begin and complete authorization.

*Call graph*: calls 1 internal fn (_connect_redirect_uri); called by 1 (run); 3 external calls (__init__, __init__, open_connector_namespace).


##### `_connect_redirect_uri`  (lines 1005–1029)

```
def _connect_redirect_uri(config: Config, providers: Mapping[str, OAuthProvider]) -> str
```

**Purpose**: Computes and validates the public OAuth callback URL. This must be a real external address because outside providers redirect the user’s browser back to it.

**Data flow**: It reads `connect.public_base_url`, checks whether connector providers exist, allows an empty value only when no providers are installed, validates scheme and host, rejects local bind addresses, and returns the base URL plus the callback path.

**Call relations**: _connect_flow calls this while creating the OAuth flow. The returned URL is the redirect URI presented during connector authorization.

*Call graph*: called by 1 (_connect_flow); 1 external calls (urlparse).
