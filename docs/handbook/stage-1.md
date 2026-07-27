# Operational entrypoints, command dispatch, and deployment preflight  `stage-1`

This stage is the system’s set of front doors and preflight checks. It is used when a person starts UFO, prepares it for deployment, runs administrator tasks, or proves that a sandbox is safe to serve traffic. The main user-facing door is core/src/ufo/cli.py, which defines ufoctl. This command-line tool lets people create a workspace, run it, inspect it, package it, and connect it to extensions. When packaging is needed, core/src/ufo/bundle.py gathers the current settings and chosen extensions into a self-contained folder, so the same setup can be rebuilt as a Docker image on another machine. For the hosted control service, control/src/ufo_control/main.py starts the web gateway and runs admin jobs such as database setup, invite creation, Slack retry work, and security setup. Before deployment, the sandbox checks act like test pilots. sandbox/mount_gate.py starts a real sandbox and proves the shared /workspace folder can be mounted and used. sandbox/proxy_gate.py proves the sandbox HTTPS proxy path works and returns the expected controlled response.

## Files in this stage

### Workspace CLI packaging
Human-facing workspace commands begin in the main CLI and include repeatable bundle creation for deployment.

### `core/src/ufo/cli.py`

`entrypoint` · `command invocation`

This file turns the UFO runtime into everyday terminal commands. Without it, a user would have to call lower-level Python code or edit database records by hand to do basic jobs like creating a workspace, starting the server, chatting with the agent, setting spending limits, storing API keys, installing extensions, or building a deployable bundle.

The file is built around Click, a Python library for command-line programs. The top-level `main` command loads local secrets from a `.env` file, then dispatches to subcommands such as `init`, `serve`, `chat`, `spend`, `credential`, `ext`, and `bundle`.

Several commands are thin switches that hand off to deeper subsystems. For example, `serve` starts the runtime, `proxy` starts the egress proxy, and `migrate` updates the database schema. Others do more coordination. `init` writes a default config, creates needed development secrets, prepares the database, runs onboarding, and stores a long-lived CLI token. `chat` sends a user message to the local server and renders the streamed answer, including status updates and private credential prompts.

A useful way to think of this file is as the project’s control panel. It does not contain the whole engine, but it gives humans safe buttons and readouts for the engine’s most important operations.

#### Function details

##### `_ufoctl_dir`  (lines 63–65)

```
def _ufoctl_dir() -> Path
```

**Purpose**: Finds the private local folder where `ufoctl` stores machine-specific files, such as the CLI token and current chat session. It lets tests or deployments override the location with an environment variable.

**Data flow**: It reads the `UFOCTL_DIR` environment variable. If that value exists, it returns it as a path; otherwise it returns a `.ufoctl` folder inside the user’s home directory.

**Call relations**: Startup and user commands call this when they need local CLI state. `init` uses it to save the token, `chat` uses it to find the token, and `_session` uses it to remember or create the conversation id.

*Call graph*: called by 3 (_session, chat, init); 2 external calls (Path, home).


##### `_dotenv_path`  (lines 68–69)

```
def _dotenv_path() -> Path
```

**Purpose**: Locates the `.env` file that sits beside the main UFO config file. This is where local secrets created by `init` are stored.

**Data flow**: It asks the config system where the config file is, takes that file’s parent folder, and returns the path to `.env` inside it.

**Call relations**: `main` reaches it through `_load_dotenv` before commands run. `init` and `_write_dev_secrets` use the same location so the secrets they write are the same secrets later loaded by the CLI and server.

*Call graph*: called by 3 (_load_dotenv, _write_dev_secrets, init); 1 external calls (config_path).


##### `_dotenv_pairs`  (lines 72–88)

```
def _dotenv_pairs(text: str) -> list[tuple[str, str]]
```

**Purpose**: Reads simple `.env` text and extracts environment variable names and values. It supports only the small format this tool needs: one `KEY=VALUE` per line.

**Data flow**: It receives raw text, skips blank lines and comments, strips an optional `export`, removes matching surrounding quotes, and returns a list of `(name, value)` pairs.

**Call relations**: `_load_dotenv` uses it to import variables into the process. `_write_dev_secrets` uses it to avoid writing duplicate secrets that are already present.

*Call graph*: called by 2 (_load_dotenv, _write_dev_secrets).


##### `_load_dotenv`  (lines 91–100)

```
def _load_dotenv() -> None
```

**Purpose**: Loads local secret values from the `.env` file into the current process before any command tries to read them. Already-exported environment variables take priority.

**Data flow**: It finds the `.env` path, stops if the file does not exist, parses the file, and fills only environment variables that are not already set.

**Call relations**: `main` calls this at the start of every CLI run. That makes commands like `init`, `serve`, and `credential set` work smoothly after local secrets have been written.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (main).


##### `main`  (lines 104–106)

```
def main() -> None
```

**Purpose**: Defines the top-level `ufoctl` command group. It prepares the command environment before Click routes to a specific subcommand.

**Data flow**: It takes no user data itself, calls `_load_dotenv`, and then Click continues to whichever subcommand the user requested.

**Call relations**: Every command in this file hangs under `main`. It is the shared doorway that runs before `init`, `chat`, `serve`, `spend`, extension commands, and the other subcommands.

*Call graph*: calls 1 internal fn (_load_dotenv).


##### `init`  (lines 112–140)

```
def init(email: str, model: str) -> None
```

**Purpose**: Sets up a new local UFO workspace from the terminal. It creates a default config if needed, prepares secrets and database schema, onboards the owner and default agent, and saves a CLI login token.

**Data flow**: It receives an owner email and model name, writes missing config and secrets, creates a PostgreSQL system database when needed, applies migrations, runs onboarding, mints a bearer token, and stores that token in the local `ufoctl` folder.

**Call relations**: This is usually the first command a user runs. It coordinates `_write_dev_secrets`, `_create_postgres_system_database`, `_onboard`, and `_ufoctl_dir`, then hands the prepared workspace to later commands like `serve` and `chat`.

*Call graph*: calls 5 internal fn (_create_postgres_system_database, _dotenv_path, _onboard, _ufoctl_dir, _write_dev_secrets); 7 external calls (run, ClickException, echo, mint_token, config_path, load_config, apply_migrations).


##### `_write_dev_secrets`  (lines 143–165)

```
def _write_dev_secrets(config: Config) -> tuple[str, ...]
```

**Purpose**: Creates local development secrets that the server and CLI need, without overwriting secrets the user already supplied. These include encryption and token-signing keys.

**Data flow**: It receives the loaded config, builds candidate secret values, reads existing `.env` content and environment variables, writes only missing names to `.env`, sets those new names in the current process, and returns the names it added.

**Call relations**: `init` calls this before onboarding and token creation. It makes the local zero-service setup work without asking the user to manually generate or export several secret keys.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 2 external calls (generate_key, token_urlsafe).


##### `_onboard`  (lines 168–187)

```
async def _onboard(config: Config, email: str, model: str) -> Onboarded
```

**Purpose**: Creates the first workspace records and runs extension onboarding steps. It makes sure the core workspace and owner exist before add-ons try to add their own setup.

**Data flow**: It receives config, email, and model name, opens the database connection layer, optionally builds an encrypted credential store, loads extension manifests, runs onboarding, and returns the created onboarding summary. It always closes the database layer afterward.

**Call relations**: `init` calls this after schema setup. It delegates the actual workspace creation to the onboarding subsystem and supplies extension manifests so installed extensions can participate.

*Call graph*: called by 1 (init); 6 external calls (__init__, __init__, Fernet, dispose_db, init_db, load_manifests).


##### `_create_postgres_system_database`  (lines 190–201)

```
async def _create_postgres_system_database(config: Config) -> None
```

**Purpose**: Creates the separate PostgreSQL system database if the configured setup needs one and it is missing. This avoids a manual database-administration step during initialization.

**Data flow**: It converts the configured async database address into a normal PostgreSQL connection string, connects, checks whether the system database exists, creates it if absent, and then closes the connection.

**Call relations**: `init` calls this only for PostgreSQL configurations. It runs before migrations so the database objects that migrations expect can exist.

*Call graph*: called by 1 (init); 1 external calls (connect).


##### `migrate`  (lines 205–222)

```
def migrate() -> None
```

**Purpose**: Updates the database schema to the latest version for core UFO and active extensions. This is the command to run after installing an extension that owns tables.

**Data flow**: It loads config, optionally uses an owner database URL from the environment, normalizes that URL for async access, applies migrations, and prints confirmation.

**Call relations**: Users or deployment jobs call this outside the main server loop. It hands the actual schema work to `apply_migrations`, using the owner connection when shared-schema deployments need extra database privileges.

*Call graph*: 3 external calls (echo, load_config, apply_migrations).


##### `serve`  (lines 226–228)

```
def serve() -> None
```

**Purpose**: Starts the main UFO runtime: surfaces, workers, jobs, and the embedded proxy in single-node setups.

**Data flow**: It takes no extra CLI data and directly calls the server runner. The process then becomes the running UFO service.

**Call relations**: This is a command wrapper around the serving subsystem. After `init` and migrations prepare the workspace, `serve` starts the long-running runtime that `chat` talks to.

*Call graph*: 1 external calls (run).


##### `proxy`  (lines 232–234)

```
def proxy() -> None
```

**Purpose**: Starts the shared egress proxy service. This proxy fronts sandbox network traffic for workspaces in deployments that use it separately.

**Data flow**: It takes no extra CLI data and calls the proxy runner, which takes over the process.

**Call relations**: This is a command wrapper around the proxy-serving subsystem. It is used in deployments where the proxy is run as its own service rather than embedded with `serve`.

*Call graph*: 1 external calls (run).


##### `chat`  (lines 240–258)

```
def chat(message: str | None, new: bool) -> None
```

**Purpose**: Lets a user talk to the agent from the terminal. It can send one message or open a simple interactive prompt that keeps using the same conversation session.

**Data flow**: It loads config, reads the saved CLI token, chooses or creates a session id, then sends each non-empty user message through `_run_turn`. If the token is missing, it tells the user to run `ufoctl init` first.

**Call relations**: This command is the human side of a conversation. It relies on `_session` for continuity and `_run_turn` for the actual request to the running `serve` process.

*Call graph*: calls 3 internal fn (_run_turn, _session, _ufoctl_dir); 3 external calls (ClickException, echo, load_config).


##### `_session`  (lines 261–267)

```
def _session(new: bool) -> str
```

**Purpose**: Chooses the conversation channel used by `chat`. It keeps a session id on disk so separate CLI invocations can continue the same conversation unless the user asks for a new one.

**Data flow**: It reads the local session file. If `new` is true or the file is missing, it creates the local folder if needed, writes a fresh random id, and returns the stored id.

**Call relations**: `chat` calls this before sending messages. The returned id becomes part of the server path used by `_run_turn` and `_stream_turn`.

*Call graph*: calls 1 internal fn (_ufoctl_dir); called by 1 (chat); 1 external calls (uuid4).


##### `_run_turn`  (lines 270–284)

```
def _run_turn(config: Config, token: str, channel: str, message: str) -> None
```

**Purpose**: Runs one chat turn and turns connection problems into friendly terminal errors. It also explains what happens if the user interrupts a turn.

**Data flow**: It receives config, token, channel, and message, builds the local server base URL, runs `_stream_turn`, and catches keyboard interrupts or HTTP failures to print or raise clear messages.

**Call relations**: `chat` calls this for each user message. It bridges the synchronous terminal command to the asynchronous streaming code in `_stream_turn`.

*Call graph*: calls 1 internal fn (_stream_turn); called by 1 (chat); 3 external calls (run, ClickException, echo).


##### `_stream_turn`  (lines 296–304)

```
async def _stream_turn(base: str, token: str, channel: str, message: str) -> None
```

**Purpose**: Opens an HTTP client and streams one chat turn from the server to the terminal display. HTTP is the web protocol used here to talk to the local UFO service.

**Data flow**: It receives the server base URL, token, channel, and message, creates a display object and authorization headers, opens an async HTTP client, and asks `_ChatStream` to run the turn.

**Call relations**: `_run_turn` calls this inside `asyncio.run`. It sets up the transport pieces that `_ChatStream.run` uses to post the message and render server directives.

*Call graph*: called by 1 (_run_turn); 3 external calls (__init__, __init__, AsyncClient).


##### `_ChatStream.run`  (lines 318–329)

```
async def run(self, message: str) -> None
```

**Purpose**: Drives a full chat turn, including reconnects and private credential prompts. It keeps asking the server for updates until the turn is finished.

**Data flow**: It starts with the user’s message as the request body. After each stream drain, it either waits and reconnects with an empty body for polling, closes the display and fulfills requested secrets, or returns when nothing more is pending.

**Call relations**: `_stream_turn` creates `_ChatStream` and calls this. It repeatedly uses `_drain` for the streamed server response and calls `_fulfill_secret` if the server asked for credentials.

*Call graph*: calls 2 internal fn (_drain, _fulfill_secret); 1 external calls (sleep).


##### `_ChatStream._drain`  (lines 331–371)

```
async def _drain(self, body: str) -> _Pending
```

**Purpose**: Reads one streamed response from the chat surface and turns each server directive into terminal output or pending follow-up work. A directive is a small command line from the server, such as text to print or a request to poll again.

**Data flow**: It posts a request body to the chat path, checks the HTTP status, reads lines from the response, unescapes tab-separated fields, updates the display for text/status messages, records secret prompts, records poll timing, and returns a `_Pending` summary.

**Call relations**: `_ChatStream.run` calls this each time it opens or reopens the stream. It hands display work to `_TurnDisplay` and parsing help to `_unescape`.

*Call graph*: calls 1 internal fn (_unescape); called by 1 (run); 2 external calls (__init__, ClickException).


##### `_ChatStream._fulfill_secret`  (lines 373–390)

```
async def _fulfill_secret(self, sealed: str, slot: str, prompt: str) -> None
```

**Purpose**: Collects one private credential value from the user and sends it to the server outside the normal chat transcript. This keeps secrets from being treated like conversation text.

**Data flow**: It receives the server’s sealed prompt information, asks the user for a hidden value, posts that value with special headers, checks for success, then prints any acknowledgement the server returns.

**Call relations**: `_ChatStream.run` calls this after the normal turn stream closes and `_drain` has collected secret prompts. It uses `_unescape` to read acknowledgement directives and `_TurnDisplay.line` to show them.

*Call graph*: calls 1 internal fn (_unescape); called by 1 (run); 2 external calls (ClickException, prompt).


##### `_unescape`  (lines 396–408)

```
def _unescape(text: str) -> str
```

**Purpose**: Decodes the simple escaping used in chat stream directives. It turns written escape sequences like `\t` and `\n` back into real tabs and newlines.

**Data flow**: It receives one text field, scans it character by character, replaces recognized backslash escapes, and returns the decoded string.

**Call relations**: Both `_ChatStream._drain` and `_ChatStream._fulfill_secret` use it before interpreting server fields. This keeps the stream format safe even when text contains tabs, newlines, or backslashes.

*Call graph*: called by 2 (_drain, _fulfill_secret).


##### `_TurnDisplay.text`  (lines 425–429)

```
def text(self, delta: str) -> None
```

**Purpose**: Prints streamed answer text exactly as it arrives. It is used for partial text chunks, not necessarily complete lines.

**Data flow**: It receives a text delta, erases any temporary status meter first, writes the delta to standard output without forcing a newline, and records whether the current output line is still open.

**Call relations**: `_ChatStream._drain` calls this when the server sends a `txt` directive. It works with `_erase_meter` so status text does not overwrite answer text.

*Call graph*: calls 1 internal fn (_erase_meter); 1 external calls (echo).


##### `_TurnDisplay.line`  (lines 431–435)

```
def line(self, text: str) -> None
```

**Purpose**: Prints a complete line from the server, such as a non-streamed answer, failure message, link, or credential acknowledgement.

**Data flow**: It receives text, closes any unfinished streamed line, then writes the text with a newline to standard output.

**Call relations**: `_ChatStream._drain` and `_ChatStream._fulfill_secret` use this for `say` directives. It relies on `_close_line` to keep terminal output tidy.

*Call graph*: calls 1 internal fn (_close_line); 1 external calls (echo).


##### `_TurnDisplay.activity`  (lines 437–441)

```
def activity(self, note: str) -> None
```

**Purpose**: Shows a mid-turn activity note, such as a tool call or skill load, as a dim standalone line. This gives the user progress context without mixing it into the answer text.

**Data flow**: It receives a note, closes any unfinished line, styles the note dimly, and prints it to standard output.

**Call relations**: `_ChatStream._drain` calls this for `note` directives. Like other finished-line output, it uses `_close_line` first so streamed text is not corrupted.

*Call graph*: calls 1 internal fn (_close_line); 2 external calls (echo, style).


##### `_TurnDisplay.meter`  (lines 443–452)

```
def meter(self, text: str) -> None
```

**Purpose**: Shows a temporary status meter on terminals that support it. The meter is like a pencil note in the margin: visible while waiting, erased before real output continues.

**Data flow**: It receives status text, does nothing when output is not a terminal, moves to a clean line if streamed text is open, writes an erasable dim status line to standard error, and marks the meter as shown.

**Call relations**: `_ChatStream._drain` calls this for `status` directives. Later calls to `text`, `line`, `activity`, or `close` erase it before printing lasting content.

*Call graph*: 2 external calls (echo, style).


##### `_TurnDisplay.close`  (lines 454–455)

```
def close(self) -> None
```

**Purpose**: Finishes the display for a turn cleanly. It makes sure any temporary meter is gone and any partial line ends properly.

**Data flow**: It takes the display’s current line state and delegates to `_close_line`, which erases transient status and adds a newline if needed.

**Call relations**: `_ChatStream.run` calls this when the streamed turn is complete and before secret prompts are fulfilled. It is the final tidy-up step for normal streamed output.

*Call graph*: calls 1 internal fn (_close_line).


##### `_TurnDisplay._erase_meter`  (lines 457–461)

```
def _erase_meter(self) -> None
```

**Purpose**: Removes the temporary status meter from the terminal if one is visible. This prevents progress text from being left behind or mixed into the answer.

**Data flow**: It checks whether a meter is currently shown. If so, it writes the terminal erase sequence to standard error and records that no meter is visible.

**Call relations**: `text` calls this before streaming answer text, and `_close_line` calls it before printing line endings. It is an internal helper for keeping terminal output clean.

*Call graph*: called by 2 (_close_line, text); 1 external calls (echo).


##### `_TurnDisplay._close_line`  (lines 463–467)

```
def _close_line(self) -> None
```

**Purpose**: Closes any unfinished output line and clears temporary status text. This keeps later messages from starting in the middle of a streamed answer.

**Data flow**: It erases any meter, then checks whether a line is open. If a line is open, it prints a newline and marks the line closed.

**Call relations**: `line`, `activity`, and `close` call this before producing finished output or ending a turn. It depends on `_erase_meter` for the transient-status cleanup.

*Call graph*: calls 1 internal fn (_erase_meter); called by 3 (activity, close, line); 1 external calls (echo).


##### `spend_cap`  (lines 471–472)

```
def spend_cap() -> None
```

**Purpose**: Defines the `spend-cap` command group for reading and changing spending limits. These limits are enforced elsewhere when turns or model calls are admitted.

**Data flow**: It does not process data itself. It groups subcommands such as `set` and `list` under one CLI namespace.

**Call relations**: Click uses this as the parent for `spend_cap_set` and `spend_cap_list`. It gives users a clear place to control spend-cap settings.


##### `spend_cap_set`  (lines 483–501)

```
def spend_cap_set(scope: str, subject_id: str, window_seconds: int, limit_micro_usd: int, on_breach: str) -> None
```

**Purpose**: Creates or updates a spending cap for a workspace, member, or agent. It validates that the chosen scope has the right kind of subject id.

**Data flow**: It receives CLI options, checks whether a subject id is required or forbidden, converts the id to a UUID when present, loads config, writes the cap through `_write_spend_cap`, converts micro-dollars to dollars for display, and prints the result.

**Call relations**: Users call this under `ufoctl spend-cap set`. It performs input checks at the CLI edge and hands the database write to `_write_spend_cap`.

*Call graph*: calls 1 internal fn (_write_spend_cap); 5 external calls (run, ClickException, echo, load_config, UUID).


##### `spend_cap_list`  (lines 505–515)

```
def spend_cap_list() -> None
```

**Purpose**: Prints the spending caps currently set for the workspace. It gives operators a quick view of limits and breach behavior.

**Data flow**: It loads config, reads caps through `_read_spend_caps`, prints a no-caps message if empty, otherwise formats each cap with dollar amounts and scope details.

**Call relations**: Users call this under `ufoctl spend-cap list`. It relies on `_read_spend_caps` for database access and only handles terminal presentation.

*Call graph*: calls 1 internal fn (_read_spend_caps); 3 external calls (run, echo, load_config).


##### `_write_spend_cap`  (lines 518–572)

```
async def _write_spend_cap(config: Config, scope: str, subject: UUID | None, window_seconds: int, limit_micro_usd: int, on_breach: str) -> UUID
```

**Purpose**: Writes a spend cap into the database, updating an existing matching cap instead of creating a duplicate. Matching is based on workspace, scope, subject, and time window.

**Data flow**: It receives config and cap fields, opens the database layer, finds the workspace id, searches for an existing cap, updates it if found, otherwise inserts a new cap id and values, then returns the cap id and closes the database layer.

**Call relations**: `spend_cap_set` calls this after validating CLI input. It is the database-writing part of the spend-cap command.

*Call graph*: called by 1 (spend_cap_set); 7 external calls (insert, select, update, dispose_db, init_db, workspace_tx, uuid4).


##### `_read_spend_caps`  (lines 575–601)

```
async def _read_spend_caps(config: Config) -> list[tuple[UUID, str, UUID | None, int, int, str]]
```

**Purpose**: Reads all spend caps for the current workspace from the database. It returns plain rows that the CLI can format.

**Data flow**: It receives config, opens the database layer, finds the workspace id, selects cap fields for that workspace ordered by scope, converts rows into tuples, and closes the database layer.

**Call relations**: `spend_cap_list` calls this when a user wants to inspect limits. The command then turns the returned data into human-readable lines.

*Call graph*: called by 1 (spend_cap_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `spend`  (lines 609–629)

```
def spend(window_seconds: int) -> None
```

**Purpose**: Shows how much money the workspace has spent over a recent time window. It breaks the total down by useful categories such as member, agent, and price version.

**Data flow**: It receives a window size in seconds, loads config, reads a spend report through `_read_spend`, converts micro-dollars to dollars, and prints the total and breakdowns.

**Call relations**: Users call this directly as `ufoctl spend`. It delegates the accounting calculation to `_read_spend` and the accounting subsystem, then handles display.

*Call graph*: calls 1 internal fn (_read_spend); 3 external calls (run, echo, load_config).


##### `_read_spend`  (lines 632–639)

```
async def _read_spend(config: Config, window_seconds: int) -> SpendReport
```

**Purpose**: Asks the accounting subsystem to summarize ledger entries for the current workspace. A ledger is the record of charged usage.

**Data flow**: It receives config and a time window, opens the database layer, finds the workspace id, creates a `SpendRollup`, reads the report for that window, returns it, and closes the database layer.

**Call relations**: `spend` calls this to get data before printing. It connects the CLI to `SpendRollup`, which contains the actual rollup logic.

*Call graph*: called by 1 (spend); 5 external calls (__init__, select, dispose_db, init_db, workspace_tx).


##### `grants`  (lines 643–655)

```
def grants() -> None
```

**Purpose**: Lists OAuth account grants connected to agents. OAuth is the common web flow where a user authorizes an app to access an account without sharing a password.

**Data flow**: It loads config, reads grant summaries through `_read_grants`, prints `no grants` if none exist, otherwise prints agent, provider, account, sharing mode, and grant date.

**Call relations**: Users call this to inspect accounts connected through chat flows such as `connect_account`. It relies on `_read_grants` for database and grant lookup work.

*Call graph*: calls 1 internal fn (_read_grants); 3 external calls (run, echo, load_config).


##### `_read_grants`  (lines 658–665)

```
async def _read_grants(config: Config) -> tuple[GrantSummary, ...]
```

**Purpose**: Reads summarized account-grant information for the current workspace. It keeps the CLI from needing to know the details of how grants are stored.

**Data flow**: It receives config, opens the database layer, finds the workspace id, calls `grant_summaries` for that workspace, returns the summaries, and closes the database layer.

**Call relations**: `grants` calls this before printing. It bridges the command-line view to the grants subsystem.

*Call graph*: called by 1 (grants); 5 external calls (select, dispose_db, init_db, workspace_tx, grant_summaries).


##### `credential`  (lines 669–671)

```
def credential() -> None
```

**Purpose**: Defines the `credential` command group for extension-owned secret slots. These are bring-your-own-key values, such as API keys, stored encrypted at rest.

**Data flow**: It does not process values itself. It groups subcommands for setting and listing declared credential slots.

**Call relations**: Click uses this as the parent for `credential_set` and `credential_list`. It gives users one command area for secret slot operations.


##### `credential_set`  (lines 676–694)

```
def credential_set(slot: str) -> None
```

**Purpose**: Stores a secret value for one declared credential slot. It avoids unsafe input paths by never accepting the secret as a command-line argument.

**Data flow**: It receives a slot name, loads config, checks that installed extension manifests declare that slot, checks that the encryption key environment variable is set, reads the value from a hidden prompt or standard input, rejects an empty value, writes it through `_write_credential`, and prints confirmation.

**Call relations**: Users call this under `ufoctl credential set`. It uses `_declared_slots` to validate the slot and `_write_credential` to encrypt and store the value.

*Call graph*: calls 2 internal fn (_declared_slots, _write_credential); 5 external calls (run, ClickException, echo, prompt, load_config).


##### `credential_list`  (lines 698–708)

```
def credential_list() -> None
```

**Purpose**: Shows which declared credential slots exist and whether each has a stored value. It never reads or prints the secret values themselves.

**Data flow**: It loads config, reads declared slots from extension manifests, prints a no-slots message if there are none, reads stored slot names, and prints each declared slot with its owning extension and set/unset status.

**Call relations**: Users call this under `ufoctl credential list`. It combines manifest data from `_declared_slots` with database state from `_read_stored_slots`.

*Call graph*: calls 2 internal fn (_declared_slots, _read_stored_slots); 3 external calls (run, echo, load_config).


##### `_declared_slots`  (lines 711–716)

```
def _declared_slots(config: Config) -> dict[str, str]
```

**Purpose**: Builds the list of credential slots declared by installed extensions. This tells the CLI which secret names are valid.

**Data flow**: It receives config, loads extension manifests for the configured pack, and returns a dictionary mapping each slot name to the extension that declared it. If manifest loading fails, it turns the failure into a CLI-friendly error.

**Call relations**: `credential_set` uses this to reject unknown slots, and `credential_list` uses it to show all possible slots. It connects credential commands to the extension manifest system.

*Call graph*: called by 2 (credential_list, credential_set); 2 external calls (ClickException, load_manifests).


##### `_write_credential`  (lines 719–726)

```
async def _write_credential(config: Config, key: str, slot: str, value: str) -> None
```

**Purpose**: Encrypts and stores one credential value for the current workspace. Encryption at rest means the database stores sealed text, not the raw secret.

**Data flow**: It receives config, encryption key, slot name, and value, opens the database layer, finds the workspace id, creates a `CredentialStore` with the key, stores the value, and closes the database layer.

**Call relations**: `credential_set` calls this after reading and validating user input. It hands the actual secure storage to `CredentialStore`.

*Call graph*: called by 1 (credential_set); 6 external calls (__init__, Fernet, select, dispose_db, init_db, workspace_tx).


##### `_read_stored_slots`  (lines 729–743)

```
async def _read_stored_slots(config: Config) -> frozenset[str]
```

**Purpose**: Reads which credential slots currently have stored values for the workspace. It only returns slot names, never secret contents.

**Data flow**: It receives config, opens the database layer, finds the workspace id, selects credential slot names for that workspace, returns them as an immutable set, and closes the database layer.

**Call relations**: `credential_list` calls this to decide whether each declared slot is set or unset. It intentionally avoids touching encrypted values.

*Call graph*: called by 1 (credential_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `ext`  (lines 747–748)

```
def ext() -> None
```

**Purpose**: Defines the `ext` command group for searching, installing, and removing extensions. Extensions add optional capabilities to a UFO deployment.

**Data flow**: It does not process extension data itself. It groups extension store subcommands under one CLI namespace.

**Call relations**: Click uses this as the parent for `ext_search`, `ext_install`, and `ext_remove`. Those commands use `_store` to talk to the configured extension catalog and lockfile.


##### `_store`  (lines 751–754)

```
def _store(config: Config) -> ExtensionStore
```

**Purpose**: Creates an `ExtensionStore` connected to the configured catalog and local lockfile. The catalog says what is available; the lockfile says what this deploy has pinned.

**Data flow**: It receives config, checks that an extension store is configured, reads the catalog, finds the lockfile path, and returns an `ExtensionStore`. If no store is configured, it raises a clear CLI error.

**Call relations**: All extension subcommands call this before searching or changing installs. It centralizes the setup needed to use the extension store.

*Call graph*: called by 3 (ext_install, ext_remove, ext_search); 4 external calls (__init__, ClickException, lockfile_path, read_catalog).


##### `ext_search`  (lines 759–772)

```
def ext_search(query: str) -> None
```

**Purpose**: Searches the configured extension store and prints matching extensions. It marks whether each result is installed, available, or bundle-only.

**Data flow**: It receives a query string, loads config, builds the store through `_store`, asks for matches, and prints either a no-match message or one formatted line per listing.

**Call relations**: Users call this under `ufoctl ext search`. It is the read-only extension discovery command and depends on `_store` for catalog access.

*Call graph*: calls 1 internal fn (_store); 2 external calls (echo, load_config).


##### `ext_install`  (lines 777–783)

```
def ext_install(name: str) -> None
```

**Purpose**: Pins an extension from the store into the deploy’s lockfile. The next server run can then load that extension.

**Data flow**: It receives an extension name, loads config, builds the store through `_store`, asks it to install the name, catches store errors as CLI errors, and prints the pinned name, version, and digest.

**Call relations**: Users call this under `ufoctl ext install`. It does not load the extension immediately; it updates the lockfile that later `serve`, `migrate`, or `bundle` operations use.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `ext_remove`  (lines 788–794)

```
def ext_remove(name: str) -> None
```

**Purpose**: Removes an extension pin from the deploy’s lockfile. On the next server run, that extension will no longer be loaded.

**Data flow**: It receives an extension name, loads config, builds the store through `_store`, asks it to remove the pin, catches store errors as CLI errors, and prints confirmation.

**Call relations**: Users call this under `ufoctl ext remove`. Like install, it changes the lockfile rather than the currently running server process.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `_ufo_project_dir`  (lines 800–811)

```
def _ufo_project_dir() -> Path
```

**Purpose**: Finds the source project directory for UFO so the bundle command can build a wheel package from the right place. A wheel is a Python package file used for installation.

**Data flow**: It walks upward from this file, looks for a `pyproject.toml` whose project name is `ufo`, returns that directory if found, and raises a CLI error if no source project is available.

**Call relations**: `bundle` calls this before running `uv build`. It makes bundling independent of the user’s current working directory, but fails clearly in wheel-only installs that lack source files.

*Call graph*: called by 1 (bundle); 3 external calls (ClickException, Path, loads).


##### `bundle`  (lines 818–834)

```
def bundle(out: Path) -> None
```

**Purpose**: Builds a runnable deployment bundle containing the pinned config, extension lock information, image recipe, and the UFO wheel. This freezes the current deploy into an artifact.

**Data flow**: It receives an output directory, loads config, optionally reads the extension catalog, builds bundle files through `Bundle`, runs `uv build` to create a wheel from the source project, checks that the wheel exists, then prints the bundle location and pinned extensions.

**Call relations**: Users call this as `ufoctl bundle` when preparing a deployable artifact. It coordinates config, extension pins, bundle-building logic, source discovery through `_ufo_project_dir`, and the external `uv` build tool.

*Call graph*: calls 1 internal fn (_ufo_project_dir); 8 external calls (__init__, ClickException, echo, run, wheel_name, config_path, load_config, read_catalog).


### `core/src/ufo/bundle.py`

`orchestration` · `bundle creation`

This file solves a packaging problem: a running UFO setup may depend on extensions and local configuration, and a future machine should not have to guess what to install. The bundle is like packing a lunchbox before a trip: it copies the needed recipe, the exact ingredient list, and instructions for how to start eating it.

The main class, `Bundle`, creates a Docker build context, which is just a folder Docker can use to build an image. It copies the current `ufo.toml` configuration into that folder. It also writes a `ufo.lock` lockfile, which records the exact extensions that should be active and verifies their installed digests. A digest is a fingerprint of installed content, used to make sure the extension is exactly the expected one.

To decide what goes into the lockfile, the bundle starts from the existing lockfile if there is one. If not, it uses every extension currently discovered in the environment. If an extension catalog is available, it also includes entries marked as disabled in the store, because those are “bundle-only”: they are installed during bundling rather than later at runtime.

Finally, the file writes a Dockerfile. That Dockerfile starts from a slim Python image, installs the locally built UFO wheel, copies in the frozen config and lockfile, and starts `ufoctl serve` by default.

#### Function details

##### `wheel_name`  (lines 25–28)

```
def wheel_name() -> str
```

**Purpose**: This builds the expected filename of the UFO Python wheel that will be copied into the Docker image. A wheel is a packaged Python distribution, and here it is the local package Docker will install because UFO is not fetched from a public package index.

**Data flow**: It reads the current UFO version from the extension store version helper. It then formats that version into a standard wheel filename such as `ufo-<version>-py3-none-any.whl`, and returns that string.

**Call relations**: When `Bundle._dockerfile` writes the Dockerfile text, it calls `wheel_name` so the Dockerfile knows exactly which local wheel file to copy and install.

*Call graph*: called by 1 (_dockerfile); 1 external calls (ufo_version).


##### `Bundle.build`  (lines 49–62)

```
def build(self) -> BundleResult
```

**Purpose**: This is the main action that creates the bundle folder. It writes the copied configuration, the freshly pinned lockfile, and the Dockerfile, then returns a summary of what it produced.

**Data flow**: It starts with a `Bundle` containing the source config path, optional extension catalog, and output folder. It asks `_pins` for the exact extension pins, creates the output directory, copies the config text into `ufo.toml`, writes a JSON lockfile with the current UFO version and pins, writes the Dockerfile from `_dockerfile`, and returns a `BundleResult` containing the paths and chosen pins.

**Call relations**: This is the coordinator for the bundle operation. It calls `_pins` first to freeze the extension set, then `_dockerfile` to create the container build instructions, and packages the result into `BundleResult` for the caller of the bundle command to inspect or report.

*Call graph*: calls 2 internal fn (_dockerfile, _pins); 3 external calls (__init__, __init__, ufo_version).


##### `Bundle._pins`  (lines 64–78)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: This decides exactly which extensions belong in the bundle and verifies that each one can be pinned from what is installed. The goal is to avoid creating an artifact that depends on missing or uncertain extension code.

**Data flow**: It looks at the extensions currently discovered in the environment and checks whether a lockfile already exists. If a lockfile exists, it starts from the extension names already locked; otherwise it starts from all discovered extension names. If a catalog is present, it adds catalog entries marked disabled, because those are meant to be included only during bundling. It removes duplicates while keeping order, then turns each name into an `ExtensionPin` using `pin_for`, which records the installed extension fingerprint.

**Call relations**: `Bundle.build` calls this before writing the lockfile. This function relies on loader helpers to find installed extensions and read the existing lockfile, then hands each chosen name to the store pinning helper so the final bundle can boot with verified extensions.

*Call graph*: called by 1 (build); 4 external calls (discovered, lockfile_path, read_lockfile, pin_for).


##### `Bundle._dockerfile`  (lines 80–94)

```
def _dockerfile(self) -> str
```

**Purpose**: This creates the text of the Dockerfile used to build the runnable image. The Dockerfile tells Docker how to install UFO, where to find the frozen config and lockfile, and what command to run by default.

**Data flow**: It has no outside input beyond the bundle constants and the current wheel filename. It assembles a short Dockerfile string: start from Python 3.12 slim, work in `/app`, set environment variables pointing UFO at the bundled config and lockfile, copy and install the local wheel, copy the frozen files, and set `ufoctl serve` as the default command.

**Call relations**: `Bundle.build` calls this when it is ready to write the Dockerfile into the output folder. It calls `wheel_name` so the Dockerfile refers to the same wheel filename the bundle command is expected to build beside the Docker context.

*Call graph*: calls 1 internal fn (wheel_name); called by 1 (build).


### Control service administration
Hosted control-service commands start the gateway and run administrator setup and maintenance jobs.

### `control/src/ufo_control/main.py`

`entrypoint` · `startup and operator/admin commands`

This file is the place an operator or deployment system talks to the control service. Think of it like a small control panel with several buttons: one button starts the public gateway, while others prepare the database, create a one-time workspace invite, retry a failed Slack Connect delivery, or install database access rules.

When the command group starts, it sets up normal logging and, if an OpenTelemetry endpoint is configured, also sends logs to the platform log collector. OpenTelemetry is a standard way to send operational signals such as logs and traces to monitoring tools. The code is careful not to send OpenTelemetry's own internal error logs back through the same export pipeline, which avoids a feedback loop if log shipping itself fails.

The `gateway` command starts the FastAPI/ASGI web app through Uvicorn, using a port from the environment or a default. The database-focused commands use the owner database connection string, check or shape the control schema, and then run one focused task. Short synchronous command functions wrap asynchronous database work with `asyncio.run`, so operators can use simple shell commands while the service still uses efficient async database access under the hood.

#### Function details

##### `main`  (lines 33–36)

```
def main() -> None
```

**Purpose**: Defines the top-level command group for operating the hosted shared-workspace service. It also sets up process-wide logging before any subcommand runs.

**Data flow**: It reads the optional log-export endpoint from the environment, sets a standard console log format, and passes that endpoint to the log-export setup. It does not return user data; it prepares the process so later commands have consistent logging.

**Call relations**: This is the command group that Click uses as the outer shell for the other commands in this file. As part of that setup, it calls `_export_logs` so every command can share the same logging behavior.

*Call graph*: calls 1 internal fn (_export_logs); 1 external calls (basicConfig).


##### `_export_logs`  (lines 39–49)

```
def _export_logs(otlp_endpoint: str | None) -> None
```

**Purpose**: Turns on remote log shipping when the deployment provides an OpenTelemetry collector endpoint. If no endpoint is configured, it leaves logs on normal standard output only.

**Data flow**: It receives either a base collector URL or `None`. With `None`, it stops immediately. With a URL, it creates a log provider labeled with the service name, builds an HTTP log exporter pointed at the collector's log path, attaches a batch sender, and then asks `_install_root_handler` to connect normal Python logging to that provider.

**Call relations**: `main` calls this during command setup. When remote logging is enabled, this function builds the OpenTelemetry pieces and hands them to `_install_root_handler`, which attaches them to the root logger used across the process.

*Call graph*: calls 1 internal fn (_install_root_handler); called by 1 (main); 4 external calls (OTLPLogExporter, LoggerProvider, BatchLogRecordProcessor, create).


##### `_install_root_handler`  (lines 52–57)

```
def _install_root_handler(logger_provider: LoggerProvider) -> None
```

**Purpose**: Connects ordinary Python log messages to the OpenTelemetry logging system. It deliberately filters out OpenTelemetry's own logs so a failure in log exporting cannot recursively generate more export attempts.

**Data flow**: It receives a prepared OpenTelemetry logger provider. It creates a logging handler from it, adds a filter that rejects records whose logger name starts with `opentelemetry`, and attaches the handler to Python's root logger. The visible result is that later application logs are also sent through the OpenTelemetry pipeline.

**Call relations**: `_export_logs` calls this after it has created the provider and exporter. From then on, log messages emitted by the gateway or admin commands flow through the added root handler.

*Call graph*: called by 1 (_export_logs); 2 external calls (getLogger, LoggingHandler).


##### `gateway`  (lines 61–66)

```
def gateway() -> None
```

**Purpose**: Starts the hosted gateway web service. This is the command used when the process should serve onboarding, fleet-count information, and the terminal client.

**Data flow**: It reads the desired port from the `UFO_GATEWAY_PORT` environment variable, falling back to the default port if none is set. It then starts Uvicorn, an ASGI web server, pointing it at `ufo_control.gateway:app`; the function does not return until the server stops.

**Call relations**: This is one of the subcommands under `main`. Instead of doing the web work itself, it hands control to Uvicorn, which imports and runs the gateway application.

*Call graph*: 1 external calls (run).


##### `migrate`  (lines 70–73)

```
def migrate() -> None
```

**Purpose**: Brings the control database schema up to the expected shape. Operators use it when deploying or upgrading so the gateway has the tables and records it relies on.

**Data flow**: It asks for the database owner connection string, runs the asynchronous schema-shaping routine inside `asyncio.run`, and then prints a success message. The main change is in the database: its control schema is created or adjusted to the current expected version.

**Call relations**: This command is invoked directly from the CLI under `main`. It delegates the real database work to `shape_control_schema`, using `owner_dsn` to connect with enough privilege to change the schema.

*Call graph*: 4 external calls (run, echo, owner_dsn, shape_control_schema).


##### `invite`  (lines 78–83)

```
def invite(object_number: int) -> None
```

**Purpose**: Creates a one-time invite for a new workspace and prints the email text that contains the code. It is meant for an operator who needs to send a controlled signup invitation.

**Data flow**: It receives an object number from the command line. It runs `_mint_invite` to create the invite and build the email, then prints that email text. If invite creation fails with an invite-specific error, it converts that into a clean command-line error message.

**Call relations**: This command is invoked from the CLI under `main`. It keeps the user-facing error handling and output here, while `_mint_invite` performs the async database and email-content work.

*Call graph*: calls 1 internal fn (_mint_invite); 3 external calls (run, ClickException, echo).


##### `_mint_invite`  (lines 86–96)

```
async def _mint_invite(object_number: int) -> str
```

**Purpose**: Does the actual work of creating a one-time workspace invite and turning it into email text. It exists separately because the command wrapper is synchronous while the database work is asynchronous.

**Data flow**: It receives the requested object number. It reads the public host name and owner database connection string, verifies that the control schema is present, opens a small database connection pool, mints an invite code through `InviteCodes`, closes the pool, and formats the subject and body of the invite email. It returns a single printable string containing the subject and body.

**Call relations**: `invite` calls this when an operator requests a new invite. This function coordinates helpers from the email, invite-code, schema, and database-connection parts of the system, then hands the completed email text back to the command for printing.

*Call graph*: called by 1 (invite); 6 external calls (__init__, create_pool, invite_email, public_apex_host, owner_dsn, require_control_schema).


##### `slack_connect_retry`  (lines 101–107)

```
def slack_connect_retry(onboard_claim_id: uuid.UUID) -> None
```

**Purpose**: Retries, or more precisely re-arms, a Slack Connect delivery that previously failed during signup. Operators use it after fixing the cause of the failed delivery.

**Data flow**: It receives an onboard claim ID from the command line. It runs `_rearm_slack_connect` and gets back either the time the delivery originally failed or `None`. If nothing failed for that claim, it raises a clear command-line error; otherwise it prints a confirmation with the failure time in UTC.

**Call relations**: This is a CLI subcommand under `main`. It keeps the operator-facing message formatting here, while `_rearm_slack_connect` performs the async database lookup and state change.

*Call graph*: calls 1 internal fn (_rearm_slack_connect); 3 external calls (run, ClickException, echo).


##### `_rearm_slack_connect`  (lines 110–117)

```
async def _rearm_slack_connect(onboard_claim_id: uuid.UUID) -> datetime | None
```

**Purpose**: Finds a failed Slack Connect delivery for a signup claim and marks it ready to be tried again. It returns the original failure time so the operator can see what was re-armed.

**Data flow**: It receives a signup claim UUID. It gets the owner database connection string, checks that the control schema exists, opens a small database pool, asks `rearm_failed_delivery` to update the failed delivery if one exists, closes the pool, and returns either the failure timestamp or `None`.

**Call relations**: `slack_connect_retry` calls this after parsing the command-line UUID. This helper delegates the specific Slack-delivery state change to `rearm_failed_delivery`, then gives the result back to the command for user-friendly reporting.

*Call graph*: called by 1 (slack_connect_retry); 4 external calls (create_pool, rearm_failed_delivery, owner_dsn, require_control_schema).


##### `rls_bootstrap`  (lines 121–124)

```
def rls_bootstrap() -> None
```

**Purpose**: Installs or updates the database access-control setup used by the shared service. RLS means row-level security, a database feature that controls which rows a role is allowed to see or change.

**Data flow**: It runs `_bootstrap` inside `asyncio.run`, then prints a success message. The important output is not returned to Python; it is the updated database roles and policies.

**Call relations**: This is a CLI subcommand under `main`. It provides the simple operator command, while `_bootstrap` performs the ordered database bootstrap steps.

*Call graph*: calls 1 internal fn (_bootstrap); 2 external calls (run, echo).


##### `_bootstrap`  (lines 127–130)

```
async def _bootstrap() -> None
```

**Purpose**: Performs the database security bootstrap steps: creating or updating workspace policies and ensuring the serving role exists. The serving role is the database identity the running service can use safely.

**Data flow**: It reads the owner database connection string, then calls the policy bootstrap routine and the serve-role creation/check routine with that connection string. It returns nothing; its effect is to leave the database access-control setup at the expected state.

**Call relations**: `rls_bootstrap` calls this when an operator runs the security bootstrap command. It hands the privileged database connection string to `bootstrap_policies` and `ensure_serve_role`, which do the actual database changes.

*Call graph*: called by 1 (rls_bootstrap); 3 external calls (bootstrap_policies, ensure_serve_role, owner_dsn).


### Sandbox deployment gates
Preflight checks verify that sandbox workspace mounting and proxied HTTPS traffic work before serving users.

### `sandbox/mount_gate.py`

`entrypoint` · `deployment validation`

This script is like a smoke alarm for the sandbox file system. The project expects agents inside an E2B sandbox to use `/workspace` as a normal folder, even though the files are really backed by S3 storage through a FUSE mount. FUSE is a system that lets a program make remote storage look like a local folder. If the template, credentials, proxy endpoint, cloud permissions, or bucket setup is wrong, agents may later fail in confusing ways. This gate catches that before deployment succeeds.

The script creates a throwaway conversation id, uses it as a temporary storage prefix, and writes a marker object first because the mount tool needs the remote folder prefix to exist. It then starts a sandbox from the published template, installs the egress certificate, places a short-lived mount token in the sandbox, runs the same mount preparation commands used in production, and checks that the mount is healthy.

After mounting, it exercises the folder the way an agent would: create a file, list it, read it, change permissions, delete it, and test a tricky case where a file is deleted while still open. That last case matters because packaged versions of `s3fs`, the S3-backed mount tool, have crashed there before. Whether the script succeeds or fails, it tries to kill the sandbox and remove the temporary marker.

#### Function details

##### `_mount_gate_recipe`  (lines 85–124)

```
def _mount_gate_recipe(*, bucket: str, region: str, proxy_url: str, ca_cert: str | None, token_secret: str | None, conversation: UUID, now: datetime) -> _MountGateRecipe
```

**Purpose**: Builds the set of ingredients needed to mount `/workspace` inside the sandbox. It checks that required secrets are present, creates a short-lived access token, and assembles the shell commands that will prepare and verify the mount.

**Data flow**: It receives the S3 bucket and region, the proxy URL, the certificate text, the token-signing secret, a temporary conversation id, and the current time. It rejects missing certificate or token secret values, then signs a temporary gate token, builds the S3 mount command for that conversation’s storage prefix, builds the credential endpoint URL, and packages all of this into a `_MountGateRecipe`. The result contains the certificate, the token, the token-staging command, and the root commands that must run inside the sandbox.

**Call relations**: The main deployment check calls this before starting the sandbox so it knows exactly what to install and run. This function delegates token creation to the sandbox file-credential code and delegates mount command construction to the sandbox mount code, so the gate uses the same path as the real service instead of inventing a separate test-only setup.

*Call graph*: called by 1 (main); 10 external calls (__init__, timedelta, rstrip, issue_sandbox_fs_gate_token, workspace_key_prefix, install_token_command, mount_health_check, mount_scripts, prepare_token_staging_command, s3fs_command).


##### `main`  (lines 127–186)

```
def main() -> None
```

**Purpose**: Runs the mount gate from the command line. It creates a real sandbox, prepares the remote workspace prefix, mounts it, tests file operations, and exits with an error if any step fails.

**Data flow**: It reads command-line values for bucket, region, and proxy URL, and reads required secrets from environment variables. It creates a random conversation id, asks `_mount_gate_recipe` for the certificate, token, and commands, creates an S3 store helper, writes the workspace marker object, starts an E2B sandbox, installs the certificate, creates `/workspace`, stages the token, runs the mount commands, and finally runs the file-operation exercise script. On success it prints the exercise output, kills the sandbox, deletes the marker, and prints a pass message. On failure it still tries to clean up, but preserves the original failure so the deployment pipeline reports the real problem.

**Call relations**: This is the top-level entrypoint when the script is run as `mount-gate`. It calls `_mount_gate_recipe` to prepare the plan, uses `ensure_workspace_marker` before mounting because the S3-backed folder must already exist, uses `Sandbox.create` to launch the real environment, and uses sandbox file and command APIs to perform the same steps an operator or service would perform during a real mount.

*Call graph*: calls 1 internal fn (_mount_gate_recipe); 7 external calls (__init__, ArgumentParser, run, now, create, ensure_workspace_marker, uuid4).


### `sandbox/proxy_gate.py`

`entrypoint` · `deploy validation`

This script acts like a gate at deployment time: it checks that the off-cluster sandbox can use the project’s TLS egress proxy before the system relies on it. Without this check, a sandbox might start running jobs while its outgoing secure web traffic is broken, misconfigured, or unable to trust the proxy certificate.

The script receives a public proxy URL and reads a certificate from an environment variable. It creates a fresh E2B sandbox, writes that certificate into the sandbox, and runs the certificate installation command as root. Then it repeatedly runs a small curl test inside the sandbox. Curl is a command-line tool for making web requests. Here it tries to reach Anthropic’s API through the proxy using an intentionally invalid run token. A successful gate is not a normal API success; it expects HTTP CONNECT status 403, meaning the proxy is reachable and is rejecting the fake token in the expected way.

While the proxy is still coming up, curl may report temporary connection failures or timeouts. The script treats a small set of those as “not ready yet,” waits, and retries until a deadline. If the response is malformed, unexpected, or never becomes ready, it raises an error. Whether the test passes or fails, it kills the temporary sandbox at the end so the check does not leave resources behind.

#### Function details

##### `ProxyTlsGate.run`  (lines 45–109)

```
def run(self) -> None
```

**Purpose**: Runs the actual proxy readiness test. It verifies that the given proxy URL is an HTTPS URL, prepares a temporary sandbox with the needed certificate, then probes the proxy until it either gets the expected rejection response or fails with a clear error.

**Data flow**: It starts with the gate’s stored public proxy URL and certificate text. It parses the URL, builds a curl command that uses an intentionally invalid token, creates a sandbox, writes and installs the certificate there, and runs the probe command inside that sandbox. The output from curl is turned into a proxy CONNECT status and an exit code. If the status is the expected 403, the function prints a pass message and returns. If the status is still pending, it waits and retries until the timeout. If anything is malformed or unexpected, it raises an error. In all cases, it kills the sandbox before leaving.

**Call relations**: This is the workhorse called after the command-line setup has created a ProxyTlsGate instance. Inside the flow, it uses URL parsing to reject bad proxy addresses, shell quoting to build a safe curl command, E2B sandbox creation to run the check in the same kind of environment that real jobs use, and monotonic time plus sleep to retry without waiting forever.

*Call graph*: 5 external calls (create, join, monotonic, sleep, urlsplit).


##### `main`  (lines 112–119)

```
def main() -> None
```

**Purpose**: Provides the command-line entry point for the proxy gate. It collects the proxy URL argument, reads the required certificate from the environment, and starts the gate check.

**Data flow**: It reads command-line arguments using an argument parser and expects a required --proxy-url value. It then reads the certificate text from the configured environment variable. If the certificate is missing, it raises an error immediately. Otherwise, it creates a ProxyTlsGate with the proxy URL and certificate and runs it.

**Call relations**: This is the outer wrapper that runs when the script is executed directly. It performs only the setup needed to call ProxyTlsGate.run: parse input, fetch the certificate, construct the gate object, and hand control to the gate’s run method.

*Call graph*: 2 external calls (__init__, ArgumentParser).

## 📊 State Registers Touched

- `reg-config` — The effective deployment settings that tell the system how to start, what services to use, and what safety rules are enabled.
- `reg-extension-set` — The saved and loaded set of extensions, packs, manifests, and contributed capabilities available to the runtime.
- `reg-onboarding-state` — The invite codes, email claim codes, onboarding records, and first-workspace setup state for new hosted users.
- `reg-db-schema-version` — The applied core, control-plane, and extension migration revisions that determine which persisted schema the runtime may safely use.
- `reg-outbound-delivery-queue` — The durable pending, sent, failed, and retry state for replies or notifications that must be delivered back to external surfaces such as Slack.
- `reg-slack-connect-provisioning` — Durable Slack Connect customer-channel and invitation provisioning state, including retry/idempotency progress for admin background jobs.
- `reg-update-check-cache` — Cached version/update-check metadata used by CLI or startup paths to avoid repeated remote checks and notify operators about newer releases.
- `reg-extension-catalog-cache` — The searchable catalog metadata for available extensions or packs, distinct from the installed extension lockfile and loaded capability set.
