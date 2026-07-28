# Main server startup, configuration, pack loading, and extension assembly  `stage-3`

This stage is the system’s startup workshop. It begins when a person runs ufoctl in cli.py or starts the server through serve.py. The command-line tool is the control panel for setup, inspection, packaging, and talking to a workspace. The server entry point then builds the running service: it reads settings, connects storage, loads add-ons, sets up web routes, starts background workers, and applies workspace safety rules. config.py supplies the rulebook by loading one TOML settings file and stopping early if required settings are missing or wrong.

The rest of the stage decides what abilities the service will have. Pack discovery chooses a pack, which is a recipe for a particular assistant setup. Extension discovery finds and pins add-ons so the same chosen features can be loaded again later. Skill loading prepares built-in and user-created skill folders for safe use. The many extension manifests act like plug-in instruction cards. They register web pages, Slack, live hubs, browser and coding helpers, document and research skills, connectors, content sources, scheduled tasks, credentials, and jobs. Together they turn a plain server process into a configured UFO assistant.

## Sub-stages

- [Pack and extension discovery](stage-3.1.md) `stage-3.1` — 21 files
- [Skill and user-authored skill loading](stage-3.2.md) `stage-3.2` — 5 files
- [Core extension discovery and pinning](stage-3.3.md) `stage-3.3` — 2 files
- [Web, shell, communication, and live-surface extension manifests](stage-3.4.md) `stage-3.4` — 5 files
- [Agent, skill, document, research, and creation extension manifests](stage-3.5.md) `stage-3.5` — 6 files
- [Connector, source, automation, and scheduled-job extension manifests](stage-3.6.md) `stage-3.6` — 7 files

## Files in this stage

### Server Startup
Command-line control, deployment configuration, and service bootstrap form the main startup path for the UFO server.

### `core/src/ufo/cli.py`

`entrypoint` · `command invocation, startup, operations, interactive chat`

This file turns many internal UFO services into simple terminal commands. Without it, a user would have to create config files, prepare databases, mint tokens, store secrets, run servers, inspect spending, and install extensions by calling lower-level code by hand.

The file is built around Click, a Python library for command-line tools. The top-level `main` command loads a nearby `.env` file first, so local secrets written during setup are available automatically. The `init` command creates a starter `ufo.toml`, writes development secrets, prepares the database, onboards the first workspace owner and default agent, then saves a long-lived CLI token on the machine.

Other commands start the server or proxy, run migrations, chat with the agent, view grants, manage encrypted extension credentials, inspect and set spend caps, report spending, search/install/remove extensions, and build a deployable bundle.

The chat path is the most interactive part. It sends a message to the running server, reads back a stream of simple directives such as text, status, notes, polling requests, and secret prompts, then renders them cleanly in the terminal. Think of it like a radio operator: it sends one message, listens for coded replies, updates the display, and reconnects if the server asks it to wait.

#### Function details

##### `_ufoctl_dir`  (lines 63–65)

```
def _ufoctl_dir() -> Path
```

**Purpose**: Finds the private directory where this CLI stores local state, such as the saved login token and current chat session. A user can override it with an environment variable, which is useful for tests or separate setups.

**Data flow**: It reads the `UFOCTL_DIR` environment variable. If it is set, that value becomes the directory path; otherwise it uses `.ufoctl` inside the user's home directory. It returns that path without creating it.

**Call relations**: Setup uses it when saving the CLI token, chat uses it when reading that token, and session tracking uses it when remembering which conversation to continue.

*Call graph*: called by 3 (_session, chat, init); 2 external calls (Path, home).


##### `_dotenv_path`  (lines 68–69)

```
def _dotenv_path() -> Path
```

**Purpose**: Chooses the `.env` file that lives beside the main UFO config file. This is where local secrets can be stored for easy development use.

**Data flow**: It asks the config system where `ufo.toml` is, takes that file's parent directory, and returns the path to `.env` in the same folder.

**Call relations**: The environment loader reads from this path, setup writes newly minted development secrets to it, and `init` mentions it in user-facing messages.

*Call graph*: called by 3 (_load_dotenv, _write_dev_secrets, init); 1 external calls (config_path).


##### `_dotenv_pairs`  (lines 72–88)

```
def _dotenv_pairs(text: str) -> list[tuple[str, str]]
```

**Purpose**: Parses a small, simple `.env` format into key-value pairs. It supports the common cases this tool writes and reads, without trying to be a full shell parser.

**Data flow**: It receives text, skips blank lines and comments, splits lines shaped like `KEY=VALUE`, removes an optional leading `export`, strips matching quotes around values, and returns a list of names and values.

**Call relations**: The startup environment loader uses it to import secrets, and the development-secret writer uses it to avoid overwriting names already present in `.env`.

*Call graph*: called by 2 (_load_dotenv, _write_dev_secrets).


##### `_load_dotenv`  (lines 91–100)

```
def _load_dotenv() -> None
```

**Purpose**: Loads secrets from the local `.env` file into the process environment before commands run. Already exported environment variables win, so explicit user choices are not replaced.

**Data flow**: It finds the `.env` path, stops if the file is absent, parses the file into pairs, and fills only missing environment variables.

**Call relations**: The top-level CLI command calls this first, so every subcommand sees the same local secrets without the user manually running `export`.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (main).


##### `main`  (lines 104–106)

```
def main() -> None
```

**Purpose**: Defines the root `ufoctl` command. It also performs the shared startup step of loading local environment variables.

**Data flow**: When any CLI command begins, it calls the dotenv loader. It does not return data; it prepares the process environment for the selected subcommand.

**Call relations**: All commands in this file hang under this Click command group, so this is the entry gate for setup, serving, chat, extensions, spending, credentials, and bundling.

*Call graph*: calls 1 internal fn (_load_dotenv).


##### `init`  (lines 112–140)

```
def init(email: str, model: str) -> None
```

**Purpose**: Bootstraps a new UFO workspace for local use. It creates default config if needed, prepares the database, onboards the owner and default agent, and saves a CLI token.

**Data flow**: It receives an owner email and model name. It writes `ufo.toml` if missing, writes needed development secrets, creates a PostgreSQL system database when required, runs migrations, onboards the workspace, mints a bearer token, saves it in the CLI state directory, and prints what was created.

**Call relations**: This command coordinates many helpers: path helpers for files, secret writing, database creation, onboarding, migrations, and token minting. Later commands such as `chat` rely on the token and database state it creates.

*Call graph*: calls 5 internal fn (_create_postgres_system_database, _dotenv_path, _onboard, _ufoctl_dir, _write_dev_secrets); 7 external calls (run, ClickException, echo, mint_token, config_path, load_config, apply_migrations).


##### `_write_dev_secrets`  (lines 143–165)

```
def _write_dev_secrets(config: Config) -> tuple[str, ...]
```

**Purpose**: Creates local development secrets needed by the server and credential store. It avoids overwriting anything the user already set.

**Data flow**: It reads the config to learn which environment variable names are expected, generates new random secret values, checks both `.env` and the current environment for existing names, appends only missing ones to `.env`, also places them into the current process, and returns the names it added.

**Call relations**: `init` calls this so a fresh local install can run `serve` without extra manual secret setup. It uses the dotenv parsing helper to safely detect what already exists.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 2 external calls (generate_key, token_urlsafe).


##### `_onboard`  (lines 168–187)

```
async def _onboard(config: Config, email: str, model: str) -> Onboarded
```

**Purpose**: Creates the first workspace records and runs extension onboarding steps. This is where setup moves from files and secrets into actual database state.

**Data flow**: It opens the database connection layer, optionally builds an encrypted credential store if the credential key is available, loads extension manifests, constructs an onboarding object, creates the core workspace/owner/agent records, runs extension setup steps, returns the onboarding result, and closes the database layer.

**Call relations**: `init` calls this after migrations are applied. It delegates the actual onboarding rules to the `Onboarding` subsystem while making sure the database is opened and closed correctly.

*Call graph*: called by 1 (init); 6 external calls (__init__, __init__, Fernet, dispose_db, init_db, load_manifests).


##### `_create_postgres_system_database`  (lines 190–201)

```
async def _create_postgres_system_database(config: Config) -> None
```

**Purpose**: Ensures the separate PostgreSQL system database exists before migrations run. This supports deployments where the app database URL points at PostgreSQL.

**Data flow**: It converts the configured async database URL into a form `asyncpg` can connect to, extracts the target system database name, connects to PostgreSQL, checks whether that database exists, creates it if missing, and closes the connection.

**Call relations**: `init` calls this only for PostgreSQL configurations. It prepares the ground so the later migration step has a database to work with.

*Call graph*: called by 1 (init); 1 external calls (connect).


##### `migrate`  (lines 205–222)

```
def migrate() -> None
```

**Purpose**: Brings the database schema up to date. A schema is the set of tables and columns the application expects.

**Data flow**: It loads config, optionally replaces the configured database URL with an owner database URL from the environment, runs core and extension migrations for the configured pack, and prints success.

**Call relations**: Users run this after installing table-owning extensions or during deployment. It hands off the actual schema work to the database migration code.

*Call graph*: 3 external calls (echo, load_config, apply_migrations).


##### `serve`  (lines 226–228)

```
def serve() -> None
```

**Purpose**: Starts the main UFO runtime service. This includes the surfaces, workers, and jobs that make the system operate.

**Data flow**: It takes no command-specific input and simply calls the server runner. Any serving configuration is read by that lower-level server code.

**Call relations**: This command is the CLI doorway into the runtime. After `init` and `migrate`, a user runs it to make chat and other surfaces available.

*Call graph*: 1 external calls (run).


##### `proxy`  (lines 232–234)

```
def proxy() -> None
```

**Purpose**: Starts the shared egress proxy, which fronts outbound traffic for workspace sandboxes. A proxy is a controlled network middleman.

**Data flow**: It takes no command-specific input and calls the proxy runner, which reads its own configuration and starts serving.

**Call relations**: This is a separate operational entrypoint from `serve`, used when a deployment runs the proxy as its own service.

*Call graph*: 1 external calls (run).


##### `chat`  (lines 240–258)

```
def chat(message: str | None, new: bool) -> None
```

**Purpose**: Lets a user talk to the default UFO chat surface from the terminal. It can send one message or enter an interactive prompt loop.

**Data flow**: It loads config, reads the saved CLI token, chooses or creates a conversation session, and either sends the provided message or repeatedly reads lines from standard input. Each non-empty message is passed to the turn runner.

**Call relations**: It depends on `init` having saved a token. It uses `_session` to keep conversations continuous and `_run_turn` to perform each network exchange with the running server.

*Call graph*: calls 3 internal fn (_run_turn, _session, _ufoctl_dir); 3 external calls (ClickException, echo, load_config).


##### `_session`  (lines 261–267)

```
def _session(new: bool) -> str
```

**Purpose**: Gets the chat session id that lets conversations continue across CLI runs. It creates a new one when requested or when none exists.

**Data flow**: It looks in the CLI state directory for a `session` file. If `--new` was requested or the file is missing, it writes a fresh random id; then it reads and returns the id.

**Call relations**: The chat command calls this before sending messages, so repeated `ufoctl chat` calls can continue the same conversation unless the user asks for a new one.

*Call graph*: calls 1 internal fn (_ufoctl_dir); called by 1 (chat); 1 external calls (uuid4).


##### `_run_turn`  (lines 270–284)

```
def _run_turn(config: Config, token: str, channel: str, message: str) -> None
```

**Purpose**: Runs one chat turn and turns connection problems into friendly CLI errors. A turn is one user message plus the agent's response flow.

**Data flow**: It builds the server base URL from config, then runs the asynchronous streaming function with the token, channel, and message. If the user interrupts, it prints that the server may continue in the background; if HTTP fails, it raises a clear CLI exception.

**Call relations**: The chat loop calls this for every message. It bridges the synchronous command-line world and the asynchronous network streaming code.

*Call graph*: calls 1 internal fn (_stream_turn); called by 1 (chat); 3 external calls (run, ClickException, echo).


##### `_stream_turn`  (lines 296–304)

```
async def _stream_turn(base: str, token: str, channel: str, message: str) -> None
```

**Purpose**: Opens the HTTP client and hands one chat turn to the chat-stream driver. HTTP is the web protocol used to talk to the running server.

**Data flow**: It receives server base URL, bearer token, channel id, and message. It creates a terminal display object, builds request headers, opens an async HTTP client, and asks `_ChatStream` to run the turn.

**Call relations**: _run_turn calls this inside `asyncio.run`. It prepares the network and display objects that `_ChatStream` needs.

*Call graph*: called by 1 (_run_turn); 3 external calls (__init__, __init__, AsyncClient).


##### `_ChatStream.run`  (lines 318–329)

```
async def run(self, message: str) -> None
```

**Purpose**: Drives one full chat turn until the server says it is done. It also handles server requests to wait, reconnect, or collect credentials.

**Data flow**: It starts with the user's message as the request body. It drains a server stream; if the server asks for polling, it waits and reconnects with an empty body; when the stream is complete, it closes the display and fulfills any secret prompts collected during the turn.

**Call relations**: _stream_turn creates `_ChatStream` and calls this. This method repeatedly calls `_drain` and, when needed, `_fulfill_secret`.

*Call graph*: calls 2 internal fn (_drain, _fulfill_secret); 1 external calls (sleep).


##### `_ChatStream._drain`  (lines 331–371)

```
async def _drain(self, body: str) -> _Pending
```

**Purpose**: Reads one streamed server response and turns its simple directives into terminal output or pending follow-up work. A directive is a line-based command from the server, such as “print this text” or “poll again later.”

**Data flow**: It posts the current body to the chat path, checks for HTTP success, reads response lines, unescapes tab-separated fields, updates the display for text, lines, notes, and status meters, records secret prompts, records poll timing, and returns a `_Pending` object describing what should happen next.

**Call relations**: `_ChatStream.run` calls this each time it contacts the server. It uses `_unescape` to decode fields and gives display work to `_TurnDisplay`.

*Call graph*: calls 1 internal fn (_unescape); called by 1 (run); 2 external calls (__init__, ClickException).


##### `_ChatStream._fulfill_secret`  (lines 373–390)

```
async def _fulfill_secret(self, sealed: str, slot: str, prompt: str) -> None
```

**Purpose**: Privately collects a requested credential value and sends it to the server outside the chat transcript. This prevents secrets from becoming normal conversation text.

**Data flow**: It receives the sealed prompt marker, slot name, and prompt text. It asks the user for hidden input, posts that value with special headers, checks the response, and prints any acknowledgement line the server returns.

**Call relations**: `_ChatStream.run` calls this after the main turn ends if `_drain` collected secret prompts. It decodes server acknowledgements with `_unescape` and prints them through the display.

*Call graph*: calls 1 internal fn (_unescape); called by 1 (run); 2 external calls (ClickException, prompt).


##### `_unescape`  (lines 396–408)

```
def _unescape(text: str) -> str
```

**Purpose**: Decodes the small escape format used in chat directives. This lets tabs, newlines, and backslashes travel safely inside tab-separated lines.

**Data flow**: It receives a text field, walks through it character by character, replaces `\t`, `\n`, and `\\` style escape sequences with their real characters, and returns the decoded string.

**Call relations**: The chat stream reader and secret fulfilment path use this whenever they parse server directive lines.

*Call graph*: called by 2 (_drain, _fulfill_secret).


##### `_TurnDisplay.text`  (lines 425–429)

```
def text(self, delta: str) -> None
```

**Purpose**: Prints streamed text from the agent without forcing a newline. This is used for response text that arrives in pieces.

**Data flow**: It first erases any temporary status meter, writes the text delta to standard output without adding a newline, and records whether the current output line is still open.

**Call relations**: _ChatStream._drain calls this for `txt` directives. Its line bookkeeping helps later notes, meters, and final lines avoid overwriting streamed text.

*Call graph*: calls 1 internal fn (_erase_meter); 1 external calls (echo).


##### `_TurnDisplay.line`  (lines 431–435)

```
def line(self, text: str) -> None
```

**Purpose**: Prints a complete line from the server, such as a non-streamed answer, link, error, or credential acknowledgement.

**Data flow**: It closes any half-written line first, then writes the given text as a full line to standard output.

**Call relations**: _ChatStream._drain calls this for `say` directives, and `_ChatStream._fulfill_secret` uses it for acknowledgement messages.

*Call graph*: calls 1 internal fn (_close_line); 1 external calls (echo).


##### `_TurnDisplay.activity`  (lines 437–441)

```
def activity(self, note: str) -> None
```

**Purpose**: Prints a mid-turn activity note, such as a tool call or skill load, in dim text. It keeps these notes separate from the streamed answer.

**Data flow**: It closes any current streamed line, styles the note as dim, and writes it as its own line.

**Call relations**: _ChatStream._drain calls this for `note` directives. It relies on `_close_line` so activity messages do not corrupt streamed response text.

*Call graph*: calls 1 internal fn (_close_line); 2 external calls (echo, style).


##### `_TurnDisplay.meter`  (lines 443–452)

```
def meter(self, text: str) -> None
```

**Purpose**: Shows a temporary status message on terminals that support it. This is like a progress sign that can be erased when real output arrives.

**Data flow**: If output is not going to a real terminal, it does nothing. Otherwise it moves to a safe line if needed, writes an erasable dim status message to standard error, and records that a meter is visible.

**Call relations**: _ChatStream._drain calls this for `status` directives. Later text, line, activity, or close operations erase the meter before printing.

*Call graph*: 2 external calls (echo, style).


##### `_TurnDisplay.close`  (lines 454–455)

```
def close(self) -> None
```

**Purpose**: Finishes the display cleanly at the end of a chat turn. It makes sure no temporary meter or half-open line remains.

**Data flow**: It calls the line-closing helper, which erases the meter and adds a newline if streamed text left the cursor mid-line.

**Call relations**: _ChatStream.run calls this when the server has finished the turn and before any credential prompts are fulfilled.

*Call graph*: calls 1 internal fn (_close_line).


##### `_TurnDisplay._erase_meter`  (lines 457–461)

```
def _erase_meter(self) -> None
```

**Purpose**: Removes the temporary status meter from the terminal if one is currently shown.

**Data flow**: It checks the meter flag. If a meter is visible, it writes the terminal erase sequence to standard error and clears the flag.

**Call relations**: The text and line-closing paths call this before printing permanent output, so status text never overwrites the agent's answer.

*Call graph*: called by 2 (_close_line, text); 1 external calls (echo).


##### `_TurnDisplay._close_line`  (lines 463–467)

```
def _close_line(self) -> None
```

**Purpose**: Ensures the terminal is ready for a new full line. It clears temporary status text and closes any partial streamed line.

**Data flow**: It erases the meter first. If streamed text has left a line open, it writes a newline and marks the line closed.

**Call relations**: Line, activity, and final close operations use this shared helper to keep terminal output tidy.

*Call graph*: calls 1 internal fn (_erase_meter); called by 3 (activity, close, line); 1 external calls (echo).


##### `spend_cap`  (lines 471–472)

```
def spend_cap() -> None
```

**Purpose**: Defines the `spend-cap` command group for reading and changing spending limits. Spend caps are limits that control whether turns are admitted or parked when costs rise too high.

**Data flow**: It does not process data itself; it groups subcommands under one CLI name.

**Call relations**: The `set` and `list` spend-cap commands live under this group and provide the actual behavior.


##### `spend_cap_set`  (lines 483–501)

```
def spend_cap_set(scope: str, subject_id: str, window_seconds: int, limit_micro_usd: int, on_breach: str) -> None
```

**Purpose**: Creates or updates a spending cap for the workspace, a member, or an agent. It protects users from unexpected cost growth.

**Data flow**: It receives scope, optional subject id, time window, micro-dollar limit, and breach behavior. It validates which subject ids are allowed, loads config, writes the cap to the database, converts micro-dollars to dollars for display, and prints the result.

**Call relations**: This subcommand calls `_write_spend_cap` for the database work. Its validation prevents impossible cap shapes before anything is stored.

*Call graph*: calls 1 internal fn (_write_spend_cap); 5 external calls (run, ClickException, echo, load_config, UUID).


##### `spend_cap_list`  (lines 505–515)

```
def spend_cap_list() -> None
```

**Purpose**: Shows the spending caps currently set for the workspace. This lets operators see what limits are active.

**Data flow**: It loads config, reads cap rows from the database, prints a no-caps message if none exist, or formats each cap with scope, subject, dollar limit, time window, and breach behavior.

**Call relations**: This subcommand delegates database reading to `_read_spend_caps` and only handles user-facing formatting.

*Call graph*: calls 1 internal fn (_read_spend_caps); 3 external calls (run, echo, load_config).


##### `_write_spend_cap`  (lines 518–572)

```
async def _write_spend_cap(config: Config, scope: str, subject: UUID | None, window_seconds: int, limit_micro_usd: int, on_breach: str) -> UUID
```

**Purpose**: Stores a spend cap in the database, updating an existing matching cap instead of creating a duplicate.

**Data flow**: It opens the database layer, finds the workspace id, searches for a cap with the same workspace, scope, subject, and window. If found, it updates the limit and breach behavior; otherwise it inserts a new cap with a fresh id. It returns the cap id and closes the database layer.

**Call relations**: `spend_cap_set` calls this after validating command-line arguments. The rest of the system can later enforce the saved caps during turn admission and model rounds.

*Call graph*: called by 1 (spend_cap_set); 7 external calls (insert, select, update, dispose_db, init_db, workspace_tx, uuid4).


##### `_read_spend_caps`  (lines 575–601)

```
async def _read_spend_caps(config: Config) -> list[tuple[UUID, str, UUID | None, int, int, str]]
```

**Purpose**: Reads all spend caps for the current workspace from the database.

**Data flow**: It opens the database layer, finds the workspace id, selects cap id, scope, subject, window, limit, and breach behavior, converts rows into simple tuples, returns them, and closes the database layer.

**Call relations**: `spend_cap_list` calls this and then formats the returned rows for the terminal.

*Call graph*: called by 1 (spend_cap_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `spend`  (lines 609–629)

```
def spend(window_seconds: int) -> None
```

**Purpose**: Prints a spending report for a recent time window. It summarizes total cost and breaks it down by useful categories.

**Data flow**: It receives a window length in seconds, loads config, reads the spend report, converts micro-dollars to dollars, and prints totals by dimension, member, agent, and price digest.

**Call relations**: This command calls `_read_spend` for the accounting calculation. It is an operator-facing view into the ledger data created elsewhere.

*Call graph*: calls 1 internal fn (_read_spend); 3 external calls (run, echo, load_config).


##### `_read_spend`  (lines 632–639)

```
async def _read_spend(config: Config, window_seconds: int) -> SpendReport
```

**Purpose**: Builds the spend report for the current workspace and time window. The report is based on the accounting ledger.

**Data flow**: It opens the database layer, finds the workspace id, asks `SpendRollup` to read and summarize ledger entries for the given window, returns the report, and closes the database layer.

**Call relations**: `spend` calls this and handles printing. The accounting subsystem does the actual rollup math.

*Call graph*: called by 1 (spend); 5 external calls (__init__, select, dispose_db, init_db, workspace_tx).


##### `grants`  (lines 643–655)

```
def grants() -> None
```

**Purpose**: Lists OAuth account grants available to agents. OAuth is a common sign-in and permission-sharing system used by external services.

**Data flow**: It loads config, reads grant summaries, prints `no grants` if none exist, or prints each grant with agent, provider, account id, sharing mode, and date.

**Call relations**: This command calls `_read_grants` for database-backed grant information and formats it for operators.

*Call graph*: calls 1 internal fn (_read_grants); 3 external calls (run, echo, load_config).


##### `_read_grants`  (lines 658–665)

```
async def _read_grants(config: Config) -> tuple[GrantSummary, ...]
```

**Purpose**: Reads OAuth grant summaries for the current workspace.

**Data flow**: It opens the database layer, finds the workspace id, calls the grants subsystem for summaries, returns them, and closes the database layer.

**Call relations**: `grants` calls this and then prints the summaries. The grant-summary logic lives outside this CLI file.

*Call graph*: called by 1 (grants); 5 external calls (select, dispose_db, init_db, workspace_tx, grant_summaries).


##### `credential`  (lines 669–671)

```
def credential() -> None
```

**Purpose**: Defines the `credential` command group for extension secret slots. These are bring-your-own-key values, such as API keys, encrypted at rest.

**Data flow**: It does not process credentials itself; it groups related subcommands.

**Call relations**: `credential set` and `credential list` live under this group and perform the actual storage and inspection work.


##### `credential_set`  (lines 676–694)

```
def credential_set(slot: str) -> None
```

**Purpose**: Stores one secret value for a credential slot declared by an installed extension. It avoids putting secrets in command-line arguments, where they are easy to leak.

**Data flow**: It receives a slot name, loads config, checks that an extension declared that slot, requires the encryption key environment variable, reads the secret from a hidden prompt or standard input, rejects empty values, writes the encrypted credential, and prints confirmation.

**Call relations**: It uses `_declared_slots` to validate the slot and `_write_credential` to store the value. This supports extensions that need external service keys.

*Call graph*: calls 2 internal fn (_declared_slots, _write_credential); 5 external calls (run, ClickException, echo, prompt, load_config).


##### `credential_list`  (lines 698–708)

```
def credential_list() -> None
```

**Purpose**: Shows which declared credential slots exist and whether each one has a stored value. It never prints the secret values themselves.

**Data flow**: It loads config, gathers declared slots from extension manifests, reads stored slot names from the database, and prints each slot with its owning extension and `set` or `unset` status.

**Call relations**: It combines `_declared_slots` and `_read_stored_slots` so users can see what still needs to be filled.

*Call graph*: calls 2 internal fn (_declared_slots, _read_stored_slots); 3 external calls (run, echo, load_config).


##### `_declared_slots`  (lines 711–716)

```
def _declared_slots(config: Config) -> dict[str, str]
```

**Purpose**: Finds all credential slots declared by active extension manifests. A manifest is an extension's description of what it provides and needs.

**Data flow**: It loads manifests for the configured pack, turns each credential declaration into a mapping from slot name to extension name, and raises a CLI-friendly error if manifests cannot be loaded.

**Call relations**: Credential set and list both call this so they only work with slots that extensions actually declared.

*Call graph*: called by 2 (credential_list, credential_set); 2 external calls (ClickException, load_manifests).


##### `_write_credential`  (lines 719–726)

```
async def _write_credential(config: Config, key: str, slot: str, value: str) -> None
```

**Purpose**: Encrypts and stores a credential value for the current workspace.

**Data flow**: It opens the database layer, finds the workspace id, creates a `CredentialStore` using the supplied Fernet key, writes the slot value through that store, and closes the database layer. Fernet is an encryption scheme that protects the stored secret.

**Call relations**: `credential_set` calls this after reading and validating the secret value. It delegates encryption details to `CredentialStore`.

*Call graph*: called by 1 (credential_set); 6 external calls (__init__, Fernet, select, dispose_db, init_db, workspace_tx).


##### `_read_stored_slots`  (lines 729–743)

```
async def _read_stored_slots(config: Config) -> frozenset[str]
```

**Purpose**: Reads the names of credential slots that currently have stored values for the workspace.

**Data flow**: It opens the database layer, finds the workspace id, selects credential slot names for that workspace, returns them as an immutable set, and closes the database layer.

**Call relations**: `credential_list` calls this and compares the result against declared slots to show `set` or `unset`.

*Call graph*: called by 1 (credential_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `ext`  (lines 747–748)

```
def ext() -> None
```

**Purpose**: Defines the `ext` command group for extension store operations. Extensions add optional capabilities to a UFO deployment.

**Data flow**: It does not perform store work itself; it groups extension-related commands.

**Call relations**: Search, install, and remove commands live under this group and use the shared `_store` helper.


##### `_store`  (lines 751–754)

```
def _store(config: Config) -> ExtensionStore
```

**Purpose**: Creates an extension-store object from the configured catalog and lockfile. The catalog says what is available; the lockfile records what this deployment has pinned.

**Data flow**: It checks that the config enables an extension store. If not, it raises a CLI error. Otherwise it reads the catalog, finds the lockfile path, builds an `ExtensionStore`, and returns it.

**Call relations**: Extension search, install, and remove all call this before doing store-specific work.

*Call graph*: called by 3 (ext_install, ext_remove, ext_search); 4 external calls (__init__, ClickException, lockfile_path, read_catalog).


##### `ext_search`  (lines 759–772)

```
def ext_search(query: str) -> None
```

**Purpose**: Searches the configured extension store and prints matching extensions. It also shows whether each one is installed, available, or bundle-only.

**Data flow**: It receives a query string, loads config, builds the extension store, searches it, prints a no-match message if needed, and otherwise prints each listing with name, version, and state.

**Call relations**: This command depends on `_store` for access to the catalog and lockfile, then only formats the returned listings.

*Call graph*: calls 1 internal fn (_store); 2 external calls (echo, load_config).


##### `ext_install`  (lines 777–783)

```
def ext_install(name: str) -> None
```

**Purpose**: Pins an extension from the store into the deployment lockfile. Pinning means recording the exact version and digest to load later.

**Data flow**: It receives an extension name, loads config, builds the store, asks it to install the extension, converts store errors into CLI errors, and prints the installed name, version, and digest.

**Call relations**: It uses `_store` to reach the extension catalog and lockfile. After this, users typically run migrations if the extension owns tables, then restart `serve`.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `ext_remove`  (lines 788–794)

```
def ext_remove(name: str) -> None
```

**Purpose**: Removes an extension pin from the lockfile so it stops loading on the next server run.

**Data flow**: It receives an extension name, loads config, builds the store, asks it to remove that name, converts store errors into CLI errors, and prints confirmation.

**Call relations**: It uses `_store` for lockfile access. The server observes the changed lockfile on the next run, not during this command itself.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `_ufo_project_dir`  (lines 800–811)

```
def _ufo_project_dir() -> Path
```

**Purpose**: Finds the source project directory needed to build the UFO wheel for a bundle. A wheel is a packaged Python distribution.

**Data flow**: Starting from this file's location, it walks upward through parent directories, looks for `pyproject.toml`, reads it, and returns the first ancestor whose project name is `ufo`. If none is found, it raises a clear CLI error.

**Call relations**: `bundle` calls this before running the wheel build. This lets bundling work from any current working directory as long as the source checkout is present.

*Call graph*: called by 1 (bundle); 3 external calls (ClickException, Path, loads).


##### `bundle`  (lines 818–834)

```
def bundle(out: Path) -> None
```

**Purpose**: Builds a runnable deployment bundle containing pinned config, extension information, image recipe materials, and the UFO wheel.

**Data flow**: It receives an output directory, loads config, optionally reads the extension catalog, asks `Bundle` to assemble bundle files, runs `uv build` to create the UFO wheel into the bundle output, verifies the wheel exists, then prints the bundle path and pinned extensions.

**Call relations**: This command ties together config, extension catalog reading, bundle assembly, source-project discovery, subprocess wheel building, and final reporting. It is used when turning a local deploy into an artifact that can be run elsewhere.

*Call graph*: calls 1 internal fn (_ufo_project_dir); 8 external calls (__init__, ClickException, echo, run, wheel_name, config_path, load_config, read_catalog).


### `core/src/ufo/config.py`

`config` · `config load and startup`

This file is the project's configuration rulebook. A UFO deployment is expected to have one `ufo.toml` file, or a different file named by the `UFO_CONFIG` environment variable. This code says which settings are allowed, which ones are required, and what safe defaults are used when a setting is not provided.

Most settings are grouped into small configuration sections: database, blob storage, model selection, serving, sandboxing, browser transport, connectors, research, packs, and so on. These groups are Pydantic models, meaning they are Python classes that check incoming data and turn it into typed objects. They also reject unknown fields, so a misspelled setting does not silently do nothing.

A few sections add extra checks. The database section can derive a DBOS system-store URL from the main database URL. The blob storage section makes sure filesystem storage has a root folder and S3 storage has a bucket. The models section refuses to leave the automatic model name as the placeholder `auto`; deployments must pin it to a real model.

Without this file, the rest of the system would have to guess where databases, storage, models, and services live. This file acts like the checklist at launch: if something essential is missing, it stops immediately with a clear error.

#### Function details

##### `DatabaseConfig._derive_system_url`  (lines 36–49)

```
def _derive_system_url(self) -> 'DatabaseConfig'
```

**Purpose**: This validation step fills in the DBOS system database URL when the user did not write one explicitly. It keeps deployments simpler by deriving the companion system store from the main application database URL.

**Data flow**: It starts with a `DatabaseConfig` object containing the main `url` and maybe a `system_url`. If `system_url` is already set, it leaves everything unchanged. If not, it splits the main database URL, builds a sibling database name ending in `_dbos`, adjusts the driver name for SQLite or PostgreSQL, stores that derived value back on the config object, and returns the updated object.

**Call relations**: This is not called manually by normal application code. Pydantic runs it automatically after a `DatabaseConfig` is created or validated, which happens when the full `Config` object is built during configuration loading.


##### `BlobConfig._backend_complete`  (lines 73–78)

```
def _backend_complete(self) -> 'BlobConfig'
```

**Purpose**: This validation step checks that the chosen blob storage backend has the minimum information it needs. It prevents the system from starting with a storage choice that cannot actually read or write files.

**Data flow**: It receives a `BlobConfig` object after basic parsing. If the backend is `filesystem`, it checks that a local root path was provided. If the backend is `s3`, it checks that a bucket name was provided. If the required value is missing, it raises an error; otherwise it returns the config unchanged.

**Call relations**: Pydantic runs this automatically when blob settings are validated as part of the overall config. Later storage code can rely on this basic promise instead of checking again whether the selected backend has its required anchor setting.


##### `ModelsConfig._auto_model_concrete`  (lines 93–96)

```
def _auto_model_concrete(self) -> 'ModelsConfig'
```

**Purpose**: This validation step makes sure the deployment resolves the placeholder model name `auto` to a real model ID. That matters because agents may say they want `auto`, but the running deployment must decide exactly which model that means.

**Data flow**: It receives a `ModelsConfig` object with an `auto_model` value. If that value is empty or still equals the special placeholder `auto`, it raises an error. Otherwise it returns the config unchanged, now known to contain a concrete model choice.

**Call relations**: This runs automatically during model configuration validation. It supports the later model-selection flow by ensuring there is a real fallback model before any agent turn tries to use one.


##### `config_path`  (lines 265–266)

```
def config_path() -> Path
```

**Purpose**: This function decides which configuration file path to use. It checks the `UFO_CONFIG` environment variable first, and otherwise falls back to `ufo.toml` in the current working directory.

**Data flow**: It reads the process environment for `UFO_CONFIG`. If that variable is set, it wraps its value in a `Path` object; if it is not set, it uses the default `ufo.toml` path. The result is returned as a filesystem path object.

**Call relations**: When `load_config` is called without an explicit path, it calls `config_path` to find the file. This keeps the file-location rule in one small place instead of spreading environment-variable checks around the codebase.

*Call graph*: called by 1 (load_config); 1 external calls (Path).


##### `load_config`  (lines 269–275)

```
def load_config(path: Path | None=None) -> Config
```

**Purpose**: This function reads the deployment configuration file and turns it into a checked `Config` object. It is the main entry point for code that needs trusted settings.

**Data flow**: It takes an optional path. If no path is given, it asks `config_path` where to look. It then checks that the file exists; if it does not, it raises a clear `FileNotFoundError` explaining how to fix it. If the file exists, it reads the text, parses the TOML data with `tomllib.loads`, validates it against the `Config` model, and returns the resulting configuration object.

**Call relations**: Startup or setup code calls `load_config` when it needs the deployment settings. Inside, it hands off path selection to `config_path` and TOML parsing to Python's `tomllib`; Pydantic validation then triggers the section-level checks such as the database, blob, and model validators.

*Call graph*: calls 1 internal fn (config_path); 1 external calls (loads).


### `core/src/ufo/serve.py`

`entrypoint` · `startup, main loop, background work, shutdown`

Think of this file as the control room for one running UFO server. It does not contain one narrow feature. Instead, it assembles all the major parts that must exist before the product can serve users: the database connection, encrypted credential storage, extension plug-ins, model and memory backends, sandbox runner, connector OAuth flow, live update hub, artifact routes, surface routes, and DBOS workflow workers. DBOS is the workflow engine used here to run durable jobs that can survive restarts.

The key problem this file solves is safe sharing. One process serves many workspaces, so every request and every background job must know which workspace it is acting for. The file sets up middleware and route wrappers that bind a request to the right workspace before any database or credential access happens, then clear that binding afterwards. Without this, one workspace could accidentally read or write another workspace's data.

It also fails early on bad deployment choices. If an extension asks for a browser provider, search provider, auth proxy, or credential key that is missing, startup stops immediately instead of waiting for a user action to break later. For sandboxes, it sets up the egress proxy, which is the controlled doorway from sandboxed code to the internet, including credential injection and access rules. Finally, it starts background recovery, cancellation, writeback, heartbeat, and job runners so the service can recover abandoned work and shut down safely.

#### Function details

##### `_assert_no_reserved_routes`  (lines 110–126)

```
def _assert_no_reserved_routes(app: FastAPI) -> None
```

**Purpose**: Checks that this app has not mounted web routes under URL prefixes reserved for the separate onboarding gateway. This prevents a confusing deployment bug where the gateway would silently take those paths instead.

**Data flow**: It receives the FastAPI app, looks through its registered routes, and collects any route whose path starts with a reserved prefix such as `/login`, `/v1/onboard`, or `/ufo`. If it finds any, it raises an error; otherwise it leaves the app unchanged.

**Call relations**: The main `run` function calls this after all routes have been mounted and before the server starts listening. It acts as the final safety inspection before traffic reaches the app.

*Call graph*: called by 1 (run).


##### `run`  (lines 129–253)

```
def run() -> None
```

**Purpose**: Starts the shared UFO server process. It loads settings, builds every shared service object, registers workers and routes, starts the web server, and shuts workers down safely when the process exits.

**Data flow**: It begins with configuration and environment variables, then creates database connections, encrypted credential storage, extension manifests, blob storage, hub, sandbox carrier, model registry, search and memory services, connector registry, proxy endpoint, runtime object, DBOS workflow engine, FastAPI app, sync driver, and background jobs. The result is a live Uvicorn web server; on exit, it drains DBOS work and retires the fleet heartbeat only when safe.

**Call relations**: This is the top-level conductor for the file. It calls nearly every helper here to choose backends, validate extension requirements, mount extension and surface routes, create connector flows, launch jobs, and protect reserved routes before handing control to Uvicorn.

*Call graph*: calls 16 internal fn (from_env, _assert_no_reserved_routes, _connect_flow, _connector_registry, _launch_jobs, _mount_ext_routes, _mount_shared_surfaces, _proxy_endpoint, _select_carrier, _select_cdp_provider (+6 more)); 39 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, run, Fernet, DBOS (+15 more)).


##### `_stop_executor`  (lines 256–270)

```
def _stop_executor(dbos: DBOS, heartbeat: Heartbeat, graceful_shutdown_seconds: int) -> None
```

**Purpose**: Shuts down the workflow executor without accidentally allowing the same workflow to run twice elsewhere. It only retires this server's fleet seat if DBOS has no active workflows left.

**Data flow**: It receives the DBOS executor object, the heartbeat object, and a drain timeout. It asks DBOS to finish or cancel running workflows, checks whether any workflows are still active, logs and keeps the seat if work remains, or retires the heartbeat if the executor is empty.

**Call relations**: `run` calls this in its final cleanup block after Uvicorn exits. It hands off to DBOS for draining and to `Heartbeat.retire` only when the active-work check says it is safe.

*Call graph*: calls 1 internal fn (retire); called by 1 (run); 3 external calls (run, destroy, log).


##### `_shared_owner_dsn`  (lines 273–287)

```
def _shared_owner_dsn(config: Config) -> str
```

**Purpose**: Finds the database connection string used for owner-level cross-workspace reads. This is needed for jobs that must enumerate workspaces before rebinding each individual action to the correct workspace.

**Data flow**: It reads the owner database URL from an environment variable or from configuration. If none is available, it raises a clear startup error; if one is found, it normalizes a plain PostgreSQL URL into the async driver form used by the service.

**Call relations**: `run` calls this before initializing the owner database connection. The returned connection string lets owner-level job sweeps discover which workspaces need work.

*Call graph*: called by 1 (run).


##### `_launch_jobs`  (lines 290–333)

```
def _launch_jobs(runtime: Runtime, sync_driver: SyncDriver, page_feed: CorePageFeed) -> None
```

**Purpose**: Registers and starts the background job machinery for syncing sources, dispatching turns, reaping sandboxes, and reacting to page changes. These jobs are the service's behind-the-scenes workers.

**Data flow**: It receives the runtime, source sync driver, and page feed. It builds an admission helper, a page-change runner, core job bindings combined with extension job bindings, and then launches a `JobRunner` with shared services such as index, embed, blob storage, and model registry.

**Call relations**: `run` calls this after the runtime and DBOS client are ready. Inside, it creates `invoker_for` so job code can admit work for a specific workspace, then hands all bindings to `JobRunner.launch`.

*Call graph*: called by 1 (run); 8 external calls (__init__, __init__, __init__, __init__, __init__, durable_surfaces, bindings_from, core_jobs).


##### `_launch_jobs.invoker_for`  (lines 304–305)

```
def invoker_for(workspace_id: UUID) -> AdmissionInvoker
```

**Purpose**: Creates a small workspace-specific admission caller for background jobs. A job uses it when it needs to enqueue or admit work as one particular workspace.

**Data flow**: It receives a workspace ID and combines it with the shared admission object created by `_launch_jobs`. It returns an `AdmissionInvoker` tied to that workspace.

**Call relations**: This helper is passed into page-change and job runner setup inside `_launch_jobs`. Those runners call on it whenever a job must act within a particular workspace boundary.

*Call graph*: 1 external calls (__init__).


##### `_select_carrier`  (lines 336–376)

```
def _select_carrier(config: Config, manifests: tuple[Manifest, ...]) -> Carrier
```

**Purpose**: Chooses the sandbox backend that will run agent code. A carrier is the component that starts and controls sandboxes, such as the built-in local backend or an extension-provided remote backend.

**Data flow**: It starts with the built-in `local` carrier, adds carrier factories declared by extensions, checks for duplicate names, then looks up the configured backend. If the backend is remote, it also verifies that a public HTTPS proxy URL is configured so sandbox traffic can be controlled securely. It returns one constructed carrier.

**Call relations**: `run` calls this while building the runtime. The selected carrier later gets used by job and turn execution code, and `_launch_jobs` also gives it to the sandbox reaper.

*Call graph*: called by 1 (run); 2 external calls (__init__, urlparse).


##### `_source_backends`  (lines 379–393)

```
def _source_backends(manifests: tuple[Manifest, ...]) -> dict[str, SourceBackend]
```

**Purpose**: Builds the set of source-sync backends available to the sync driver. These backends know how to read content from places such as folders or extension-provided sources.

**Data flow**: It begins with the built-in folder source. For each extension manifest, it creates a credential reader limited to that extension's declared credential slots, then builds each source provider and stores it by backend name. Duplicate backend names cause startup to fail.

**Call relations**: `run` calls this when creating the `SyncDriver`. The sync driver later uses the returned map to decide which implementation should sync each source row.

*Call graph*: called by 1 (run); 2 external calls (__init__, __init__).


##### `_select_hub`  (lines 396–414)

```
def _select_hub(config: Config, manifests: tuple[Manifest, ...]) -> Hub
```

**Purpose**: Chooses the live-update hub for the process. The hub is the place where live frames or event updates are published and tailed.

**Data flow**: It registers the built-in in-process hub plus any extension-provided hub builders, checks for duplicate backend names, looks up the configured hub backend, and builds it using the configured hub URL. If the configured backend is unknown, it raises an error.

**Call relations**: `run` calls this during startup and stores the result in both the runtime and FastAPI app state. Later, shared surfaces use this hub through `HubTailer` to stream updates.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_cdp_provider`  (lines 417–445)

```
def _select_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> CdpProvider | None
```

**Purpose**: Selects the browser automation provider, if one is available and configured. CDP means Chrome DevTools Protocol, a browser-control interface used to drive browser sessions.

**Data flow**: It scans extension manifests for CDP provider specs, checks for duplicate backend names, and looks up the configured provider name. If none is registered, it returns `None`; if one is found, it verifies required credential support and builds the provider with a credential reader scoped to that extension.

**Call relations**: `run` calls this while building the runtime. `_require_cdp_provider` also calls it during requirement validation when an extension says browser control must be present.

*Call graph*: called by 2 (_require_cdp_provider, run); 1 external calls (__init__).


##### `_validate_requires`  (lines 448–472)

```
def _validate_requires(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Checks every extension's declared required seams before the service starts. A seam is a plug-in point, such as search, browser control, or memory search, that an extension depends on.

**Data flow**: It reads each manifest's `requires` list, finds the matching readiness check, and runs it. Unknown seams or failed checks are wrapped in an error message that names the extension and the missing capability.

**Call relations**: `run` calls this after credentials and manifests are loaded but before building the full runtime. It dispatches to requirement helpers such as `_require_cdp_provider`, `_require_search_provider`, and `_require_memory_search`.

*Call graph*: called by 1 (run).


##### `_require_cdp_provider`  (lines 475–489)

```
def _require_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that a usable browser automation provider exists when an active extension requires one. It turns an optional browser backend into a startup requirement.

**Data flow**: It receives configuration, manifests, and credentials, then calls `_select_cdp_provider`. If that returns `None`, it raises a clear error saying the configured provider is required but unavailable.

**Call relations**: `_validate_requires` uses this when an extension lists the `cdp_providers` seam. It relies on `_select_cdp_provider` to perform the actual lookup and credential checks.

*Call graph*: calls 1 internal fn (_select_cdp_provider).


##### `_select_search_provider`  (lines 492–527)

```
def _select_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> SearchProvider | None
```

**Purpose**: Selects the external search backend used by research tools, if configured. A search provider is the component that actually performs web or document search for the system.

**Data flow**: It scans manifests for search provider specs, checks for duplicate backend names, and returns `None` if no search provider is configured. If a provider name is configured, it verifies that the name exists and that credential encryption is available, then builds the provider with a scoped credential reader.

**Call relations**: `run` calls this while creating the runtime. `_require_search_provider` calls it when an extension declares that search must be available.

*Call graph*: called by 2 (_require_search_provider, run); 2 external calls (__init__, __init__).


##### `_require_search_provider`  (lines 530–543)

```
def _require_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Makes search a required startup dependency when an extension needs research tools. It prevents the first search request from being the moment a missing provider is discovered.

**Data flow**: It checks whether the search provider setting is present. If not, it raises an error; if it is present, it calls `_select_search_provider` so unknown names or missing credentials also fail immediately.

**Call relations**: `_validate_requires` calls this for the `search_providers` seam. It delegates provider construction and validation to `_select_search_provider`.

*Call graph*: calls 1 internal fn (_select_search_provider).


##### `_require_memory_search`  (lines 546–572)

```
def _require_memory_search(_config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Verifies that exactly one usable default memory-search provider is installed when memory search is required. Memory search is the feature that finds relevant stored memories or indexed content.

**Data flow**: It looks through all manifests for providers with the default memory-search name. It raises an error if none exist, if more than one extension registers the same default provider, or if the selected provider declares credentials but no credential store is available.

**Call relations**: `_validate_requires` calls this for the `memory_search` seam. Unlike the browser and search checks, this helper performs the full check directly rather than calling another selector.


##### `_select_auth_proxy`  (lines 584–621)

```
def _select_auth_proxy(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> AuthProxy | None
```

**Purpose**: Chooses the fallback authentication proxy for connector feed syncing. This proxy supplies or exchanges credentials for connector providers that do not use their own broker.

**Data flow**: It scans extension manifests for auth proxy specs, checks for duplicate backend names, and decides which backend to use from configuration or, if only one exists, by default. It rejects ambiguous, unknown, or credential-less choices, then builds the proxy with a credential reader scoped to the declaring extension.

**Call relations**: `_connector_registry` calls this while building the connector registry. The returned proxy becomes the fallback path for connector credentials when a connector has no provider-specific broker.

*Call graph*: called by 1 (_connector_registry); 2 external calls (__init__, __init__).


##### `_mount_ext_routes`  (lines 624–662)

```
def _mount_ext_routes(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, index: IndexBackend, embed: EmbedClient) -> None
```

**Purpose**: Adds extension-provided HTTP routes to the FastAPI app under `/ext/<extension>/<path>`. Each route is wrapped so the request must first be identified as belonging to a workspace.

**Data flow**: It receives the app, manifests, credentials, index backend, and embed client. For each extension route, it builds an extension context and registers an endpoint wrapper. At request time, that wrapper identifies the workspace, rejects unauthorized requests, binds the workspace, and then calls the extension's handler.

**Call relations**: `run` calls this after jobs are launched and before the server starts. It uses the extension context builder and FastAPI route registration; the nested endpoint is later invoked by the web framework for matching requests.

*Call graph*: called by 1 (run); 2 external calls (add_route, context_for).


##### `_mount_ext_routes.endpoint`  (lines 646–656)

```
async def endpoint(request: Request, handler=spec.handler, identify=spec.identify, extension_context=context) -> Response
```

**Purpose**: Acts as the per-request safety wrapper around one extension route. It makes sure the extension handler runs only after the request has been tied to a valid workspace.

**Data flow**: It receives a web request. It calls the route's identify function; if identification fails, it returns a 401 unauthorized response. If identification succeeds, it enters that workspace context and awaits the extension handler, returning whatever response the handler produces.

**Call relations**: `_mount_ext_routes` creates this function once for each extension route and registers it with FastAPI. FastAPI calls it when an incoming request matches that route.

*Call graph*: 2 external calls (Response, ws).


##### `WorkspaceScopeBoundary.__call__`  (lines 683–691)

```
async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None
```

**Purpose**: Clears workspace state at the start and end of every HTTP request. This prevents one request's workspace identity from leaking into another request in the same shared process.

**Data flow**: It receives the raw ASGI request scope plus receive and send callables. Non-HTTP traffic is passed through unchanged. For HTTP traffic, it sets the current workspace to `None`, runs the downstream app, and always sets it back to `None` in a final cleanup step.

**Call relations**: `_mount_shared_surfaces` installs this as middleware. The web server calls it around each HTTP request, and downstream surface endpoints set the workspace inside the protected boundary.

*Call graph*: 1 external calls (set).


##### `_mount_shared_surfaces`  (lines 694–775)

```
def _mount_shared_surfaces(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: BlobStore, hub: Hub, dbos_client: DBOSClient, artifact_secret: str, public_base_url
```

**Purpose**: Mounts shared surface routes, such as user-facing integrations, so one server can serve many workspaces safely. It also creates the writeback poller for durable surfaces that need background delivery.

**Data flow**: It receives the app, manifests, credential store, blob store, hub, DBOS client, artifact secret, and public URL. It installs the workspace-clearing middleware, builds admission and hub tail helpers, then registers each surface route with an endpoint wrapper that authenticates the request and binds the workspace. For surfaces that support posting writebacks, it creates a shared `WritebackPoller` and stores it on app state.

**Call relations**: `run` calls this after extension routes are mounted. It creates nested `context_for` and endpoint helpers, registers routes with FastAPI, and prepares the poller that `_serve_lifespan` later starts.

*Call graph*: called by 1 (run); 10 external calls (__init__, __init__, __init__, __init__, add_middleware, add_route, durable_surfaces, writeback_workspaces, log, uuid4).


##### `_mount_shared_surfaces.context_for`  (lines 721–731)

```
def context_for(workspace_id: UUID, surface: str) -> SurfaceContext
```

**Purpose**: Builds the per-request surface context passed to a surface handler. This context is the bundle of tools the surface needs, already tied to one workspace and one surface name.

**Data flow**: It receives a workspace ID and surface name. It combines them with shared services such as blob storage, admission, hub tailing, credentials, artifact token secret, and public base URL, then returns a `SurfaceContext`.

**Call relations**: Surface endpoint wrappers inside `_mount_shared_surfaces` call this after identifying the request's workspace. The writeback poller also uses it to build the right context when delivering durable surface updates.

*Call graph*: 2 external calls (__init__, __init__).


##### `_mount_shared_surfaces.endpoint`  (lines 747–761)

```
async def endpoint(request: Request, handler=route.handler, identify=resolver, surface=spec.name, surface_auth=auth) -> Response
```

**Purpose**: Acts as the per-request safety and authentication wrapper around one shared surface route. It verifies the request, binds the correct workspace, and then calls the surface's real handler.

**Data flow**: It receives a web request and asks the surface's identify function to resolve it using surface authentication. If the identify function returns a response, that response is sent directly; if it returns nothing, the endpoint sends 401 unauthorized. If it returns a workspace ID, the endpoint sets the current workspace and calls the route handler with a surface context.

**Call relations**: `_mount_shared_surfaces` creates and registers this endpoint for each surface route. FastAPI calls it for matching requests, and `WorkspaceScopeBoundary` later clears the workspace after the full response is done.

*Call graph*: 3 external calls (Response, set, context_for).


##### `_serve_lifespan`  (lines 779–802)

```
async def _serve_lifespan(app: FastAPI) -> AsyncIterator[None]
```

**Purpose**: Runs app-level background tasks for as long as the FastAPI app is alive. These tasks recover abandoned workflows, reconcile cancellations, and optionally deliver durable surface writebacks.

**Data flow**: It receives the FastAPI app and opens an asynchronous task group. It starts executor recovery and cancel reconciliation tasks, adds the writeback poller task if one was installed, yields control while the app runs, and cancels those tasks during shutdown.

**Call relations**: `run` passes this function as the FastAPI lifespan handler. FastAPI enters it when the server starts serving and exits it during shutdown; heartbeat is deliberately handled elsewhere by `run`.

*Call graph*: 3 external calls (__init__, __init__, TaskGroup).


##### `_proxy_endpoint`  (lines 805–835)

```
def _proxy_endpoint(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, pricing: Pricing, run_tokens: RunTokenCodec, workspace_fs: SandboxFsCredentialMinter | None=No
```

**Purpose**: Creates or describes the sandbox egress proxy endpoint. The egress proxy is the controlled network doorway that sandboxed code must use to reach outside services.

**Data flow**: It receives configuration, manifests, credentials, pricing, run-token codec, and optional workspace filesystem credential minter. If no public proxy URL is configured, it starts an in-process local proxy and returns its endpoint. If a public proxy URL is configured, it reads the shared proxy certificate from the environment and returns a `ProxyEndpoint` pointing to the external proxy.

**Call relations**: `run` calls this while building the runtime. It delegates local single-node setup to `_local_egress_proxy`; otherwise it builds the endpoint information directly for a separately running proxy service.

*Call graph*: calls 1 internal fn (_local_egress_proxy); called by 1 (run); 1 external calls (__init__).


##### `_local_egress_proxy`  (lines 838–877)

```
def _local_egress_proxy(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, pricing: Pricing, run_tokens: RunTokenCodec, workspace_fs: SandboxFsCredentialMinter | Non
```

**Purpose**: Starts the in-process egress proxy used by local or single-node sandbox deployments. It gives sandboxes one controlled route to the network instead of letting them connect freely.

**Data flow**: It builds a rule resolver from model rules, grants, credentials, injected credential slots, manifest internet rules, connector transfer hosts, and connector command-line tools. It starts a new event loop in a daemon thread, boots the proxy there, and waits up to the startup timeout for a `ProxyEndpoint` to come back.

**Call relations**: `_proxy_endpoint` calls this when the deployment does not use a separate public proxy. Inside, the nested `_boot` coroutine creates the certificate authority and starts the actual `EgressProxy`.

*Call graph*: called by 1 (_proxy_endpoint); 10 external calls (__init__, __init__, new_event_loop, run_coroutine_threadsafe, Thread, connector_clis, injecting_slots, model_rule_base, connector_transfer_hosts, derive_manifest_rules).


##### `_local_egress_proxy._boot`  (lines 865–875)

```
async def _boot() -> ProxyEndpoint
```

**Purpose**: Boots the local egress proxy inside its dedicated event loop. It creates the temporary certificate authority and starts the proxy server.

**Data flow**: It generates a certificate and key, then constructs an `EgressProxy` with the rule resolver, authorization check, run-token codec, pricing data, and optional workspace credential refresher. It starts the proxy on the configured port and returns the resulting endpoint.

**Call relations**: `_local_egress_proxy` schedules this coroutine on the proxy's private event loop. Its result is the `ProxyEndpoint` that the sandbox carrier will receive.

*Call graph*: 2 external calls (__init__, generate_ca).


##### `_connector_registry`  (lines 883–906)

```
def _connector_registry(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> ConnectorRegistry
```

**Purpose**: Builds the central registry of connector providers installed in this server. Connectors are integrations that may need OAuth grants, brokered credentials, or feed syncing.

**Data flow**: It scans manifests for connector declarations, checks that no two extensions claim the same provider name, and creates one registry entry per provider. It also builds the connector namespace resolver and asks `_select_auth_proxy` for the fallback auth proxy, then returns a `ConnectorRegistry`.

**Call relations**: `run` calls this during runtime setup. The returned registry is used by dynamic connector tools and by the sync runner when it needs credentials for connector-backed feeds.

*Call graph*: calls 1 internal fn (_select_auth_proxy); called by 1 (run); 3 external calls (__init__, __init__, open_connector_namespace).


##### `_connect_flow`  (lines 909–934)

```
def _connect_flow(credentials: CredentialStore | None, config: Config, manifests: tuple[Manifest, ...]) -> ConnectFlow | None
```

**Purpose**: Builds the OAuth connection flow for installed connectors. This is the machinery that starts a user's authorization handoff and completes it when the provider redirects back.

**Data flow**: It receives the credential store, configuration, and manifests. If there is no credential store, it returns `None`. Otherwise it collects OAuth provider descriptors from connector manifests, checks for duplicate provider names, derives the redirect URI, and returns a `ConnectFlow` with encryption, grant storage, and namespace resolution.

**Call relations**: `run` calls this and installs the result globally for connector tools and callback routes. It calls `_connect_redirect_uri` so both OAuth legs use the correct public callback URL.

*Call graph*: calls 1 internal fn (_connect_redirect_uri); called by 1 (run); 3 external calls (__init__, __init__, open_connector_namespace).


##### `_connect_redirect_uri`  (lines 937–961)

```
def _connect_redirect_uri(config: Config, providers: Mapping[str, OAuthProvider]) -> str
```

**Purpose**: Builds and validates the public OAuth callback URL for connector authorization. This URL must be reachable by the external provider after the user approves access.

**Data flow**: It reads `connect.public_base_url` from configuration and looks at whether any providers are registered. With no providers, it may return an empty or simple callback URL. With providers, it requires a base URL with an HTTP or HTTPS scheme, a real host, and not a local bind address; then it appends the fixed callback path.

**Call relations**: `_connect_flow` calls this while constructing the OAuth flow. Its output becomes the redirect URI shared by the tool that starts OAuth and the callback route that finishes it.

*Call graph*: called by 1 (_connect_flow); 1 external calls (urlparse).

## 📊 State Registers Touched

- `reg-deployment-schema-version` — The shared record of which database and extension upgrades have already been applied.
- `reg-effective-config` — The running service’s merged settings, such as required keys, enabled backends, safety options, and service behavior.
- `reg-extension-pack-manifest` — The installed pack and extension menu that says what tools, routes, jobs, skills, credentials, and backends exist.
- `reg-agent-profile-settings` — The saved assistant settings for a workspace, including which agent is used and what it is allowed to do.
- `reg-surface-installation-binding` — The stored connection between outside channels like Slack, web chat, or terminal clients and an internal workspace conversation.
- `reg-runtime-presence` — The live roll-call of server and worker processes used to recover abandoned work safely.
- `reg-credential-secret-store` — The encrypted store of workspace secrets and credential kinds used without exposing raw tokens to agents.
- `reg-tool-catalog` — The live list of tools the model can call, including their names, descriptions, schemas, and dispatch targets.
- `reg-model-catalog-pricing` — The shared list of available AI models, provider details, limits, credentials, and prices.
- `reg-prompt-skill-library` — The enabled instructions, skill folders, helper profiles, and prompt versions that shape how the agent behaves.
- `reg-browser-session-provider` — The shared way to obtain a browser automation endpoint for a turn, regardless of where the browser runs.
- `reg-connector-broker-catalog` — The known external service brokers and provider actions that let agents use connected services safely.
- `reg-mcp-server-connections` — The configured MCP tool-server connections used to discover and call extra provider tools.
- `reg-extension-object-store` — The durable per-workspace storage and named objects that extensions expose or update over time.
- `reg-observability-trace-context` — The trace, metric, and log context that follows requests and turns so operators can understand what happened.
- `reg-row-level-security-context` — The database safety context that keeps each workspace’s rows separated even when code uses shared tables.
- `reg-database-connection-pool` — The shared database engine/session pool and transaction lifecycle used by migrations, request handlers, workers, and shutdown cleanup.
- `reg-client-update-check-cache` — Hosted gateway state or cache for known client/install versions and whether a terminal user should be offered an updated curl-based install.
- `reg-sandbox-image-artifact-state` — The validated sandbox/workroom image identity and preflight health result used later when creating sandbox runtimes.
- `reg-user-created-skill-store` — Persistent user-authored skill definitions and metadata that are loaded into the skill library and made available to prompts and tools across turns.
- `reg-repl-snippet-state` — Remembered Python or JavaScript REPL snippets and scratch execution context retained by the REPL extension for reuse across tool calls or turns.
- `reg-web-search-fetch-backend` — The configured web-search and page-fetch provider backend, client settings, and availability used by research, browsing, source, and SDK search calls.
