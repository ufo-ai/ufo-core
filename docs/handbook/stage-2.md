# Process startup and service bootstrap  `stage-2`

This stage is the system’s “turn the lights on” step. It happens before normal request handling begins. Its job is to read settings, connect to databases, prepare web servers, set up storage and monitoring, and install clean shutdown behavior so the service can stop safely later.

The main shared server starts in `core/src/ufo/serve.py`. It brings together the UFO configuration, database access, extensions, sandboxes, background jobs, web routes, and security boundaries into one running service. For local users and developers, `core/src/ufo/cli.py` provides `ufoctl`, the command-line front door used to create, run, inspect, extend, chat with, and package a workspace. For network access from sandboxes, `core/src/ufo/proxy_serve.py` starts the shared egress proxy, a controlled gateway that only allows approved outside connections. The hosted control service starts through `control/src/ufo_control/main.py`, which runs the web gateway, creates invitation emails, and prepares database security rules. Together, these entry points prepare the machinery before the rest of UFO can do useful work.

## Files in this stage

### Hosted control startup
Hosted control entry points start the gateway and prepare operational database and invitation workflows.

### `control/src/ufo_control/main.py`

`entrypoint` · `startup and operator/admin commands`

This file gives operators a small set of commands for running and preparing the hosted shared-workspace service. It uses Click, a command-line helper library, so a person or deployment script can run commands such as starting the gateway, minting an invite, or setting up database access rules.

At startup, the top-level command sets normal console logging. If an OpenTelemetry endpoint is configured, it also sends logs to a central collector. OpenTelemetry is a standard way to gather logs and other signals from running services. The file is careful not to feed OpenTelemetry's own error logs back into that same pipeline, which avoids a loop if log exporting fails.

The `gateway` command starts the web application with Uvicorn, the server used to run the gateway app. The `invite` command opens a short-lived database connection, creates the invite table if needed, mints a one-time workspace invite, and prints a ready-to-send email containing the code. The `rls-bootstrap` command prepares database roles and row-level security policies. Row-level security means the database itself helps make sure one workspace cannot see another workspace's rows.

Without this file, operators would lack the simple entry point that turns the project’s lower-level pieces into runnable administrative commands.

#### Function details

##### `main`  (lines 29–32)

```
def main() -> None
```

**Purpose**: This is the root command for the service's command-line tool. It sets up basic logging before any subcommand runs, and it optionally enables remote log export.

**Data flow**: It reads the log export endpoint from the process environment. It configures standard logging so messages go to the console, then passes the endpoint value, if any, to `_export_logs`; the visible result is a command-line group ready to run one of its subcommands.

**Call relations**: Click calls this function first when the command-line tool starts. From there, it calls `_export_logs` during setup, and then Click dispatches to a specific subcommand such as `gateway`, `invite`, or `rls_bootstrap`.

*Call graph*: calls 1 internal fn (_export_logs); 1 external calls (basicConfig).


##### `_export_logs`  (lines 35–45)

```
def _export_logs(otlp_endpoint: str | None) -> None
```

**Purpose**: This turns on remote log shipping when an OpenTelemetry collector address is provided. If no address is set, it leaves logging as ordinary console output only.

**Data flow**: It receives either a collector endpoint string or `None`. With `None`, it stops immediately. With an endpoint, it builds an OpenTelemetry logger provider, points it at the collector's logs URL, batches log records for efficient sending, and asks `_install_root_handler` to connect normal Python logging to that provider.

**Call relations**: `main` calls this during command startup. If log export is enabled, this function prepares the OpenTelemetry pieces and then hands them to `_install_root_handler`, which attaches them to the application's root logger.

*Call graph*: calls 1 internal fn (_install_root_handler); called by 1 (main); 4 external calls (OTLPLogExporter, LoggerProvider, BatchLogRecordProcessor, create).


##### `_install_root_handler`  (lines 48–53)

```
def _install_root_handler(logger_provider: LoggerProvider) -> None
```

**Purpose**: This connects Python's normal logging system to OpenTelemetry. It also filters out OpenTelemetry's own logs so a failure while exporting logs does not create an endless reporting loop.

**Data flow**: It receives a configured OpenTelemetry logger provider. It creates a logging handler from that provider, adds a filter that rejects records whose logger name starts with `opentelemetry`, and attaches the handler to the root logger so ordinary log messages are also sent through OpenTelemetry.

**Call relations**: `_export_logs` calls this after it has created the remote logging pipeline. This is the final bridge between the standard logging calls used by the app and the external log collector.

*Call graph*: called by 1 (_export_logs); 2 external calls (getLogger, LoggingHandler).


##### `gateway`  (lines 57–62)

```
def gateway() -> None
```

**Purpose**: This command starts the hosted gateway web service. The gateway serves onboarding, fleet count information, and the terminal client.

**Data flow**: It reads the desired port from the `UFO_GATEWAY_PORT` environment variable, or uses the default port if that variable is absent. It then starts Uvicorn on all network interfaces and points it at the `ufo_control.gateway:app` application; the result is a running HTTP service.

**Call relations**: Click runs this when an operator chooses the `gateway` command. It hands control to `uvicorn.run`, which becomes the long-running web server process.

*Call graph*: 1 external calls (run).


##### `invite`  (lines 67–72)

```
def invite(object_number: int) -> None
```

**Purpose**: This command creates a one-time invite for a new workspace and prints an email that contains the invite code. It is meant for an operator who wants to send a fresh onboarding invitation.

**Data flow**: It receives an object number from the command line and runs the asynchronous `_mint_invite` helper to do the database work. If that succeeds, it prints the generated email text. If invite creation fails with an invite-specific error, it turns that into a clean command-line error message.

**Call relations**: Click calls this for the `invite` command. It uses `asyncio.run` to call `_mint_invite`, because the actual database work is asynchronous, then uses Click's output and error helpers to present the result to the operator.

*Call graph*: calls 1 internal fn (_mint_invite); 3 external calls (run, ClickException, echo).


##### `_mint_invite`  (lines 75–85)

```
async def _mint_invite(object_number: int) -> str
```

**Purpose**: This creates the actual invite record in the database and turns it into a ready-to-send email. It keeps the database connection short-lived and closes it when finished.

**Data flow**: It receives an object number. It reads the public host name for links and the owner database connection string, opens a tiny database connection pool, ensures the invite table exists, mints an invite code, closes the pool, and then formats the subject and body of the invite email. It returns the full email text as a string.

**Call relations**: `invite` calls this when an operator requests a new invite. This helper brings together database access from `InviteCodes`, host/email formatting from `gateway_email`, and database connection information from `rls.owner_dsn`.

*Call graph*: called by 1 (invite); 5 external calls (__init__, create_pool, invite_email, public_apex_host, owner_dsn).


##### `rls_bootstrap`  (lines 89–92)

```
def rls_bootstrap() -> None
```

**Purpose**: This command prepares the database permissions needed by the hosted service. It creates or updates the shared serving role and workspace isolation policies.

**Data flow**: It has no command-line inputs. It runs the asynchronous `_bootstrap` helper, then prints a short success message saying the row-level security policies are current.

**Call relations**: Click calls this for the `rls-bootstrap` command. It uses `asyncio.run` to execute `_bootstrap`, because the underlying database setup functions are asynchronous.

*Call graph*: calls 1 internal fn (_bootstrap); 2 external calls (run, echo).


##### `_bootstrap`  (lines 95–98)

```
async def _bootstrap() -> None
```

**Purpose**: This performs the database security setup behind the `rls-bootstrap` command. It applies workspace isolation policies and ensures the service role exists.

**Data flow**: It reads the owner database connection string, then passes that same connection string to the policy bootstrap routine and the serve-role creation routine. It does not return a value; its effect is changing the database setup so the service can safely access workspace data.

**Call relations**: `rls_bootstrap` calls this as its asynchronous worker. It delegates the real database changes to `bootstrap_policies` and `ensure_serve_role`, keeping the command itself simple.

*Call graph*: called by 1 (rls_bootstrap); 3 external calls (bootstrap_policies, ensure_serve_role, owner_dsn).


### Local operator CLI
The local command-line interface initializes and drives workspace setup, runtime, inspection, extension, and packaging workflows.

### `core/src/ufo/cli.py`

`entrypoint` · `command invocation`

This file turns many parts of the UFO system into simple terminal commands. Without it, a user would need to manually create config files, set secrets, run database setup, call server code, store credentials, inspect spending, manage extensions, and talk to the agent through raw HTTP calls. The file is like a reception desk: it does not do every job itself, but it knows which room to send each request to and checks the paperwork first. At startup it loads a nearby `.env` file so local secrets work without extra shell setup. The `init` command writes a default config when needed, creates development secrets, prepares the database, onboards the first workspace owner and agent, and saves a long-lived CLI token. The `serve` and `proxy` commands start runtime services. The `chat` command sends messages to the UFO surface over HTTP and renders the streamed answer neatly in the terminal, including progress notes and private credential prompts. Other command groups let an operator set spending caps, read spending summaries, list OAuth grants, fill extension credential slots, search/install/remove extensions, and build a deployable bundle. Most database work is wrapped in helper functions that open the database, do one focused query or update, and always close it again.

#### Function details

##### `_ufoctl_dir`  (lines 63–65)

```
def _ufoctl_dir() -> Path
```

**Purpose**: Finds the private directory where this machine stores `ufoctl` state, such as the saved login token and chat session id. It honors an override environment variable for tests or custom setups.

**Data flow**: It reads the `UFOCTL_DIR` environment variable. If it is set, that path becomes the directory; otherwise it uses `.ufoctl` inside the current user's home folder. It returns a `Path` object and does not create anything by itself.

**Call relations**: The setup and chat paths call this when they need a stable place on disk for local CLI state. `init` writes the token there, `chat` reads it, and `_session` stores or reads the current conversation id there.

*Call graph*: called by 3 (_session, chat, init); 2 external calls (Path, home).


##### `_dotenv_path`  (lines 68–69)

```
def _dotenv_path() -> Path
```

**Purpose**: Builds the path to the `.env` file that sits next to the UFO config file. That file is where local secrets can be stored for convenient development use.

**Data flow**: It asks the config system where `ufo.toml` lives, takes that file's directory, and appends `.env`. It returns the resulting path without reading or writing it.

**Call relations**: The environment-loading and secret-writing helpers use this shared path so all commands agree on where local secrets live. `init` also mentions this path in user-facing output.

*Call graph*: called by 3 (_load_dotenv, _write_dev_secrets, init); 1 external calls (config_path).


##### `_dotenv_pairs`  (lines 72–88)

```
def _dotenv_pairs(text: str) -> list[tuple[str, str]]
```

**Purpose**: Parses a small, simple `.env` file format into key and value pairs. It supports the common basics and deliberately ignores anything more complicated.

**Data flow**: It receives the text of a `.env` file. It skips blank lines and comments, accepts lines shaped like `KEY=VALUE`, strips a leading `export`, removes matching quote marks around values, and returns a list of `(name, value)` pairs.

**Call relations**: `_load_dotenv` uses this to fill missing environment variables before commands run. `_write_dev_secrets` uses it to see which secrets are already present before adding new ones.

*Call graph*: called by 2 (_load_dotenv, _write_dev_secrets).


##### `_load_dotenv`  (lines 91–100)

```
def _load_dotenv() -> None
```

**Purpose**: Loads local secrets from the `.env` file into the process environment. Already exported shell variables win, so explicit operator choices are not overwritten.

**Data flow**: It finds the `.env` path, exits quietly if the file does not exist, parses the file into pairs, and sets each variable only if it is currently unset. It changes the current process environment and returns nothing.

**Call relations**: The top-level `main` command group calls this before any subcommand logic runs. That means commands like `init`, `serve`, and `credential set` can read secrets without asking the user to manually export them.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (main).


##### `main`  (lines 104–106)

```
def main() -> None
```

**Purpose**: Defines the root `ufoctl` command. It gives Click, the command-line framework, a parent command under which all subcommands are registered.

**Data flow**: It takes no user data directly. When a `ufoctl` command starts, it loads local `.env` values into the process environment, then Click dispatches to the selected subcommand.

**Call relations**: This is the command-line entry point for the file. Every command declared below it hangs from this group, and its early call to `_load_dotenv` prepares shared configuration for them.

*Call graph*: calls 1 internal fn (_load_dotenv).


##### `init`  (lines 112–140)

```
def init(email: str, model: str) -> None
```

**Purpose**: Creates a usable local UFO workspace. It writes default configuration if missing, prepares the database, creates the first owner and default agent, and saves a CLI token for future commands.

**Data flow**: It receives the owner's email and desired model name from command-line options. It may write `ufo.toml`, may add missing local secrets to `.env`, may create a PostgreSQL system database, applies migrations, runs onboarding, mints a bearer token, and writes that token into the private `ufoctl` directory.

**Call relations**: This command coordinates many lower-level helpers: `_write_dev_secrets` for local secret material, `_create_postgres_system_database` when PostgreSQL needs an extra database, `_onboard` for workspace creation, and `_ufoctl_dir` for token storage. Later commands such as `chat` depend on the token it writes.

*Call graph*: calls 5 internal fn (_create_postgres_system_database, _dotenv_path, _onboard, _ufoctl_dir, _write_dev_secrets); 7 external calls (run, ClickException, echo, mint_token, config_path, load_config, apply_migrations).


##### `_write_dev_secrets`  (lines 143–165)

```
def _write_dev_secrets(config: Config) -> tuple[str, ...]
```

**Purpose**: Creates development secrets needed for a zero-service local setup, without replacing secrets the user already supplied. These include encryption and signing keys.

**Data flow**: It reads the config to learn the environment variable names, generates fresh secret values, reads the existing `.env` file if present, and compares against both the file and current environment. Only missing names are appended to `.env` and also placed into the current process environment. It returns the names it newly wrote.

**Call relations**: `init` calls this before onboarding or token minting so required keys are available immediately. It relies on `_dotenv_path` and `_dotenv_pairs` to safely merge with any existing local secret file.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 2 external calls (generate_key, token_urlsafe).


##### `_onboard`  (lines 168–187)

```
async def _onboard(config: Config, email: str, model: str) -> Onboarded
```

**Purpose**: Creates the core workspace, owner member, default agent, and extension-specific onboarding data. It opens the database boundary once and closes it when done.

**Data flow**: It receives the loaded config, owner email, and model name. It initializes the database connection, optionally builds an encrypted credential store if the credential key is present, loads extension manifests, runs the onboarding object, and returns an `Onboarded` result containing ids such as the workspace id.

**Call relations**: `init` calls this after database migrations are ready. It hands off the real creation work to the onboarding subsystem, then `init` uses the returned workspace id to mint the local CLI token.

*Call graph*: called by 1 (init); 6 external calls (__init__, __init__, Fernet, dispose_db, init_db, load_manifests).


##### `_create_postgres_system_database`  (lines 190–201)

```
async def _create_postgres_system_database(config: Config) -> None
```

**Purpose**: Creates the separate PostgreSQL system database if it does not already exist. This supports deployments where the application database and system database are distinct.

**Data flow**: It turns the configured async PostgreSQL URL into a plain driver URL, extracts the desired system database name, connects to PostgreSQL, checks whether that database exists, and creates it if missing. It closes the connection afterward.

**Call relations**: `init` calls this only for PostgreSQL configurations before migrations run. It prepares the ground so later database setup does not fail because the system database is absent.

*Call graph*: called by 1 (init); 1 external calls (connect).


##### `migrate`  (lines 205–222)

```
def migrate() -> None
```

**Purpose**: Brings the database schema up to date. A schema is the database's table and column layout; this command applies any missing changes for core UFO and active extensions.

**Data flow**: It loads config, checks whether an owner database URL was supplied through the environment, normalizes PostgreSQL URL forms when needed, and calls the migration runner. It prints a success message when done.

**Call relations**: This is a standalone operator command. It delegates the actual schema changes to `apply_migrations`, using the owner DSN in shared-schema deployments and the normal config URL in local setups.

*Call graph*: 3 external calls (echo, load_config, apply_migrations).


##### `serve`  (lines 226–228)

```
def serve() -> None
```

**Purpose**: Starts the main UFO runtime on this machine. That includes the surfaces, workers, and scheduled jobs provided by the server subsystem.

**Data flow**: It takes no direct input. It simply calls the server run function, which continues running the service.

**Call relations**: This command is a thin entry point from the CLI into `ufo.serve.run`. It is used after setup and migrations when the operator wants the actual application to accept work.

*Call graph*: 1 external calls (run).


##### `proxy`  (lines 232–234)

```
def proxy() -> None
```

**Purpose**: Starts the shared egress proxy, which is the network gateway used by workspace sandboxes. Egress means outbound network traffic.

**Data flow**: It takes no command arguments and calls the proxy server's run function. The proxy subsystem then owns the long-running process.

**Call relations**: This is the CLI doorway into `ufo.proxy_serve.run`. It is separate from `serve` for deployments that run the proxy as its own service.

*Call graph*: 1 external calls (run).


##### `chat`  (lines 240–258)

```
def chat(message: str | None, new: bool) -> None
```

**Purpose**: Lets a user talk to the default UFO chat surface from the terminal. It can send one message and exit, or enter an interactive prompt loop.

**Data flow**: It loads config, reads the saved CLI token, chooses or creates a conversation session id, and sends each non-empty user message through `_run_turn`. If no token exists, it stops with a clear instruction to run `ufoctl init`.

**Call relations**: This command depends on `init` having written a token into `_ufoctl_dir`. It uses `_session` to keep conversations continuous and `_run_turn` to perform each HTTP-backed chat turn.

*Call graph*: calls 3 internal fn (_run_turn, _session, _ufoctl_dir); 3 external calls (ClickException, echo, load_config).


##### `_session`  (lines 261–267)

```
def _session(new: bool) -> str
```

**Purpose**: Returns the conversation session id used by `ufoctl chat`. This lets separate CLI invocations continue the same conversation unless the user asks for a new one.

**Data flow**: It looks in the private `ufoctl` directory for a `session` file. If the user requested a new session or the file is missing, it writes a fresh random id. It returns the session id text.

**Call relations**: `chat` calls this before sending messages. The returned id becomes part of the HTTP path used by `_run_turn` and `_stream_turn`, so the server knows which conversation channel to use.

*Call graph*: calls 1 internal fn (_ufoctl_dir); called by 1 (chat); 1 external calls (uuid4).


##### `_run_turn`  (lines 270–284)

```
def _run_turn(config: Config, token: str, channel: str, message: str) -> None
```

**Purpose**: Runs one chat turn and turns common interruptions into friendly CLI behavior. A turn is one user message plus the agent's response.

**Data flow**: It receives config, bearer token, session channel, and message text. It builds the local server base URL, runs the async streaming chat helper, and catches keyboard interrupts or HTTP connection errors to show useful messages.

**Call relations**: `chat` calls this for each entered message. It hands the real network streaming work to `_stream_turn`, while keeping the outer synchronous Click command simple.

*Call graph*: calls 1 internal fn (_stream_turn); called by 1 (chat); 3 external calls (run, ClickException, echo).


##### `_stream_turn`  (lines 296–304)

```
async def _stream_turn(base: str, token: str, channel: str, message: str) -> None
```

**Purpose**: Opens an HTTP client and streams one chat turn through the shared UFO surface. It also creates the terminal display object that renders the stream.

**Data flow**: It receives the server base URL, token, channel id, and message. It builds authorization headers and the surface path, opens an async HTTP client, constructs `_ChatStream`, and asks it to run the turn.

**Call relations**: `_run_turn` calls this inside `asyncio.run`. `_stream_turn` is the bridge from the synchronous CLI world into the async HTTP streaming object `_ChatStream`.

*Call graph*: called by 1 (_run_turn); 3 external calls (__init__, __init__, AsyncClient).


##### `_ChatStream.run`  (lines 318–329)

```
async def run(self, message: str) -> None
```

**Purpose**: Drives a complete chat turn until the server says it is done. It can reconnect for polling and can ask the user for private secrets after the streamed response ends.

**Data flow**: It starts with the user's message as the request body. It repeatedly drains a server stream; if the server asks to poll, it waits and reconnects with an empty body. When no poll is pending, it closes the display, prompts for any requested secrets, sends them out-of-band, and finishes.

**Call relations**: `_stream_turn` creates `_ChatStream` and calls this method. This method coordinates `_drain` for normal directives and `_fulfill_secret` for credential prompts.

*Call graph*: calls 2 internal fn (_drain, _fulfill_secret); 1 external calls (sleep).


##### `_ChatStream._drain`  (lines 331–371)

```
async def _drain(self, body: str) -> _Pending
```

**Purpose**: Reads one server response stream and turns its directive lines into terminal output or pending follow-up work. A directive is a small text command from the server, such as 'print this text' or 'poll again later'.

**Data flow**: It sends a POST request containing the current body, checks for a successful response, then reads lines from the stream. It unescapes tab-separated fields and reacts to known verbs: streaming text, full lines, notes, status meters, secret prompts, poll delays, and terminal markers. It returns a `_Pending` object describing whether to poll again or collect secrets.

**Call relations**: `_ChatStream.run` calls this each time it needs to read the server's held stream. `_drain` uses `_unescape` to decode fields and calls methods on `_TurnDisplay` to show output cleanly.

*Call graph*: calls 1 internal fn (_unescape); called by 1 (run); 2 external calls (__init__, ClickException).


##### `_ChatStream._fulfill_secret`  (lines 373–390)

```
async def _fulfill_secret(self, sealed: str, slot: str, prompt: str) -> None
```

**Purpose**: Collects one secret value privately from the user and sends it to the server without putting it in the chat transcript. This is used for extension credential setup.

**Data flow**: It receives a sealed prompt token, a credential slot name, and prompt text. It asks the user for the value with hidden input, posts the value with special headers identifying the prompt, checks the response, and prints any acknowledgement line the server sends.

**Call relations**: `_ChatStream.run` calls this after a turn ends if `_drain` collected secret prompts. It reuses `_unescape` to read the server's acknowledgement format and writes output through the display.

*Call graph*: calls 1 internal fn (_unescape); called by 1 (run); 2 external calls (ClickException, prompt).


##### `_unescape`  (lines 396–408)

```
def _unescape(text: str) -> str
```

**Purpose**: Decodes the small escape format used in UFO surface directive fields. This lets tabs, newlines, and backslashes travel safely inside tab-separated text lines.

**Data flow**: It receives an escaped string. It scans left to right, replacing `\t` with a tab, `\n` with a newline, `\\` with a backslash, and leaving other characters in place. It returns the decoded string.

**Call relations**: The chat stream parser calls this while reading normal directives and credential acknowledgements. It is a small helper that keeps `_drain` and `_fulfill_secret` from duplicating decoding logic.

*Call graph*: called by 2 (_drain, _fulfill_secret).


##### `_TurnDisplay.text`  (lines 425–429)

```
def text(self, delta: str) -> None
```

**Purpose**: Prints streamed answer text exactly as it arrives. It keeps track of whether the current terminal line is still open.

**Data flow**: It receives a text fragment, erases any temporary status meter first, prints the fragment without adding an extra newline, and updates internal line state based on whether the fragment ended with a newline.

**Call relations**: `_ChatStream._drain` calls this for `txt` directives. It works with `_erase_meter` so progress status never overwrites answer text.

*Call graph*: calls 1 internal fn (_erase_meter); 1 external calls (echo).


##### `_TurnDisplay.line`  (lines 431–435)

```
def line(self, text: str) -> None
```

**Purpose**: Prints a complete line from the server, such as a non-streamed answer, error message, link, or stored-credential acknowledgement.

**Data flow**: It receives line text, first closes any half-written streamed line, then prints the new line to standard output. It updates display state through the close helper.

**Call relations**: `_ChatStream._drain` calls this for `say` directives, and `_ChatStream._fulfill_secret` uses it for acknowledgements. It relies on `_close_line` to keep terminal output tidy.

*Call graph*: calls 1 internal fn (_close_line); 1 external calls (echo).


##### `_TurnDisplay.activity`  (lines 437–441)

```
def activity(self, note: str) -> None
```

**Purpose**: Prints a dim, separate progress note during a turn, such as a tool call or skill load. It is meant to be visible but less prominent than the answer.

**Data flow**: It receives note text, closes any open streamed line, styles the note as dim text, and prints it to standard output.

**Call relations**: `_ChatStream._drain` calls this for `note` directives. It uses `_close_line` before printing so notes do not appear in the middle of streamed answer text.

*Call graph*: calls 1 internal fn (_close_line); 2 external calls (echo, style).


##### `_TurnDisplay.meter`  (lines 443–452)

```
def meter(self, text: str) -> None
```

**Purpose**: Shows a temporary status meter on the terminal, such as a short progress message. It only does this when both output streams are real terminals.

**Data flow**: It receives status text. If not running on a terminal, it ignores it. If answer text has left a line open, it first moves to a fresh line, then writes a dim status line to standard error without a trailing newline and remembers that a meter is visible.

**Call relations**: `_ChatStream._drain` calls this for `status` directives. Later calls to `text`, `line`, `activity`, or `close` erase the meter before printing permanent output.

*Call graph*: 2 external calls (echo, style).


##### `_TurnDisplay.close`  (lines 454–455)

```
def close(self) -> None
```

**Purpose**: Finishes the display for a chat turn. It ensures there is no dangling status meter or half-open output line.

**Data flow**: It takes no input, calls the line-closing helper, and updates internal state so the terminal is left clean.

**Call relations**: `_ChatStream.run` calls this when the server stream is complete and before secret prompts are shown. It delegates the actual cleanup to `_close_line`.

*Call graph*: calls 1 internal fn (_close_line).


##### `_TurnDisplay._erase_meter`  (lines 457–461)

```
def _erase_meter(self) -> None
```

**Purpose**: Removes the temporary status meter from the terminal if one is currently shown. This prevents progress text from becoming part of the final output.

**Data flow**: It checks the display's `meter_shown` flag. If a meter is visible, it prints the terminal escape sequence that clears the current line on standard error and marks the meter as gone.

**Call relations**: `text` calls this before streaming answer text, and `_close_line` calls it before printing permanent line endings. It is an internal cleanup tool for `_TurnDisplay`.

*Call graph*: called by 2 (_close_line, text); 1 external calls (echo).


##### `_TurnDisplay._close_line`  (lines 463–467)

```
def _close_line(self) -> None
```

**Purpose**: Closes any unfinished streamed line and clears any temporary meter. This keeps later output from landing in the wrong place.

**Data flow**: It erases the meter if needed, then checks whether streamed text left the cursor mid-line. If so, it prints a newline and marks the line as closed.

**Call relations**: `line`, `activity`, and `close` call this before they print or finish. It uses `_erase_meter` as the first cleanup step.

*Call graph*: calls 1 internal fn (_erase_meter); called by 3 (activity, close, line); 1 external calls (echo).


##### `spend_cap`  (lines 471–472)

```
def spend_cap() -> None
```

**Purpose**: Defines the `spend-cap` command group. These commands let an operator read and set limits on how much money the workspace, a member, or an agent may spend.

**Data flow**: It receives no data itself. It exists so Click can attach subcommands like `set` and `list` under `ufoctl spend-cap`.

**Call relations**: This group is the parent for `spend_cap_set` and `spend_cap_list`. Those subcommands do the real database reading and writing.


##### `spend_cap_set`  (lines 483–501)

```
def spend_cap_set(scope: str, subject_id: str, window_seconds: int, limit_micro_usd: int, on_breach: str) -> None
```

**Purpose**: Creates or updates a spending cap. Spending is stored in micro-dollars, meaning millionths of a US dollar, so small model costs can be tracked accurately.

**Data flow**: It receives scope, optional subject id, time window, limit, and breach behavior from command-line options. It validates that subject ids are present only when needed, loads config, writes the cap through `_write_spend_cap`, converts the limit to dollars for display, and prints the saved cap id.

**Call relations**: This is the user-facing command for changing spend limits. It calls `_write_spend_cap` for the database update and reports the result back through the terminal.

*Call graph*: calls 1 internal fn (_write_spend_cap); 5 external calls (run, ClickException, echo, load_config, UUID).


##### `spend_cap_list`  (lines 505–515)

```
def spend_cap_list() -> None
```

**Purpose**: Shows all spend caps configured for the workspace. It gives operators a quick view of limits and what happens when they are exceeded.

**Data flow**: It loads config, reads caps through `_read_spend_caps`, and prints either a 'none set' message or one formatted line per cap with scope, subject, dollar amount, window, and breach behavior.

**Call relations**: This command is the read-side partner to `spend_cap_set`. It relies on `_read_spend_caps` for the database query and only formats the results for humans.

*Call graph*: calls 1 internal fn (_read_spend_caps); 3 external calls (run, echo, load_config).


##### `_write_spend_cap`  (lines 518–572)

```
async def _write_spend_cap(config: Config, scope: str, subject: UUID | None, window_seconds: int, limit_micro_usd: int, on_breach: str) -> UUID
```

**Purpose**: Writes a spend cap row in the database, updating an existing matching cap when one already exists. This avoids creating duplicate limits for the same scope and time window.

**Data flow**: It opens the configured database, finds the current workspace id, looks for an existing cap matching workspace, scope, subject, and window. If found, it updates the limit and breach behavior; otherwise it inserts a new row with a fresh id. It returns the cap id and closes the database.

**Call relations**: `spend_cap_set` calls this after validating command-line input. It uses the shared workspace transaction helper so the database read and write happen inside the workspace context.

*Call graph*: called by 1 (spend_cap_set); 7 external calls (insert, select, update, dispose_db, init_db, workspace_tx, uuid4).


##### `_read_spend_caps`  (lines 575–601)

```
async def _read_spend_caps(config: Config) -> list[tuple[UUID, str, UUID | None, int, int, str]]
```

**Purpose**: Reads the workspace's configured spend caps from the database. It returns just the fields needed for the CLI listing.

**Data flow**: It opens the database, finds the current workspace id, selects cap ids, scopes, subjects, windows, limits, and breach behavior for that workspace, orders them by scope, converts database rows to tuples, and closes the database.

**Call relations**: `spend_cap_list` calls this and then handles display. This helper keeps database access separate from terminal formatting.

*Call graph*: called by 1 (spend_cap_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `spend`  (lines 609–629)

```
def spend(window_seconds: int) -> None
```

**Purpose**: Prints a spending report for a recent time window. It shows the total and breakdowns by usage dimension, member, agent, and price version.

**Data flow**: It receives a window size in seconds, loads config, reads a `SpendReport` through `_read_spend`, converts micro-dollars to dollars, and prints a structured report.

**Call relations**: This command is an operator inspection tool. It delegates calculation to `_read_spend`, which in turn uses the accounting subsystem's rollup object.

*Call graph*: calls 1 internal fn (_read_spend); 3 external calls (run, echo, load_config).


##### `_read_spend`  (lines 632–639)

```
async def _read_spend(config: Config, window_seconds: int) -> SpendReport
```

**Purpose**: Fetches the accounting rollup for one workspace and time window. A rollup is a summarized view of many ledger entries.

**Data flow**: It opens the database, finds the workspace id, creates a `SpendRollup` for that workspace, asks it to read the requested window, returns the resulting `SpendReport`, and closes the database.

**Call relations**: `spend` calls this to get the report it prints. This helper is the bridge between the CLI and the accounting subsystem.

*Call graph*: called by 1 (spend); 5 external calls (__init__, select, dispose_db, init_db, workspace_tx).


##### `grants`  (lines 643–655)

```
def grants() -> None
```

**Purpose**: Lists OAuth account grants available to agents. OAuth is a standard way to let an app access another service account without storing the user's password.

**Data flow**: It loads config, reads grant summaries, and prints either 'no grants' or one line per grant showing agent, provider, account id, sharing mode, and grant date.

**Call relations**: This command calls `_read_grants` for database-backed grant information, then formats the summaries for terminal users.

*Call graph*: calls 1 internal fn (_read_grants); 3 external calls (run, echo, load_config).


##### `_read_grants`  (lines 658–665)

```
async def _read_grants(config: Config) -> tuple[GrantSummary, ...]
```

**Purpose**: Reads OAuth grant summaries for the current workspace. It first discovers the workspace id, then asks the grants subsystem for the detailed summaries.

**Data flow**: It opens the database, selects the workspace id inside a workspace transaction, calls `grant_summaries` with that id, returns the tuple of summaries, and closes the database.

**Call relations**: `grants` calls this command helper. It separates the workspace lookup and grant retrieval from the display logic.

*Call graph*: called by 1 (grants); 5 external calls (select, dispose_db, init_db, workspace_tx, grant_summaries).


##### `credential`  (lines 669–671)

```
def credential() -> None
```

**Purpose**: Defines the `credential` command group for extension-provided Bring Your Own Key slots. These are named secrets, such as API keys, stored encrypted at rest.

**Data flow**: It takes no input itself. It exists so Click can attach `set` and `list` subcommands under `ufoctl credential`.

**Call relations**: This group is the parent for `credential_set` and `credential_list`. Those commands use extension manifests to know which credential slots exist.


##### `credential_set`  (lines 676–694)

```
def credential_set(slot: str) -> None
```

**Purpose**: Stores one secret value for a declared credential slot. It avoids putting secrets on the command line, where shell history or process listings could expose them.

**Data flow**: It receives the slot name, loads config, checks that installed extension manifests declare the slot, verifies that the encryption key environment variable is set, reads the secret from a hidden prompt or piped standard input, rejects empty values, writes it through `_write_credential`, and prints confirmation.

**Call relations**: This command calls `_declared_slots` to validate the slot and `_write_credential` to encrypt and store the value. It is the manual counterpart to chat-time secret prompts.

*Call graph*: calls 2 internal fn (_declared_slots, _write_credential); 5 external calls (run, ClickException, echo, prompt, load_config).


##### `credential_list`  (lines 698–708)

```
def credential_list() -> None
```

**Purpose**: Lists declared credential slots and whether each one has a stored value. It never reads or prints the secret values themselves.

**Data flow**: It loads config, reads slot declarations from extension manifests, reads stored slot names from the database, and prints each declared slot with its owning extension and set/unset status.

**Call relations**: This command uses `_declared_slots` for what should exist and `_read_stored_slots` for what is present. It combines those two views for a safe operator report.

*Call graph*: calls 2 internal fn (_declared_slots, _read_stored_slots); 3 external calls (run, echo, load_config).


##### `_declared_slots`  (lines 711–716)

```
def _declared_slots(config: Config) -> dict[str, str]
```

**Purpose**: Builds a map of credential slot names declared by installed extensions. This lets the CLI reject unknown secret names instead of storing arbitrary labels.

**Data flow**: It loads extension manifests for the configured pack. If manifest loading fails, it turns the error into a user-friendly CLI exception. It returns a dictionary from slot name to extension name.

**Call relations**: `credential_set` calls this before accepting a secret, and `credential_list` calls it before displaying slot status. It relies on the extension loader as the source of truth.

*Call graph*: called by 2 (credential_list, credential_set); 2 external calls (ClickException, load_manifests).


##### `_write_credential`  (lines 719–726)

```
async def _write_credential(config: Config, key: str, slot: str, value: str) -> None
```

**Purpose**: Encrypts and stores a credential value for the current workspace. Encryption uses Fernet, a standard symmetric encryption scheme where the same secret key locks and unlocks the value.

**Data flow**: It receives config, encryption key, slot name, and secret value. It opens the database, finds the workspace id, creates a credential store with the key, stores the value for that workspace and slot, and closes the database.

**Call relations**: `credential_set` calls this after validating input and reading the secret. The actual storage work is delegated to `CredentialStore`.

*Call graph*: called by 1 (credential_set); 6 external calls (__init__, Fernet, select, dispose_db, init_db, workspace_tx).


##### `_read_stored_slots`  (lines 729–743)

```
async def _read_stored_slots(config: Config) -> frozenset[str]
```

**Purpose**: Reads which credential slots currently have stored values for the workspace. It returns only names, never secret contents.

**Data flow**: It opens the database, finds the workspace id, selects slot names from the credential table for that workspace, converts them to a frozen set, and closes the database.

**Call relations**: `credential_list` calls this to decide whether each declared slot should be shown as set or unset. It keeps secret-safe database reading separate from display.

*Call graph*: called by 1 (credential_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `ext`  (lines 747–748)

```
def ext() -> None
```

**Purpose**: Defines the `ext` command group for extension store actions. Extensions add optional capabilities to a UFO deployment.

**Data flow**: It receives no data directly. It acts as a Click parent for search, install, and remove commands.

**Call relations**: This group organizes `ext_search`, `ext_install`, and `ext_remove`. Those subcommands all use `_store` to open the configured extension catalog and lockfile.


##### `_store`  (lines 751–754)

```
def _store(config: Config) -> ExtensionStore
```

**Purpose**: Creates an `ExtensionStore` object from the configured catalog and local lockfile. The lockfile records which extensions are pinned for this deployment.

**Data flow**: It receives config, checks that an extension store is configured, reads the catalog, finds the lockfile path, and returns an `ExtensionStore`. If no store is enabled, it raises a clear CLI error.

**Call relations**: The extension search, install, and remove commands all call this first. It centralizes the setup so those commands operate on the same catalog and lockfile.

*Call graph*: called by 3 (ext_install, ext_remove, ext_search); 4 external calls (__init__, ClickException, lockfile_path, read_catalog).


##### `ext_search`  (lines 759–772)

```
def ext_search(query: str) -> None
```

**Purpose**: Searches or lists extensions available in the configured store. It marks whether each result is already installed, only available in the bundle, or available to install.

**Data flow**: It receives a query string, loads config, creates the extension store, asks the store for matching listings, and prints either a no-results message or formatted extension rows.

**Call relations**: This command calls `_store` to get the catalog view. It does not change the lockfile; it only reports what the store says.

*Call graph*: calls 1 internal fn (_store); 2 external calls (echo, load_config).


##### `ext_install`  (lines 777–783)

```
def ext_install(name: str) -> None
```

**Purpose**: Pins an extension from the store into the deployment lockfile. Pinning means recording the exact version and digest so future runs load the same extension.

**Data flow**: It receives an extension name, loads config, opens the extension store, asks it to install the named extension, catches store errors as CLI errors, and prints the installed version and digest.

**Call relations**: This command calls `_store` and then delegates the lockfile change to the extension store. After using it, operators may need to run `migrate` before `serve` if the extension owns tables.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `ext_remove`  (lines 788–794)

```
def ext_remove(name: str) -> None
```

**Purpose**: Removes an extension pin from the deployment lockfile. The next server start will stop loading that extension.

**Data flow**: It receives an extension name, loads config, opens the extension store, asks it to remove the pin, converts store errors into CLI errors, and prints confirmation.

**Call relations**: This command calls `_store` for the shared catalog/lockfile setup. It changes what `serve` will load on later runs.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `_ufo_project_dir`  (lines 800–811)

```
def _ufo_project_dir() -> Path
```

**Purpose**: Finds the source checkout of the UFO project so the bundle command can build a Python wheel from it. A wheel is a packaged Python distribution file.

**Data flow**: It walks upward from this file's location, looking for a `pyproject.toml` whose project name is `ufo`. If it finds one, it returns that directory; if not, it raises an error explaining that bundling needs the source project.

**Call relations**: `bundle` calls this before invoking `uv build`. This avoids depending on the user's current working directory and fails clearly when running from a wheel-only install.

*Call graph*: called by 1 (bundle); 3 external calls (ClickException, Path, loads).


##### `bundle`  (lines 818–834)

```
def bundle(out: Path) -> None
```

**Purpose**: Builds a deployable bundle containing the pinned config, extension lock information, image recipe, and UFO wheel. This freezes a deployment into something repeatable.

**Data flow**: It receives an output directory option, loads config, optionally reads the extension catalog, builds bundle files through `Bundle`, runs `uv build` to create the UFO wheel, checks that the wheel exists, then prints the bundle location and pinned extensions.

**Call relations**: This command coordinates the bundling subsystem and the external `uv` build tool. It calls `_ufo_project_dir` to locate source code and `wheel_name` to verify the expected build result.

*Call graph*: calls 1 internal fn (_ufo_project_dir); 8 external calls (__init__, ClickException, echo, run, wheel_name, config_path, load_config, read_catalog).


### Shared runtime services
Shared service entry points launch the egress proxy and the main UFO server with configuration, storage, databases, security, routes, and background work.

### `core/src/ufo/proxy_serve.py`

`entrypoint` · `startup and long-running proxy main loop`

A sandbox should not be allowed to freely call any website or service. This file builds and runs the shared egress proxy: a controlled doorway between sandboxed work and the outside internet. Think of it like a front desk in a secure building. Every outgoing request has to pass through it, and the front desk checks which workspace and task the request belongs to before allowing it.

The file first decides which model providers, such as Anthropic or OpenAI, can be reached. It only creates rules for providers whose API keys are present in the process environment. If no provider key is available, it stops immediately, because the sandbox would have no useful route to a model.

The shared proxy serves many workspaces at once, so it needs special database access. Instead of using a workspace-limited database connection, it uses an owner database URL and relies on explicit workspace IDs in each lookup to keep data separated. It also requires a stable certificate authority, meaning a trusted signing certificate and key, so sandboxes can keep trusting proxy-created certificates across restarts.

The ProxyServe class ties these pieces together. It opens the database, builds rule resolution from manifests and grants, starts the EgressProxy on the configured port, logs that it is listening, and then waits forever.

#### Function details

##### `model_rule_base`  (lines 37–56)

```
def model_rule_base(config: Config) -> tuple[Rule, ...]
```

**Purpose**: Builds the basic outbound network rules for model providers. It allows only providers whose API keys are actually present, so the proxy does not advertise access it cannot safely provide.

**Data flow**: It takes the loaded configuration, reads the environment variable names for model API keys, and checks the current process environment for those keys. For each key it finds, it asks the model-rule builder to create rules for a representative model, gathers the allowed host names into one host rule, and keeps any extra request-rewriting rules. It returns the combined rule set, or raises an error if no model provider key is set.

**Call relations**: ProxyServe._base calls this when preparing the shared proxy's fixed rules. It relies on derive_model_rules to turn a model name and real API key into proxy rules, and it creates a ScopeRule to describe the model-provider hosts the sandbox may contact.

*Call graph*: called by 1 (_base); 2 external calls (__init__, derive_model_rules).


##### `run`  (lines 59–77)

```
def run() -> None
```

**Purpose**: Starts the standalone shared proxy process. It is the top-level boot sequence that gathers configuration, secrets, telemetry setup, manifests, pricing, and then hands control to the async server.

**Data flow**: It loads the project configuration, initializes observability using either an environment override or the configured endpoint, loads the active pack manifests, reads the shared egress certificate authority, and finds the owner database URL. It builds a ProxyServe object with those ingredients plus model pricing, logs that startup is beginning, and then runs the server until it stops.

**Call relations**: This is the entry path for the proxy command. It calls _egress_ca and _owner_dsn to validate required secrets before constructing ProxyServe, uses load_config and load_manifests to discover deployment settings, uses model_registry to get pricing, and finally passes execution to ProxyServe.serve through asyncio.run.

*Call graph*: calls 2 internal fn (_egress_ca, _owner_dsn); 7 external calls (__init__, run, load_config, load_manifests, model_registry, init_o11y, log).


##### `_egress_ca`  (lines 80–91)

```
def _egress_ca() -> tuple[str, str]
```

**Purpose**: Reads the shared certificate authority material that the proxy uses to create trusted certificates for sandbox traffic. It stops startup if either the certificate or key is missing, because sandboxes would not trust a freshly invented per-process authority.

**Data flow**: It reads two environment variables: one for the certificate and one for the private key, both expected in PEM text format. If both are present, it returns them as a pair. If either is absent, it raises an error explaining that the shared proxy must use one stable authority trusted by every workspace sandbox.

**Call relations**: run calls this during startup before the proxy is created. The returned certificate and key are passed into ProxyServe and then into EgressProxy, which uses them when proxying secure outgoing connections.

*Call graph*: called by 1 (run).


##### `_owner_dsn`  (lines 94–107)

```
def _owner_dsn(config: Config) -> str
```

**Purpose**: Finds the database connection string the shared proxy uses to read data across workspaces. It prefers an environment variable and falls back to the configured owner database URL, then ensures the async PostgreSQL driver form is used.

**Data flow**: It receives the loaded configuration, looks for UFO_OWNER_DSN in the environment, and if that is not set reads config.database.owner_url. If neither is available, it raises an error because the shared proxy cannot safely resolve workspace rules. If it finds a URL, it rewrites a plain postgresql:// prefix to postgresql+psycopg:// so the async database driver is selected, and returns the final string.

**Call relations**: run calls this during startup and passes the result into ProxyServe. Later, ProxyServe.serve gives this database string to init_db so the proxy can resolve per-workspace rules while still filtering by workspace ID.

*Call graph*: called by 1 (run).


##### `ProxyServe.serve`  (lines 122–141)

```
async def serve(self) -> None
```

**Purpose**: Creates and starts the actual egress proxy service, then keeps it alive. This is where the stored settings become a running network server.

**Data flow**: It starts by initializing database access with the owner database URL. It builds a PerAgentRules resolver using the base proxy rules, a grant store, connector transfer hosts, and connector command-line definitions from the loaded manifests. It then creates an EgressProxy with that resolver, an authorization check, the shared certificate authority, and pricing data. After starting the proxy on the configured port and public URL, it logs that the service is listening and waits forever.

**Call relations**: run creates the ProxyServe object and invokes this through asyncio.run. Inside the serving flow, it calls ProxyServe._base to get static rules, then hands rule lookup and authorization functions to EgressProxy. It also calls connector_clis and connector_transfer_hosts so connector-specific traffic rules are included.

*Call graph*: calls 1 internal fn (_base); 8 external calls (__init__, __init__, __init__, Event, init_db, connector_clis, log, connector_transfer_hosts).


##### `ProxyServe._base`  (lines 143–158)

```
def _base(self) -> tuple[Rule, ...]
```

**Purpose**: Builds the shared proxy's static rule base and makes sure it does not try to inject workspace-specific secrets. This protects the shared proxy from doing something that only a dedicated per-workspace proxy is allowed to do.

**Data flow**: It scans every loaded manifest for credential slots marked for injection, meaning secrets that would be inserted into outbound requests. If any are found, it raises an error listing those slots. If none are found, it calls model_rule_base with the configuration and returns the model-provider rules.

**Call relations**: ProxyServe.serve calls this while assembling PerAgentRules. If the active pack is compatible with a shared proxy, this function hands back the model access rules; if not, it stops the server before EgressProxy is built.

*Call graph*: calls 1 internal fn (model_rule_base); called by 1 (serve).


### `core/src/ufo/serve.py`

`entrypoint` · `startup and main loop`

Think of this file as the building manager for a large shared office. One server process serves many workspaces, so it must make sure every request, job, credential lookup, and streamed response is tied to the correct workspace and cannot accidentally see another one. Without this file, the pieces of the system might exist, but nothing would reliably start together or be scoped safely.

At startup, `run` loads config, opens the databases, loads extension manifests, creates encrypted credential storage, records this server instance as alive, and builds shared services such as blob storage, the live event hub, model registry, memory search, connector registry, sandbox carrier, and egress proxy. It then registers durable jobs with DBOS, the workflow/job system used here to run reliable background work.

The file also mounts web routes. Extension routes are placed under `/ext/...`; shared product surfaces are placed under `/surface/...`. For shared surfaces, it installs `WorkspaceScopeBoundary`, a small ASGI middleware that clears the current workspace before and after each HTTP request. That is important because this server handles many workspaces in one process. It is like wiping a whiteboard before the next meeting starts.

Several helper functions fail loudly at boot if configuration is unsafe: unknown extension backends, missing credential keys, remote sandboxes without HTTPS proxy URLs, OAuth callback URLs that are not public, or route prefixes reserved for another gateway.

#### Function details

##### `_assert_no_reserved_routes`  (lines 103–119)

```
def _assert_no_reserved_routes(app: FastAPI) -> None
```

**Purpose**: Checks that this FastAPI app has not registered routes under URL prefixes reserved for the onboarding gateway. This prevents a route from appearing to exist in code but being hidden by the front-door router in production.

**Data flow**: It reads the app's route list, filters for normal HTTP routes whose paths begin with reserved prefixes such as `/login`, and either does nothing if there are no conflicts or raises an error listing the conflicting paths.

**Call relations**: `run` calls this after all built-in, extension, and surface routes have been mounted. It is the final safety check before the server starts accepting traffic.

*Call graph*: called by 1 (run).


##### `run`  (lines 122–226)

```
def run() -> None
```

**Purpose**: Starts the shared fleet server. It is the main assembly point that turns configuration and installed extensions into a running web service with jobs, databases, sandboxes, credentials, and routes.

**Data flow**: It reads configuration and environment variables, initializes observability and databases, loads extension manifests, builds shared service objects, registers background jobs, creates the FastAPI app, mounts routes, then hands the app to Uvicorn to serve HTTP requests. On shutdown, it stops DBOS and retires this instance's heartbeat.

**Call relations**: This is the top-level caller for most helpers in the file. It uses the selection helpers to choose pluggable backends, uses mounting helpers to expose HTTP routes, uses job helpers to start scheduled work, and uses proxy/connect helpers to prepare sandbox networking and OAuth connection flows.

*Call graph*: calls 15 internal fn (_assert_no_reserved_routes, _connect_flow, _connector_registry, _launch_jobs, _mount_ext_routes, _mount_shared_surfaces, _proxy_endpoint, _sandbox_fs_minter, _select_carrier, _select_cdp_provider (+5 more)); 36 external calls (__init__, __init__, __init__, __init__, __init__, __init__, run, Fernet, DBOS, destroy (+15 more)).


##### `_shared_owner_dsn`  (lines 229–243)

```
def _shared_owner_dsn(config: Config) -> str
```

**Purpose**: Finds the special database connection string used for cross-workspace owner-level reads. This is needed for jobs that first list work across all workspaces and then re-enter each workspace safely.

**Data flow**: It reads the owner database URL from an environment variable or config. If none is present, it raises an error. If it finds a normal PostgreSQL URL, it rewrites it to use the async database driver expected by the rest of the service.

**Call relations**: `run` calls this before initializing the owner database connection. Other background sweepers then rely on that owner connection being available.

*Call graph*: called by 1 (run).


##### `_launch_jobs`  (lines 246–289)

```
def _launch_jobs(runtime: Runtime, sync_driver: SyncDriver, page_feed: CorePageFeed) -> None
```

**Purpose**: Registers and starts the server's background jobs. These jobs include syncing sources, dispatching queued turns, cleaning up sandboxes, and reacting to page changes.

**Data flow**: It receives the prepared runtime, sync driver, and page feed. It builds an admission helper, creates job runners and job bindings from core and extension jobs, and launches them through the job system.

**Call relations**: `run` calls this after the runtime and DBOS client are ready. Inside, it creates an `invoker_for` helper so each job can admit work into the correct workspace.

*Call graph*: called by 1 (run); 8 external calls (__init__, __init__, __init__, __init__, __init__, durable_surfaces, bindings_from, core_jobs).


##### `_launch_jobs.invoker_for`  (lines 260–261)

```
def invoker_for(workspace_id: UUID) -> AdmissionInvoker
```

**Purpose**: Creates a workspace-specific admission invoker for background jobs. An admission invoker is the object that knows how to submit work for one workspace.

**Data flow**: It takes a workspace ID, combines it with the shared admission object, and returns an `AdmissionInvoker` tied to that workspace.

**Call relations**: _launch_jobs passes this helper into job runners. When a job needs to admit or re-admit work for a particular workspace, the runner calls this helper to get the right scoped invoker.

*Call graph*: 1 external calls (__init__).


##### `_sandbox_fs_minter`  (lines 292–308)

```
def _sandbox_fs_minter(blob: BlobConfig) -> SandboxFsCredentialMinter | None
```

**Purpose**: Builds the credential minter needed when workspace files live in S3. A minter creates short-lived cloud credentials so a sandbox can access only the storage path it should.

**Data flow**: It reads the blob storage configuration. For non-S3 storage it returns `None`. For S3, it checks required bucket, endpoint, and role fields, then returns a `SandboxFsCredentialMinter` backed by an AWS STS client.

**Call relations**: `run` calls this while constructing the runtime. The resulting object is passed to sandbox-related code so sandbox file mounts can be prepared safely.

*Call graph*: called by 1 (run); 2 external calls (__init__, __init__).


##### `_select_carrier`  (lines 311–351)

```
def _select_carrier(config: Config, manifests: tuple[Manifest, ...]) -> Carrier
```

**Purpose**: Chooses the sandbox carrier, meaning the backend that actually runs sandboxed work. It supports the built-in local carrier and any carriers contributed by extensions.

**Data flow**: It builds a name-to-factory map from the built-in carrier and extension manifests, rejects duplicate names, looks up the configured backend, validates remote backend proxy requirements, and returns one carrier instance.

**Call relations**: `run` calls this before creating the runtime. The selected carrier is later used by turn execution and cleanup jobs to create and reap sandboxes.

*Call graph*: called by 1 (run); 2 external calls (__init__, urlparse).


##### `_source_backends`  (lines 354–368)

```
def _source_backends(manifests: tuple[Manifest, ...]) -> dict[str, SourceBackend]
```

**Purpose**: Builds the list of source-sync backends available to the sync driver. A source backend knows how to read pages or files from a particular kind of source.

**Data flow**: It starts with the built-in folder source, then reads extension source providers. For each extension, it creates credential access limited to that extension's declared credential slots, rejects duplicate backend names, and returns the backend map.

**Call relations**: `run` calls this when building the `SyncDriver`. The sync driver later uses this map to choose the right implementation for each source row.

*Call graph*: called by 1 (run); 2 external calls (__init__, __init__).


##### `_select_hub`  (lines 371–389)

```
def _select_hub(config: Config, manifests: tuple[Manifest, ...]) -> Hub
```

**Purpose**: Chooses the live-frame hub, which is the process-wide channel for streaming live updates to surfaces. It can use the built-in in-process hub or an extension-provided hub.

**Data flow**: It collects hub builders from the built-in option and extension manifests, rejects duplicate backend names, looks up the configured backend, and returns the built hub.

**Call relations**: `run` calls this early and stores the hub in both the runtime and app state. Surface routes and tailers later use it to stream updates.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_cdp_provider`  (lines 392–420)

```
def _select_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> CdpProvider | None
```

**Purpose**: Chooses a browser-control provider if one is installed and selected. CDP means Chrome DevTools Protocol, a way for software to control a browser.

**Data flow**: It scans extension manifests for CDP provider specs, rejects duplicate backend names, looks up the configured provider, checks whether credentials are required, and returns a built provider or `None`.

**Call relations**: `run` uses this to put a browser provider into the runtime. `_require_cdp_provider` also calls it during boot validation when an extension says browser support is required.

*Call graph*: called by 2 (_require_cdp_provider, run); 1 external calls (__init__).


##### `_validate_requires`  (lines 423–447)

```
def _validate_requires(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Checks that every active extension's declared required capabilities are actually available. This turns missing dependencies into startup errors instead of surprising failures during a user action.

**Data flow**: It reads each manifest's `requires` list, finds the matching readiness check, runs it, and wraps any failure with a message naming the extension and missing capability.

**Call relations**: `run` calls this after loading manifests and credentials. It delegates to seam-specific checks such as `_require_cdp_provider`, `_require_search_provider`, and `_require_memory_search`.

*Call graph*: called by 1 (run).


##### `_require_cdp_provider`  (lines 450–464)

```
def _require_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that a usable browser-control provider exists when an extension requires one.

**Data flow**: It calls `_select_cdp_provider`. If selection returns `None`, it raises an error explaining that the configured provider is required but not registered.

**Call relations**: _validate_requires calls this for extensions that require the `cdp_providers` seam. It uses the same provider selection logic that `run` uses for normal runtime setup.

*Call graph*: calls 1 internal fn (_select_cdp_provider).


##### `_select_search_provider`  (lines 467–502)

```
def _select_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> SearchProvider | None
```

**Purpose**: Chooses the search provider used by research tools. It returns no provider when search is not configured, unless another validation step requires it.

**Data flow**: It scans extension manifests for search providers, rejects duplicate names, reads the configured provider name, validates that it exists and has credential support when needed, and returns the built provider or `None`.

**Call relations**: `run` calls this while creating the runtime. `_require_search_provider` calls it to prove that a research extension has a working backend before startup finishes.

*Call graph*: called by 2 (_require_search_provider, run); 2 external calls (__init__, __init__).


##### `_require_search_provider`  (lines 505–518)

```
def _require_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that research search is configured and usable when an extension depends on it.

**Data flow**: It checks that the search provider config is set, then calls `_select_search_provider` to validate the named backend and credentials. It raises an error if anything is missing.

**Call relations**: _validate_requires calls this for extensions that require search support. It reuses the same selection path used by `run`.

*Call graph*: calls 1 internal fn (_select_search_provider).


##### `_require_memory_search`  (lines 521–547)

```
def _require_memory_search(_config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Checks that exactly one default memory-search provider is installed and usable. Memory search lets the system look up stored knowledge or past content.

**Data flow**: It searches all manifests for the default memory-search provider name, errors if none or more than one are found, and checks that any required credential key is available.

**Call relations**: _validate_requires can call this when an extension declares that memory search is required. It does not build the provider here; it verifies the startup contract.


##### `_select_auth_proxy`  (lines 559–596)

```
def _select_auth_proxy(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> AuthProxy | None
```

**Purpose**: Chooses the fallback authentication proxy used by connector feed sync when a connector does not have its own broker. An auth proxy is the host-side component that supplies credentials without exposing them directly to sandbox code.

**Data flow**: It scans manifests for auth proxy specs, rejects duplicate names, chooses the configured backend or the only available backend, checks credentials, and returns a built auth proxy or `None` if none are installed.

**Call relations**: _connector_registry calls this while building the connector registry. The registry later uses the selected fallback when syncing connector-backed feeds.

*Call graph*: called by 1 (_connector_registry); 2 external calls (__init__, __init__).


##### `_mount_ext_routes`  (lines 599–637)

```
def _mount_ext_routes(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, index: IndexBackend, embed: EmbedClient) -> None
```

**Purpose**: Adds extension-defined HTTP routes under `/ext/<extension-name>/...`. Each route must first identify the workspace for the request before extension code can run.

**Data flow**: It loops through manifests and route specs, builds an extension context with credential access, creates an endpoint wrapper for each route, and registers that wrapper on the FastAPI app.

**Call relations**: `run` calls this after the core app is created. The nested endpoint wrapper is what actually runs when an extension route receives a request.

*Call graph*: called by 1 (run); 2 external calls (add_route, context_for).


##### `_mount_ext_routes.endpoint`  (lines 621–631)

```
async def endpoint(request: Request, handler=spec.handler, identify=spec.identify, extension_context=context) -> Response
```

**Purpose**: Wraps one extension route with authorization and workspace scoping. It prevents extension handlers from running unless the request can be tied to a workspace.

**Data flow**: It receives a request, asks the route's identify function for a workspace, returns a 401 response if identification fails, otherwise enters that workspace scope and calls the extension's handler with its context.

**Call relations**: FastAPI calls this wrapper when a matching `/ext/...` route is requested. `_mount_ext_routes` creates one such wrapper per extension route.

*Call graph*: 2 external calls (Response, ws).


##### `WorkspaceScopeBoundary.__call__`  (lines 658–666)

```
async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None
```

**Purpose**: Clears the current workspace before and after each HTTP request. This protects a shared process from accidentally carrying one workspace's identity into another request.

**Data flow**: It receives the raw ASGI request scope. For non-HTTP traffic it passes through unchanged. For HTTP traffic it sets the current workspace to `None`, runs the downstream app, and always clears the workspace again afterward.

**Call relations**: _mount_shared_surfaces installs this as middleware. Surface endpoints then set the current workspace for their request, and this boundary guarantees the setting is cleaned up after the full response is sent.

*Call graph*: 1 external calls (set).


##### `_mount_shared_surfaces`  (lines 669–750)

```
def _mount_shared_surfaces(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: BlobStore, hub: Hub, dbos_client: DBOSClient, artifact_secret: str, public_base_url
```

**Purpose**: Adds shared product surface routes under `/surface/...` and sets up the per-request workspace boundary. A surface is a user-facing integration point, such as a chat or app interface.

**Data flow**: It installs the workspace-clearing middleware, creates shared admission and hub-tail helpers, loops through surface specs from manifests, builds authentication helpers, registers route wrappers, and creates a writeback poller when any durable surface needs outgoing delivery.

**Call relations**: `run` calls this after extension routes are mounted. The nested `context_for` builds request contexts, the nested endpoint handles each surface request, and `_serve_lifespan` later starts the writeback poller if one was installed.

*Call graph*: called by 1 (run); 10 external calls (__init__, __init__, __init__, __init__, add_middleware, add_route, durable_surfaces, writeback_workspaces, log, uuid4).


##### `_mount_shared_surfaces.context_for`  (lines 696–706)

```
def context_for(workspace_id: UUID, surface: str) -> SurfaceContext
```

**Purpose**: Builds the `SurfaceContext` that a surface handler receives for one workspace and one surface. This context bundles the tools a surface needs to admit work, stream updates, read credentials, and create artifact links.

**Data flow**: It takes a workspace ID and surface name, combines them with shared objects such as blob storage, the admission helper, hub tailer, credentials, artifact secret, and public base URL, and returns a ready-to-use context.

**Call relations**: _mount_shared_surfaces.endpoint calls this after a request has been identified. The writeback poller also receives this factory so it can deliver durable surface output under the correct workspace.

*Call graph*: 2 external calls (__init__, __init__).


##### `_mount_shared_surfaces.endpoint`  (lines 722–736)

```
async def endpoint(request: Request, handler=route.handler, identify=resolver, surface=spec.name, surface_auth=auth) -> Response
```

**Purpose**: Wraps one shared surface route with surface authentication and workspace binding. It makes sure the route's handler runs only for the workspace proven by the request.

**Data flow**: It receives a request, asks the surface identify function to resolve it, returns a custom response or 401 when identification fails, sets the current workspace when it succeeds, builds a surface context, and calls the route handler.

**Call relations**: FastAPI calls this wrapper for `/surface/...` requests. It relies on `WorkspaceScopeBoundary` to clean up the workspace after the full response, including streamed response bodies.

*Call graph*: 3 external calls (Response, set, context_for).


##### `_serve_lifespan`  (lines 754–777)

```
async def _serve_lifespan(app: FastAPI) -> AsyncIterator[None]
```

**Purpose**: Runs background tasks that should live for the same lifetime as the FastAPI app. These include executor recovery, cancel reconciliation, and durable surface writeback polling.

**Data flow**: When the app starts, it opens an async task group and starts the recovery and cancellation loops, plus the writeback poller if present. When the app shuts down, it cancels those tasks.

**Call relations**: `run` passes this as the FastAPI lifespan function. It uses app state populated earlier by `run` and `_mount_shared_surfaces`.

*Call graph*: 3 external calls (__init__, __init__, TaskGroup).


##### `_proxy_endpoint`  (lines 780–806)

```
def _proxy_endpoint(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, pricing: Pricing) -> ProxyEndpoint
```

**Purpose**: Chooses how sandboxes reach the egress proxy, which controls outbound network access. It supports either an in-process local proxy or a separate public proxy service.

**Data flow**: It reads sandbox proxy config. If no public proxy URL is set, it starts a local egress proxy and returns its endpoint. If a public URL is set, it reads the required CA certificate from the environment and returns endpoint details for the external proxy.

**Call relations**: `run` calls this while building the runtime. It delegates to `_local_egress_proxy` for single-node local deployments.

*Call graph*: calls 1 internal fn (_local_egress_proxy); called by 1 (run); 1 external calls (__init__).


##### `_local_egress_proxy`  (lines 809–842)

```
def _local_egress_proxy(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, pricing: Pricing) -> ProxyEndpoint
```

**Purpose**: Starts an in-process egress proxy for local single-node sandbox runs. This proxy is the sandbox's controlled path to the outside network.

**Data flow**: It builds per-agent proxy rules, creates a separate event loop in a background thread, starts the proxy asynchronously, waits up to the startup timeout, and returns the proxy endpoint.

**Call relations**: _proxy_endpoint calls this when no standalone proxy URL is configured. It uses `_local_rule_base` to build static model-provider rules and its nested `_boot` coroutine to actually start the network service.

*Call graph*: calls 1 internal fn (_local_rule_base); called by 1 (_proxy_endpoint); 7 external calls (__init__, __init__, new_event_loop, run_coroutine_threadsafe, Thread, connector_clis, connector_transfer_hosts).


##### `_local_egress_proxy._boot`  (lines 832–840)

```
async def _boot() -> ProxyEndpoint
```

**Purpose**: Boots the local proxy service inside the background event loop. It creates temporary certificate authority material and starts the proxy listener.

**Data flow**: It generates a certificate authority, builds an `EgressProxy` with rule resolution, authorization, certificates, and pricing, starts it on the configured port, and returns the resulting proxy endpoint.

**Call relations**: _local_egress_proxy schedules this coroutine on the proxy's separate event loop. The returned endpoint is handed back up to `_proxy_endpoint` and then into the runtime.

*Call graph*: 2 external calls (__init__, generate_ca).


##### `_local_rule_base`  (lines 845–859)

```
def _local_rule_base(config: Config, manifests: tuple[Manifest, ...]) -> tuple[Rule, ...]
```

**Purpose**: Builds the static allow rules for the in-process proxy. It only permits shared model-provider egress and refuses credential injection in the shared fleet.

**Data flow**: It scans manifests for credential slots that require injection into outbound requests. If any exist, it raises an error. Otherwise it returns the model-provider rule base from config.

**Call relations**: _local_egress_proxy calls this before starting the proxy. This keeps the local proxy safe for shared-fleet use and directs deployments needing credential injection to the standalone proxy.

*Call graph*: called by 1 (_local_egress_proxy); 1 external calls (model_rule_base).


##### `_connector_registry`  (lines 865–886)

```
def _connector_registry(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> ConnectorRegistry
```

**Purpose**: Builds the connector registry used by tools and sync jobs to understand installed connector providers. Connectors are integrations that can authorize and sync from external services.

**Data flow**: It scans manifests for connector definitions, rejects duplicate OAuth provider names, creates connector entries, selects a fallback auth proxy, and returns a `ConnectorRegistry`.

**Call relations**: `run` calls this before creating the runtime. It calls `_select_auth_proxy` for unbrokered providers that need a shared fallback credential path.

*Call graph*: calls 1 internal fn (_select_auth_proxy); called by 1 (run); 2 external calls (__init__, __init__).


##### `_connect_flow`  (lines 889–913)

```
def _connect_flow(credentials: CredentialStore | None, config: Config, manifests: tuple[Manifest, ...]) -> ConnectFlow | None
```

**Purpose**: Creates the OAuth connect flow for connector authorization. This is the flow that sends a user to a provider, receives the callback, and stores the resulting grant.

**Data flow**: If no credential store exists, it returns `None`. Otherwise it gathers OAuth providers from manifests, rejects duplicate provider names, computes the redirect URI, and returns a `ConnectFlow` using the credential store's encryption key and a grant store.

**Call relations**: `run` calls this and installs the result globally with `install_connect_flow`. It calls `_connect_redirect_uri` to validate the external callback URL.

*Call graph*: calls 1 internal fn (_connect_redirect_uri); called by 1 (run); 2 external calls (__init__, __init__).


##### `_connect_redirect_uri`  (lines 916–940)

```
def _connect_redirect_uri(config: Config, providers: Mapping[str, OAuthProvider]) -> str
```

**Purpose**: Builds and validates the public OAuth callback URL. OAuth providers must be able to redirect the user's browser back to this address.

**Data flow**: It reads `connect.public_base_url` from config and checks whether any connector providers are installed. If connectors exist, it requires a URL with a scheme and public host, rejects local bind addresses, and appends the fixed callback path.

**Call relations**: _connect_flow calls this while building the OAuth flow. Its result is given to providers as the redirect URI used by both the outbound authorization request and the inbound callback.

*Call graph*: called by 1 (_connect_flow); 1 external calls (urlparse).

## 📊 State Registers Touched

- `reg-effective-config` — The combined settings that tell the service which features, adapters, limits, and deployment options to use.
- `reg-extension-pack-lock` — The saved choice of active packs and installed extensions for a workspace.
- `reg-capability-registry` — The loaded menu of extension-provided routes, tools, skills, hooks, jobs, credentials, models, and search backends.
- `reg-sandbox-session-policy` — The safe execution room state: sandbox handles, mounted workspace files, storage access, and restrictions.
- `reg-egress-proxy-state` — The controlled network gateway state that decides which outside sites sandboxed work may contact and how usage is counted.
- `reg-live-event-stream` — The live progress channel that lets clients attach, resume, and receive streamed turn updates.
- `reg-artifact-blob-store` — The shared file and blob storage used for uploaded content, generated artifacts, and token-protected downloads.
- `reg-runtime-instance-fleet` — The heartbeat records for live worker and runtime processes, used to detect dead workers and clean up abandoned work.
- `reg-observability-trace-state` — The logs, metrics, traces, traceparent links, and redaction rules used to monitor work safely.
- `reg-background-job-registry` — The registered set of built-in and extension background workflows that the scheduler can run.
- `reg-schema-migration-version` — The Alembic/schema version state recording which core and extension migrations have been applied before runtime uses the database.
- `reg-database-connection-pool` — The process-wide database engine/session factory and connection pool used by request handlers, workers, schedulers, and persistence code.
- `reg-secret-keyring` — Loaded signing and encryption key material used to mint/verify tokens and seal/unseal protected secrets across trusted paths.
- `reg-process-lifecycle-state` — Process-wide startup/shutdown state: background task handles, drain/cancel signals, and resource close callbacks created during service bootstrap.
- `reg-redis-connection-pool` — Process-wide Redis client/connection pool and stream backend handles used to distribute live turn events across server processes.
- `reg-adapter-implementation-registry` — Process-wide mapping from configured backend/provider names to implementation adapters for Redis hubs, sandboxes, browsers, models, search, sources, and related services.
- `reg-http-client-pools` — Shared outbound HTTP client/session pools and retry-capable transport state used for provider APIs, OAuth/credential bridges, connectors, model calls, billing, email, and other integrations.
