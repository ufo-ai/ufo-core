# Process Bootstrap, CLI Commands, and Pack Selection  `stage-2`

This stage is the system’s front door. It runs when UFO is first started from a terminal, launched as a service, or packaged for deployment. The main command tools are ufoctl in cli.py for local setup, running, inspection, and packaging, and the hosted control command in main.py for starting the managed web gateway, preparing the database, invitations, Slack setup, and security rules. serve.py is the main assembly bench: it reads settings and connects the database, extensions, web routes, background workers, credentials, sandbox, and shutdown hooks. onboarding.py performs first-run setup by creating the first workspace, admin, and assistant, while preventing duplicate or incomplete setup. bundle.py freezes a chosen configuration into a Docker deployment folder. select.py picks the one sandbox, meaning the isolated place where tools run.

The pack files are ready-made menus of capabilities. Assistant, billing, hosted, and eval packs choose different assistant setups. Chief of staff, DSQA, GDPVal, sample, and YC packs declare their own extensions, skills, and setup steps so startup can enable the right bundle by name.

## Files in this stage

### Operator entry points
These files provide the main command-line fronts for local UFO administration and hosted control-service operations.

### `core/src/ufo/cli.py`

`entrypoint` · `startup, administration, interactive chat, packaging`

This file turns many backend features into commands a person can type. Without it, a user would have to create config files, database rows, secrets, extension pins, spending rules, and chat requests by hand. It is like the control panel for the system.

At startup, the CLI loads a nearby `.env` file so local secrets work without manual shell setup. The `init` command creates a default config, prepares the database, creates the first workspace owner and default agent, writes development secrets, and saves a long-lived CLI token on the user’s machine. The serving commands then start the main server, proxy, or ingress gateway.

The `chat` command sends messages to the running server over HTTP and prints the agent’s streaming replies. It understands simple server instructions such as “print text,” “show a status line,” “poll again later,” or “ask privately for a secret.”

The rest of the file is administrative tooling: setting and listing spending caps, reading spending reports, auditing transcript reads, listing OAuth grants, storing encrypted extension credentials, searching or pinning extensions, and building a deployable bundle. Most database work follows the same pattern: load config, open the database, find the current workspace, read or write the needed rows, then close the connection.

#### Function details

##### `_ufoctl_dir`  (lines 65–67)

```
def _ufoctl_dir() -> Path
```

**Purpose**: Chooses where this CLI stores its own local files, such as the saved login token and current chat session. A user can override the location with an environment variable.

**Data flow**: It reads the `UFOCTL_DIR` environment variable. If it is set, that path is used; otherwise it builds a path under the user’s home directory named `.ufoctl`. It returns that path without creating it.

**Call relations**: The setup flow uses it when writing the CLI token. The chat flow uses it to find the token and remember the ongoing session.

*Call graph*: called by 3 (_session, chat, init); 2 external calls (Path, home).


##### `_dotenv_path`  (lines 70–71)

```
def _dotenv_path() -> Path
```

**Purpose**: Finds the `.env` file that sits next to the main UFO config file. This is where local secret values can be stored for development.

**Data flow**: It asks the config system where the config file lives, takes that file’s folder, and appends `.env`. It returns the resulting path.

**Call relations**: Startup uses it to load secrets. Initialization uses it to report and write newly generated development secrets.

*Call graph*: called by 3 (_load_dotenv, _write_dev_secrets, init); 1 external calls (config_path).


##### `_dotenv_pairs`  (lines 74–90)

```
def _dotenv_pairs(text: str) -> list[tuple[str, str]]
```

**Purpose**: Reads simple `.env` text and turns it into name-and-value pairs. This lets the CLI understand the small environment-file format it writes itself.

**Data flow**: It receives raw text, skips blank lines and comments, accepts lines shaped like `KEY=VALUE`, removes an optional `export`, and strips matching surrounding quotes. It returns a list of parsed pairs.

**Call relations**: The environment loader uses it before commands run. The secret writer uses it to avoid overwriting names already present in the `.env` file.

*Call graph*: called by 2 (_load_dotenv, _write_dev_secrets).


##### `_load_dotenv`  (lines 93–102)

```
def _load_dotenv() -> None
```

**Purpose**: Loads local `.env` values into the process environment before any command reads secrets. Already exported shell variables win over file values.

**Data flow**: It finds the `.env` path, does nothing if the file is missing, parses the file if present, and sets only environment variables that are not already set.

**Call relations**: The top-level CLI group calls it first, so every command sees the same local-secret behavior.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (main).


##### `main`  (lines 106–108)

```
def main() -> None
```

**Purpose**: Defines the root `ufoctl` command. It gives all subcommands a common startup step.

**Data flow**: When a command starts, it loads `.env` values into the environment. It does not return user data; it prepares the process for the selected subcommand.

**Call relations**: All command groups and commands hang from this root. Its main handoff is to `_load_dotenv`, which prepares secrets before the chosen command continues.

*Call graph*: calls 1 internal fn (_load_dotenv).


##### `_one_address`  (lines 111–116)

```
def _one_address(_ctx: click.Context, _param: click.Parameter, value: str) -> str
```

**Purpose**: Validates that an email option looks like one local email address with a domain. It gives a clear command-line error before database setup begins.

**Data flow**: It receives the option value from Click, checks it with the seat/email helper, and either returns the same value or raises a friendly parameter error.

**Call relations**: The `init` command uses it for the owner email option, so onboarding gets a clean address instead of discovering the problem later.

*Call graph*: 2 external calls (BadParameter, email_domain).


##### `init`  (lines 122–150)

```
def init(email: str, model: str) -> None
```

**Purpose**: Creates a usable UFO workspace from scratch. It writes default local config if needed, prepares storage, creates the first owner and agent, and saves a CLI token.

**Data flow**: It takes an owner email and model name. It writes config and secrets if missing, creates a Postgres system database when needed, runs migrations, onboards the workspace, mints a bearer token, and writes that token to the local CLI directory.

**Call relations**: This is the first command most local users run. It calls the secret writer, database creator, migration runner, onboarding helper, token helper, and path helper in sequence so later commands such as `serve` and `chat` have everything they need.

*Call graph*: calls 5 internal fn (_create_postgres_system_database, _dotenv_path, _onboard, _ufoctl_dir, _write_dev_secrets); 7 external calls (run, ClickException, echo, mint_token, config_path, load_config, apply_migrations).


##### `_write_dev_secrets`  (lines 153–175)

```
def _write_dev_secrets(config: Config) -> tuple[str, ...]
```

**Purpose**: Creates the local secret values needed for a zero-setup development server. It avoids replacing secrets that already exist.

**Data flow**: It receives loaded config, generates an encryption key and token-signing secrets, reads existing `.env` content and current environment variables, writes only missing names, also adds them to the current process, and returns the names it added.

**Call relations**: The `init` command uses it before onboarding. Its output is printed to tell the user what was added, and those values later let the server encrypt credentials and verify CLI tokens.

*Call graph*: calls 2 internal fn (_dotenv_pairs, _dotenv_path); called by 1 (init); 2 external calls (generate_key, token_urlsafe).


##### `_onboard`  (lines 178–197)

```
async def _onboard(config: Config, email: str, model: str) -> Onboarded
```

**Purpose**: Creates the first workspace, owner member, default agent, model setup, and extension onboarding records. It keeps database setup in one clean async operation.

**Data flow**: It receives config, email, and model. It opens the database, optionally creates an encrypted credential store from the configured key, loads extension manifests, runs core onboarding, runs extension onboarding steps, returns the created onboarding summary, and always closes the database.

**Call relations**: `init` calls it after migrations and secrets are ready. It hands back workspace information used to mint the local CLI token.

*Call graph*: called by 1 (init); 6 external calls (__init__, __init__, Fernet, dispose_db, init_db, load_manifests).


##### `_create_postgres_system_database`  (lines 200–211)

```
async def _create_postgres_system_database(config: Config) -> None
```

**Purpose**: Creates the extra Postgres database used by the system if it does not already exist. This smooths first-time setup for Postgres deployments.

**Data flow**: It turns the configured async database URL into a plain Postgres connection string, extracts the system database name, connects to Postgres, checks whether that database exists, creates it if missing, and closes the connection.

**Call relations**: `init` calls it only when the configured database is Postgres. After this, migrations can safely run against the expected databases.

*Call graph*: called by 1 (init); 1 external calls (connect).


##### `migrate`  (lines 215–232)

```
def migrate() -> None
```

**Purpose**: Brings the database schema up to date. A schema is the set of tables and columns the application expects.

**Data flow**: It loads config, optionally uses an owner database URL from the environment for shared deployments, applies core and extension migrations, and prints confirmation.

**Call relations**: Operators run this after setup or extension changes. It delegates the actual schema changes to the database migration layer.

*Call graph*: 3 external calls (echo, load_config, apply_migrations).


##### `serve`  (lines 236–238)

```
def serve() -> None
```

**Purpose**: Starts the main UFO service process. This is the command that runs surfaces, workers, and background jobs.

**Data flow**: It takes no command arguments. It hands control to the server runner, which keeps the process alive.

**Call relations**: This command is a thin entry point from the CLI into the main serving subsystem.

*Call graph*: 1 external calls (run).


##### `proxy`  (lines 242–244)

```
def proxy() -> None
```

**Purpose**: Starts the shared egress proxy, which is the outbound network gateway for workspace sandboxes. A proxy is a service that forwards traffic under controlled rules.

**Data flow**: It takes no command arguments and calls the proxy runner. The runner owns the long-running network process.

**Call relations**: This command is used when the proxy is deployed separately from the main server.

*Call graph*: 1 external calls (run).


##### `ingress`  (lines 248–250)

```
def ingress() -> None
```

**Purpose**: Starts the sandbox ingress gateway, which lets approved requests reach sandbox ports. It protects those routes with tokens.

**Data flow**: It takes no command arguments and calls the ingress runner. That runner starts the reverse-proxy service.

**Call relations**: This command is another thin CLI door into a long-running server component.

*Call graph*: 1 external calls (run).


##### `chat`  (lines 256–274)

```
def chat(message: str | None, new: bool) -> None
```

**Purpose**: Lets a user talk to the agent from the terminal. It either sends one message or opens a simple prompt loop.

**Data flow**: It loads config, reads the saved CLI token, chooses or creates a conversation channel, and sends each non-empty message through `_run_turn`. If no token exists, it tells the user to run initialization first.

**Call relations**: This is the human-facing start of the chat flow. It relies on `_session` for continuity and `_run_turn` for each server interaction.

*Call graph*: calls 3 internal fn (_run_turn, _session, _ufoctl_dir); 3 external calls (ClickException, echo, load_config).


##### `_session`  (lines 277–283)

```
def _session(new: bool) -> str
```

**Purpose**: Keeps chat conversations continuing across separate CLI runs. It stores a small session id on disk.

**Data flow**: It reads or writes a `session` file in the CLI directory. If the caller asks for a new session, or no session exists, it creates a fresh random id. It returns the session id text.

**Call relations**: `chat` calls it before sending messages so the server can connect each message to the right conversation channel.

*Call graph*: calls 1 internal fn (_ufoctl_dir); called by 1 (chat); 1 external calls (uuid4).


##### `_run_turn`  (lines 286–300)

```
def _run_turn(config: Config, token: str, channel: str, message: str) -> None
```

**Purpose**: Runs one chat turn and turns connection problems into helpful CLI messages. A turn is one user message plus the agent’s response.

**Data flow**: It builds the server base URL from config, then runs the async streaming chat call. If the user presses Ctrl-C, it explains that the server may keep working; if the connection fails, it suggests retrying to catch up.

**Call relations**: `chat` calls it for each entered message. It hands the real network streaming work to `_stream_turn`.

*Call graph*: calls 1 internal fn (_stream_turn); called by 1 (chat); 3 external calls (run, ClickException, echo).


##### `_stream_turn`  (lines 312–320)

```
async def _stream_turn(base: str, token: str, channel: str, message: str) -> None
```

**Purpose**: Opens the HTTP client and drives one streamed chat turn. It connects the network layer to the terminal display.

**Data flow**: It receives the server URL, token, channel, and message. It builds authorization headers and the channel path, creates a display object and async HTTP client, then asks `_ChatStream` to run the turn.

**Call relations**: `_run_turn` calls it inside an async runner. It constructs `_TurnDisplay` for output and `_ChatStream` for the server protocol.

*Call graph*: called by 1 (_run_turn); 3 external calls (__init__, __init__, AsyncClient).


##### `_ChatStream.run`  (lines 334–345)

```
async def run(self, message: str) -> None
```

**Purpose**: Runs the full chat-stream loop until the server says the turn is finished. It also deals with private credential prompts after the normal stream closes.

**Data flow**: It starts with the user’s message as the request body. Each drain may ask it to wait and reconnect with an empty body, or may return secrets to fill. When no more polling is needed, it closes the display, prompts for any requested secrets, sends them, and returns.

**Call relations**: `_stream_turn` creates the `_ChatStream` and calls this method. This method coordinates `_drain` for server output and `_fulfill_secret` for out-of-band secret entry.

*Call graph*: calls 2 internal fn (_drain, _fulfill_secret); 1 external calls (sleep).


##### `_ChatStream._drain`  (lines 347–387)

```
async def _drain(self, body: str) -> _Pending
```

**Purpose**: Reads one server response stream and turns server directives into terminal output. A directive is a small instruction such as “print this text” or “show this status.”

**Data flow**: It posts the request body to the chat path, checks for a successful response, reads each line, unescapes fields, updates the display for text, lines, notes, and status, records secret prompts, records polling instructions, and returns a pending summary.

**Call relations**: `_ChatStream.run` calls it once or many times. It uses `_unescape` to decode the server’s tab-separated protocol and `_TurnDisplay` methods to show the result.

*Call graph*: calls 1 internal fn (_unescape); called by 1 (run); 2 external calls (__init__, ClickException).


##### `_ChatStream._fulfill_secret`  (lines 389–406)

```
async def _fulfill_secret(self, sealed: str, slot: str, prompt: str) -> None
```

**Purpose**: Privately asks the user for a secret value and sends it to the server without adding it to the chat transcript. This protects credentials from being treated like normal chat text.

**Data flow**: It receives the sealed prompt marker, slot name, and prompt text. It asks the user for hidden input, posts the value with special headers, checks the response, and prints any acknowledgement line from the server.

**Call relations**: `_ChatStream.run` calls it after a turn has ended and `_drain` collected secret prompts. It again uses `_unescape` to read server acknowledgement lines.

*Call graph*: calls 1 internal fn (_unescape); called by 1 (run); 2 external calls (ClickException, prompt).


##### `_unescape`  (lines 412–424)

```
def _unescape(text: str) -> str
```

**Purpose**: Decodes escaped text from the chat directive protocol. This lets tabs, newlines, and backslashes travel safely inside one line.

**Data flow**: It receives encoded text, walks through it character by character, replaces known backslash escape sequences with their real characters, and returns the decoded string.

**Call relations**: The chat stream reader and secret fulfiller use it whenever they split a server directive into fields.

*Call graph*: called by 2 (_drain, _fulfill_secret).


##### `_TurnDisplay.text`  (lines 441–445)

```
def text(self, delta: str) -> None
```

**Purpose**: Prints streamed pieces of agent text exactly as they arrive. This is what makes the answer appear gradually in the terminal.

**Data flow**: It removes any temporary status meter, writes the text fragment to standard output without forcing a newline, and records whether the current output line is still open.

**Call relations**: `_ChatStream._drain` calls it when the server sends a text directive. It uses `_erase_meter` so status text never overwrites the response.

*Call graph*: calls 1 internal fn (_erase_meter); 1 external calls (echo).


##### `_TurnDisplay.line`  (lines 447–451)

```
def line(self, text: str) -> None
```

**Purpose**: Prints a complete finished line from the server. This is used for non-streamed answers, notices, failures, links, or credential acknowledgements.

**Data flow**: It first closes any unfinished streamed line cleanly, then writes the full line to standard output.

**Call relations**: `_ChatStream._drain` and `_ChatStream._fulfill_secret` cause this path when the server says something complete. It relies on `_close_line` for tidy terminal layout.

*Call graph*: calls 1 internal fn (_close_line); 1 external calls (echo).


##### `_TurnDisplay.activity`  (lines 453–457)

```
def activity(self, note: str) -> None
```

**Purpose**: Shows a dim, separate activity note during a turn, such as a tool call or skill load. It keeps these notes from mixing into the answer text.

**Data flow**: It closes any open streamed line, styles the note dimly, and writes it to standard output on its own line.

**Call relations**: `_ChatStream._drain` calls it for note directives. It uses `_close_line` to preserve readable output.

*Call graph*: calls 1 internal fn (_close_line); 2 external calls (echo, style).


##### `_TurnDisplay.meter`  (lines 459–468)

```
def meter(self, text: str) -> None
```

**Purpose**: Shows a temporary status meter on terminals that support it. It is meant for short progress text that can later disappear.

**Data flow**: It ignores non-interactive output. On a real terminal, it first separates from any open streamed line, writes an erasable dim status line to standard error, and remembers that the meter is visible.

**Call relations**: `_ChatStream._drain` calls it for status directives. Later text, lines, or close operations erase it before printing permanent output.

*Call graph*: 2 external calls (echo, style).


##### `_TurnDisplay.close`  (lines 470–471)

```
def close(self) -> None
```

**Purpose**: Finishes terminal output for a turn. It prevents the prompt or next message from appearing on the same half-written line.

**Data flow**: It closes any open output line and clears any temporary meter. It returns no data.

**Call relations**: `_ChatStream.run` calls it when streaming is done, before prompting for any requested secrets.

*Call graph*: calls 1 internal fn (_close_line).


##### `_TurnDisplay._erase_meter`  (lines 473–477)

```
def _erase_meter(self) -> None
```

**Purpose**: Removes the temporary status meter if one is currently shown. This protects permanent text from being visually corrupted.

**Data flow**: It checks the display’s meter flag. If a meter is visible, it writes the terminal erase sequence to standard error and marks the meter as gone.

**Call relations**: The display uses it before printing streamed text and while closing lines. It is an internal cleanup helper for terminal layout.

*Call graph*: called by 2 (_close_line, text); 1 external calls (echo).


##### `_TurnDisplay._close_line`  (lines 479–483)

```
def _close_line(self) -> None
```

**Purpose**: Makes sure output is at a clean line boundary. This is a small terminal housekeeping step.

**Data flow**: It erases any meter, then, if streamed text left a line open, writes a newline and marks the line closed.

**Call relations**: Line output, activity output, and final close all call it before writing or ending, so the display stays readable.

*Call graph*: calls 1 internal fn (_erase_meter); called by 3 (activity, close, line); 1 external calls (echo).


##### `spend_cap`  (lines 487–488)

```
def spend_cap() -> None
```

**Purpose**: Defines the `spend-cap` command group. This groups commands for reading and changing cost limits.

**Data flow**: It does not process data itself. It acts as a parent command under which set and list subcommands are registered.

**Call relations**: The CLI framework enters this group before dispatching to `spend_cap_set` or `spend_cap_list`.


##### `spend_cap_set`  (lines 499–517)

```
def spend_cap_set(scope: str, subject_id: str, window_seconds: int, limit_micro_usd: int, on_breach: str) -> None
```

**Purpose**: Creates or updates a spending cap for the workspace, a member, or an agent. Spending caps limit how much model usage is allowed in a time window.

**Data flow**: It receives scope, optional subject id, time window, money limit in micro-dollars, and breach behavior. It validates the subject rules, loads config, writes the cap in the database, converts the amount for display, and prints the result.

**Call relations**: Operators call this command directly. It delegates database changes to `_write_spend_cap`.

*Call graph*: calls 1 internal fn (_write_spend_cap); 5 external calls (run, ClickException, echo, load_config, UUID).


##### `spend_cap_list`  (lines 521–531)

```
def spend_cap_list() -> None
```

**Purpose**: Shows the spending caps currently set for the workspace. This helps operators see what cost rules are active.

**Data flow**: It loads config, reads caps from the database, prints a no-caps message if empty, otherwise formats each cap with its scope, target, amount, time window, and breach behavior.

**Call relations**: This command calls `_read_spend_caps` to fetch the stored rules, then only formats them for the terminal.

*Call graph*: calls 1 internal fn (_read_spend_caps); 3 external calls (run, echo, load_config).


##### `_write_spend_cap`  (lines 534–588)

```
async def _write_spend_cap(config: Config, scope: str, subject: UUID | None, window_seconds: int, limit_micro_usd: int, on_breach: str) -> UUID
```

**Purpose**: Writes one spending cap row, updating an existing matching cap instead of creating a duplicate. This keeps each scope and window rule tidy.

**Data flow**: It opens the database, finds the workspace id, searches for a cap with the same workspace, scope, subject, and window. If found, it updates the limit and breach behavior; otherwise it inserts a new cap id. It returns the cap id and closes the database.

**Call relations**: `spend_cap_set` calls it after command-line validation. The database layer supplies the transaction boundary.

*Call graph*: called by 1 (spend_cap_set); 7 external calls (insert, select, update, dispose_db, init_db, workspace_tx, uuid4).


##### `_read_spend_caps`  (lines 591–617)

```
async def _read_spend_caps(config: Config) -> list[tuple[UUID, str, UUID | None, int, int, str]]
```

**Purpose**: Reads all spending caps for the current workspace. It returns data in a simple shape the CLI can print.

**Data flow**: It opens the database, finds the workspace id, selects cap fields for that workspace, orders them by scope, converts rows into tuples, and closes the database.

**Call relations**: `spend_cap_list` calls it, then handles all user-facing formatting.

*Call graph*: called by 1 (spend_cap_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `spend`  (lines 625–645)

```
def spend(window_seconds: int) -> None
```

**Purpose**: Prints a cost report for a recent time window. It shows total spend and breakdowns by dimension, member, agent, and price version.

**Data flow**: It receives a window length in seconds, loads config, reads the spend report, converts micro-dollars to dollars for display, and prints each rollup section.

**Call relations**: Operators call this command to inspect cost. It delegates the accounting read to `_read_spend`.

*Call graph*: calls 1 internal fn (_read_spend); 3 external calls (run, echo, load_config).


##### `_read_spend`  (lines 648–655)

```
async def _read_spend(config: Config, window_seconds: int) -> SpendReport
```

**Purpose**: Builds the spending report for the current workspace. It uses the accounting rollup code rather than doing the math in the CLI.

**Data flow**: It opens the database, finds the workspace id, asks `SpendRollup` to read totals for the requested window, returns the report, and closes the database.

**Call relations**: `spend` calls it and then prints the returned report in a human-readable layout.

*Call graph*: called by 1 (spend); 5 external calls (__init__, select, dispose_db, init_db, workspace_tx).


##### `transcript_reads`  (lines 663–676)

```
def transcript_reads(limit: int) -> None
```

**Purpose**: Lists recorded admin disclosures for reading another member’s private transcript. This supports auditability.

**Data flow**: It checks that the requested limit is positive, loads config, reads recent transcript-access records, and prints either a no-records message or each reader, subject, conversation, and time.

**Call relations**: Operators call this command for audit review. It gets the rows from `_read_transcript_accesses`.

*Call graph*: calls 1 internal fn (_read_transcript_accesses); 4 external calls (run, ClickException, echo, load_config).


##### `_read_transcript_accesses`  (lines 679–713)

```
async def _read_transcript_accesses(config: Config, limit: int) -> list[tuple[str, str, UUID, datetime]]
```

**Purpose**: Reads recent transcript-access audit records for the current workspace. It joins member records so emails can be shown instead of only ids.

**Data flow**: It opens the database, aliases the member table as reader and subject, finds the workspace id, selects recent access records with emails and timestamps, limits the result, returns tuples, and closes the database.

**Call relations**: `transcript_reads` calls it after validating the limit. The command then formats the audit records for the terminal.

*Call graph*: called by 1 (transcript_reads); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `grants`  (lines 717–729)

```
def grants() -> None
```

**Purpose**: Lists OAuth account grants available to agents. OAuth is a standard way to give an app limited access to an outside account.

**Data flow**: It loads config, reads grant summaries, prints `no grants` if empty, otherwise prints agent, provider, account id, shared/private status, and grant date.

**Call relations**: Operators call this to inspect connected accounts. It relies on `_read_grants` to gather the summaries.

*Call graph*: calls 1 internal fn (_read_grants); 3 external calls (run, echo, load_config).


##### `_read_grants`  (lines 732–739)

```
async def _read_grants(config: Config) -> tuple[GrantSummary, ...]
```

**Purpose**: Reads OAuth grant summaries for the workspace. It keeps the CLI separate from the details of how grants are summarized.

**Data flow**: It opens the database, finds the workspace id, asks the grants subsystem for summaries, returns them, and closes the database.

**Call relations**: `grants` calls it and then prints the summary rows.

*Call graph*: called by 1 (grants); 5 external calls (select, dispose_db, init_db, workspace_tx, workspace_grant_summaries).


##### `credential`  (lines 743–745)

```
def credential() -> None
```

**Purpose**: Defines the `credential` command group. These commands let operators inspect and fill secret slots declared by extensions.

**Data flow**: It does not read or write credentials itself. It provides the parent command for credential subcommands.

**Call relations**: The CLI framework enters this group before dispatching to `credential_set` or `credential_list`.


##### `credential_set`  (lines 750–773)

```
def credential_set(slot: str) -> None
```

**Purpose**: Stores one extension credential securely. It avoids putting the secret in command-line arguments, where shells and process lists might expose it.

**Data flow**: It loads config, checks that the slot is declared and allowed to be manually filled, reads the encryption key from the environment, reads the secret from a hidden prompt or standard input, rejects empty values, writes the encrypted credential, and prints confirmation.

**Call relations**: This command uses `_declared_slots` and `_fillable_slots` for safety checks, then delegates storage to `_write_credential`.

*Call graph*: calls 3 internal fn (_declared_slots, _fillable_slots, _write_credential); 5 external calls (run, ClickException, echo, prompt, load_config).


##### `credential_list`  (lines 777–787)

```
def credential_list() -> None
```

**Purpose**: Lists extension credential slots and whether each has a stored value. It never prints the secret values themselves.

**Data flow**: It loads config, reads declared slots from extension manifests, reads stored slot names from the database, and prints each slot with its owning extension and set/unset status.

**Call relations**: This command combines `_declared_slots` with `_read_stored_slots` to give an operator a safe inventory.

*Call graph*: calls 2 internal fn (_declared_slots, _read_stored_slots); 3 external calls (run, echo, load_config).


##### `_declared_slots`  (lines 790–795)

```
def _declared_slots(config: Config) -> dict[str, str]
```

**Purpose**: Finds all credential slots declared by installed extension manifests. A manifest is an extension’s description of what it needs.

**Data flow**: It loads manifests for the configured pack, converts each credential slot into a mapping from slot name to extension name, and turns manifest-loading failures into CLI errors.

**Call relations**: Credential listing and setting both call it so the CLI only accepts slots extensions actually declare.

*Call graph*: called by 2 (credential_list, credential_set); 2 external calls (ClickException, load_manifests).


##### `_fillable_slots`  (lines 798–807)

```
def _fillable_slots(config: Config) -> frozenset[str]
```

**Purpose**: Finds the subset of credential slots a person is allowed to type manually. Some slots are written by the deployment itself and should not be filled this way.

**Data flow**: It loads extension manifests, filters credentials to those marked as member-fillable, returns their names as a frozen set, and reports manifest errors as CLI errors.

**Call relations**: `credential_set` calls it after checking the slot exists, preventing an operator from entering a value for a deploy-written seal.

*Call graph*: called by 1 (credential_set); 2 external calls (ClickException, load_manifests).


##### `_write_credential`  (lines 810–817)

```
async def _write_credential(config: Config, key: str, slot: str, value: str) -> None
```

**Purpose**: Encrypts and stores one credential value for the workspace. Encryption at rest means the database does not hold the plain secret.

**Data flow**: It opens the database, finds the workspace id, creates a credential store using the provided Fernet key, stores the slot value for that workspace, and closes the database.

**Call relations**: `credential_set` calls it only after validating the slot and collecting the secret safely.

*Call graph*: called by 1 (credential_set); 6 external calls (__init__, Fernet, select, dispose_db, init_db, workspace_tx).


##### `_read_stored_slots`  (lines 820–834)

```
async def _read_stored_slots(config: Config) -> frozenset[str]
```

**Purpose**: Reads which credential slots currently have stored values. It returns names only, not secrets.

**Data flow**: It opens the database, finds the workspace id, selects credential slot names for that workspace, returns them as a frozen set, and closes the database.

**Call relations**: `credential_list` calls it so it can mark declared slots as set or unset.

*Call graph*: called by 1 (credential_list); 4 external calls (select, dispose_db, init_db, workspace_tx).


##### `ext`  (lines 838–839)

```
def ext() -> None
```

**Purpose**: Defines the `ext` command group for extension-store operations. Extensions add optional abilities to the deploy.

**Data flow**: It does not perform store work itself. It serves as the parent command for searching, installing, and removing extensions.

**Call relations**: The CLI framework enters this group before dispatching to extension subcommands.


##### `_store`  (lines 842–845)

```
def _store(config: Config) -> ExtensionStore
```

**Purpose**: Creates an extension-store object from config. It fails clearly if the extension store is not enabled.

**Data flow**: It receives config, checks for a configured store catalog, reads that catalog, finds the lockfile path, and returns an `ExtensionStore` connected to both.

**Call relations**: The extension search, install, and remove commands all call it before doing store-specific work.

*Call graph*: called by 3 (ext_install, ext_remove, ext_search); 4 external calls (__init__, ClickException, lockfile_path, read_catalog).


##### `ext_search`  (lines 850–863)

```
def ext_search(query: str) -> None
```

**Purpose**: Searches the configured extension store and shows what can be installed. It also marks extensions that are already installed or bundle-only.

**Data flow**: It loads config, creates the store, searches with the query text, prints a no-match message if empty, otherwise prints each listing with name, version, and state.

**Call relations**: This is the read-only extension command. It hands catalog and lockfile details to `_store`.

*Call graph*: calls 1 internal fn (_store); 2 external calls (echo, load_config).


##### `ext_install`  (lines 868–874)

```
def ext_install(name: str) -> None
```

**Purpose**: Pins an extension into the deploy’s lockfile. Pinning records the exact version and digest to load later.

**Data flow**: It loads config, creates the store, asks it to install the named extension, catches user-facing store errors, and prints the installed pin details.

**Call relations**: Operators call this before migrating and serving with a new extension. `_store` supplies the store object that performs the lockfile change.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `ext_remove`  (lines 879–885)

```
def ext_remove(name: str) -> None
```

**Purpose**: Removes an extension pin from the lockfile so the next server run stops loading it.

**Data flow**: It loads config, creates the store, asks it to remove the named extension, turns store errors into CLI errors, and prints confirmation.

**Call relations**: This is the uninstall path for extensions. It delegates catalog and lockfile handling through `_store`.

*Call graph*: calls 1 internal fn (_store); 3 external calls (ClickException, echo, load_config).


##### `_ufo_project_dir`  (lines 891–902)

```
def _ufo_project_dir() -> Path
```

**Purpose**: Finds the source project directory needed to build the UFO wheel for a bundle. A wheel is a packaged Python distribution file.

**Data flow**: It walks upward from this file, looks for a `pyproject.toml` whose project name is `ufo`, returns that directory when found, and raises a clear error if running from a wheel-only install without source.

**Call relations**: `bundle` calls it before running the external wheel build command, so bundling works from any current working directory.

*Call graph*: called by 1 (bundle); 3 external calls (ClickException, Path, loads).


##### `bundle`  (lines 909–925)

```
def bundle(out: Path) -> None
```

**Purpose**: Builds a deployable bundle containing pinned config, extension information, an image recipe, and the UFO wheel. This freezes the current deploy into an artifact others can run.

**Data flow**: It loads config, reads the extension catalog if configured, asks `Bundle` to build files under the output directory, runs `uv build` to create a wheel from the source project, verifies the wheel exists, and prints the bundle path and pinned extensions.

**Call relations**: Operators call this when preparing a deployable package. It coordinates config loading, bundle construction, project discovery through `_ufo_project_dir`, and the external build tool.

*Call graph*: calls 1 internal fn (_ufo_project_dir); 8 external calls (__init__, ClickException, echo, run, wheel_name, config_path, load_config, read_catalog).


### `control/src/ufo_control/main.py`

`entrypoint` · `startup and operator maintenance commands`

This file is the control panel for operating the hosted shared-workspace service. It uses Click, a command-line tool library, so operators can run clear commands such as starting the gateway, migrating the database, or minting an invite. Without this file, the service would still have many of its internal parts, but there would be no simple, official way to start them or run the key maintenance tasks.

At startup, the top-level command sets up ordinary logging and, if configured, forwards logs to an OTLP collector. OTLP is a standard way to send observability data, such as logs, to a monitoring platform. The gateway command then runs the web application with Uvicorn, the web server used for Python async apps.

The other commands are operator tools. One shapes the control database schema. One grants an invitation to a waitlist object and sends the invite email. One retries a failed Slack Connect delivery after its underlying problem has been fixed. One creates the shared database role and row-level security policies, which are database rules that limit which rows each role may see or change.

A recurring pattern is that the visible command is short and user-friendly, while a helper function does the asynchronous database or email work. This keeps the command-line experience simple while still allowing the service to use async database connections safely.

#### Function details

##### `main`  (lines 38–41)

```
def main() -> None
```

**Purpose**: Defines the main command group for operating the hosted service. It also sets up basic process logging so every command produces consistent, readable log messages.

**Data flow**: It reads the OTLP logging endpoint from the environment. It configures standard logging, then passes the optional endpoint to `_export_logs`; after that, Click uses this command group as the parent for the subcommands in this file.

**Call relations**: This is the first function Click enters when the command-line program starts. It calls `_export_logs` during setup so any later command, such as `gateway` or `invite`, can have its logs sent to the configured monitoring system.

*Call graph*: calls 1 internal fn (_export_logs); 1 external calls (basicConfig).


##### `_export_logs`  (lines 44–54)

```
def _export_logs(otlp_endpoint: str | None) -> None
```

**Purpose**: Turns on remote log shipping when an OTLP endpoint is configured. If no endpoint is provided, it leaves logging as normal console output only.

**Data flow**: It receives either a logging collector URL or `None`. If the value is `None`, it stops immediately; otherwise it builds an OpenTelemetry logger provider, attaches a batch exporter that sends logs to the collector’s `/v1/logs` path, and asks `_install_root_handler` to connect Python logging to that provider.

**Call relations**: `main` calls this once at command startup. When remote logging is enabled, this function prepares the OpenTelemetry pieces and then hands the final wiring step to `_install_root_handler`.

*Call graph*: calls 1 internal fn (_install_root_handler); called by 1 (main); 4 external calls (OTLPLogExporter, LoggerProvider, BatchLogRecordProcessor, create).


##### `_install_root_handler`  (lines 57–62)

```
def _install_root_handler(logger_provider: LoggerProvider) -> None
```

**Purpose**: Connects Python’s normal logging system to OpenTelemetry log export. It deliberately skips logs from OpenTelemetry itself so a logging export failure cannot create a feedback loop of more logging errors.

**Data flow**: It receives an OpenTelemetry logger provider. It creates a logging handler that forwards records to that provider, adds a filter that rejects logger names starting with `opentelemetry`, and attaches the handler to the root logger, which is the shared parent logger for the process.

**Call relations**: `_export_logs` calls this after building the remote log exporter. From then on, later commands write logs as usual, and this handler quietly forwards eligible records to the observability pipeline.

*Call graph*: called by 1 (_export_logs); 2 external calls (getLogger, LoggingHandler).


##### `gateway`  (lines 66–71)

```
def gateway() -> None
```

**Purpose**: Starts the hosted web gateway. This is the command an operator uses to serve onboarding, fleet counts, and the terminal client over HTTP.

**Data flow**: It reads the desired port from the `UFO_GATEWAY_PORT` environment variable, or falls back to port 8080. It then starts Uvicorn on host `0.0.0.0`, which means the server listens on all network interfaces, and points it at the `ufo_control.gateway:app` web application.

**Call relations**: This is a Click subcommand under `main`. Unlike the maintenance commands, it does not call an internal helper; it directly hands control to Uvicorn, which runs the web service until the process is stopped.

*Call graph*: 1 external calls (run).


##### `migrate`  (lines 75–78)

```
def migrate() -> None
```

**Purpose**: Updates the control database schema to the expected shape. Operators use this when deploying or upgrading so the database has the tables and structures the gateway expects.

**Data flow**: It asks for the database owner connection string, runs the asynchronous schema-shaping routine to completion, and then prints `control schema at head` to confirm success.

**Call relations**: This is a Click subcommand under `main`. It bridges the synchronous command-line world to async database work by using `asyncio.run`, then delegates the actual schema work to `shape_control_schema`.

*Call graph*: 4 external calls (run, echo, owner_dsn, shape_control_schema).


##### `invite`  (lines 84–91)

```
def invite(object_number: int, email: str) -> None
```

**Purpose**: Creates one workspace invitation for a waitlist object and emails it to the requested address. It also turns invitation and work-email validation failures into friendly command-line errors.

**Data flow**: It receives an object number and an email address from the command line. It runs `_mint_invite`, catches expected invite or email errors, then prints the granted object number, recipient email, and expiration time in UTC if everything succeeds.

**Call relations**: This is the operator-facing command for issuing invites. It keeps the user interaction and error display near the command line, while `_mint_invite` performs the database, validation, and email-sending steps.

*Call graph*: calls 1 internal fn (_mint_invite); 3 external calls (run, ClickException, echo).


##### `_mint_invite`  (lines 94–116)

```
async def _mint_invite(object_number: int, email: str) -> MintedInvite
```

**Purpose**: Does the real work of granting an invite and sending the invitation email. It is careful to check email configuration before spending the one live grant tied to a waitlist object.

**Data flow**: It receives an object number and email address. It reads the public host name and database owner connection string, verifies the control schema exists, builds the email sender, opens a small database connection pool, mints the invite in the database, closes the pool, builds the email subject and body, and sends the message. It returns the minted invite; if email sending fails after the grant is created, it raises a command-line error explaining that the grant still stands.

**Call relations**: `invite` calls this helper when an operator runs the invite command. This function coordinates several subsystems: schema checking, invite-code creation through `InviteCodes`, email body creation, and actual sending through the configured mail sender.

*Call graph*: called by 1 (invite); 8 external calls (__init__, create_pool, ClickException, email_sender_from_env, invite_email, public_apex_host, owner_dsn, require_control_schema).


##### `slack_connect_retry`  (lines 121–127)

```
def slack_connect_retry(onboard_claim_id: uuid.UUID) -> None
```

**Purpose**: Lets an operator retry a failed Slack Connect delivery for a specific onboarding claim. This is useful after fixing the reason the first delivery failed.

**Data flow**: It receives an onboarding claim UUID from the command line. It runs `_rearm_slack_connect`; if no failed delivery is found, it raises a friendly command-line error. If one is found, it prints the claim ID and the time since the delivery had been failed.

**Call relations**: This is the visible Click command for Slack Connect retry work. It delegates the database change to `_rearm_slack_connect`, then turns that result into either a clear success message or a clear failure message.

*Call graph*: calls 1 internal fn (_rearm_slack_connect); 3 external calls (run, ClickException, echo).


##### `_rearm_slack_connect`  (lines 130–137)

```
async def _rearm_slack_connect(onboard_claim_id: uuid.UUID) -> datetime | None
```

**Purpose**: Finds and re-arms one failed Slack Connect delivery in the database. Re-arming means marking it so the system can try sending it again.

**Data flow**: It receives an onboarding claim UUID. It reads the owner database connection string, verifies the control schema is present, opens a one-connection async database pool, asks `rearm_failed_delivery` to perform the retry preparation, closes the pool, and returns the original failure time or `None` if no matching failed delivery exists.

**Call relations**: `slack_connect_retry` calls this helper from the command line. This function provides the database setup and cleanup around the more focused Slack Connect operation performed by `rearm_failed_delivery`.

*Call graph*: called by 1 (slack_connect_retry); 4 external calls (create_pool, rearm_failed_delivery, owner_dsn, require_control_schema).


##### `rls_bootstrap`  (lines 141–144)

```
def rls_bootstrap() -> None
```

**Purpose**: Runs the setup needed for database row-level security. Row-level security means the database itself enforces which rows a role can access.

**Data flow**: It starts the asynchronous `_bootstrap` helper and waits for it to finish. When the database role and policies are in place, it prints `rls policies at head`.

**Call relations**: This is the operator-facing Click command for security bootstrap. It uses `_bootstrap` to do the actual database work, keeping the command itself simple and focused on reporting success.

*Call graph*: calls 1 internal fn (_bootstrap); 2 external calls (run, echo).


##### `_bootstrap`  (lines 147–150)

```
async def _bootstrap() -> None
```

**Purpose**: Creates or updates the database pieces needed for serving workspaces safely: shared policies and the serve role. These are foundational security settings for the hosted service.

**Data flow**: It reads the owner database connection string. It applies the row-level security policies, then ensures the serve role exists and is ready to use. It does not return a value; the important result is the changed database state.

**Call relations**: `rls_bootstrap` calls this helper when an operator runs the bootstrap command. It delegates the two concrete database tasks to `bootstrap_policies` and `ensure_serve_role` in sequence.

*Call graph*: called by 1 (rls_bootstrap); 3 external calls (bootstrap_policies, ensure_serve_role, owner_dsn).


### Bootstrap workflows
These files handle deployable bundle creation and safe first-run workspace initialization.

### `core/src/ufo/bundle.py`

`orchestration` · `bundle creation`

This file supports the `ufoctl bundle` command. Its job is to turn the current UFO setup into a portable artifact, much like packing a lunchbox with the exact food and utensils needed instead of hoping they exist at the destination.

The bundle contains three main things. First, it copies the current `ufo.toml` configuration into the output folder. Second, it writes a `ufo.lock` file that pins the exact extensions to use. A lockfile is a record of chosen package or extension versions and checksums, so later runs can verify they are using the same pieces. Third, it writes a Dockerfile, which is a recipe for building a container image.

The important safety step is extension pinning. If there is already a lockfile, the bundle keeps those pinned extensions. If there is no lockfile, it pins every extension currently discovered as installed. If an extension catalog is available, it also includes entries marked as disabled, because these are “bundle-only”: they are installed into the artifact at bundle time rather than fetched later at runtime. Each extension is pinned through the store, which checks that the needed extension is actually installed and has the expected digest. Without this file, deployments would be easier to drift: one machine might start with a different extension set than another.

#### Function details

##### `wheel_name`  (lines 25–28)

```
def wheel_name() -> str
```

**Purpose**: This function builds the filename of the UFO Python wheel that the Dockerfile will install. A wheel is Python’s packaged install file, and here it represents the local UFO distribution rather than something downloaded from a public package index.

**Data flow**: It reads the current UFO version from the extension store helper. It then places that version into the expected wheel filename format, producing a string such as a package file name that the Docker build can copy and install.

**Call relations**: It is used by `Bundle._dockerfile` when writing the Docker build recipe. That recipe needs the exact filename twice: once to copy the wheel into the image and once to install it with `pip`.

*Call graph*: called by 1 (_dockerfile); 1 external calls (ufo_version).


##### `Bundle.build`  (lines 49–62)

```
def build(self) -> BundleResult
```

**Purpose**: This is the main action for creating the bundle folder. It gathers the pinned extensions, copies the config, writes a lockfile, writes a Dockerfile, and returns a summary of what it created.

**Data flow**: It starts with the bundle’s input paths: the source config file, the output directory, and the optional extension catalog stored on the `Bundle` object. It asks `_pins` for the exact extension pins, creates the output directory if needed, copies the config text into `ufo.toml`, writes a new JSON lockfile containing the UFO version and extension pins, and writes the Dockerfile text from `_dockerfile`. It returns a `BundleResult` containing the output folder, the created file paths, and the pins used.

**Call relations**: This is the coordinating method that a higher-level bundle command would call. It delegates extension selection to `Bundle._pins`, Dockerfile text generation to `Bundle._dockerfile`, and uses `Lockfile` plus `ufo_version` to record a reproducible locked setup.

*Call graph*: calls 2 internal fn (_dockerfile, _pins); 3 external calls (__init__, __init__, ufo_version).


##### `Bundle._pins`  (lines 64–78)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: This private helper decides which extensions must be frozen into the bundle. It combines what the current deployment already uses with any catalog entries that are meant to be included only when bundling.

**Data flow**: It first discovers installed extensions and checks the usual lockfile path. If a lockfile already exists, it uses the extension names recorded there as the base list. If no lockfile exists, it uses all discovered installed extensions. If a catalog was provided, it adds any catalog extension marked as disabled, because those are treated as bundle-only additions. It removes duplicate names while keeping order, then turns each name into a verified extension pin using `pin_for`. The result is a tuple of exact extension pins.

**Call relations**: It is called by `Bundle.build` before any files are written. It relies on loader helpers to inspect the current installed and locked state, and on the store helper `pin_for` to turn extension names into checked, reproducible pins.

*Call graph*: called by 1 (build); 4 external calls (discovered, lockfile_path, read_lockfile, pin_for).


##### `Bundle._dockerfile`  (lines 80–94)

```
def _dockerfile(self) -> str
```

**Purpose**: This private helper writes the Dockerfile recipe for running the bundled UFO deployment. The Dockerfile installs the local UFO wheel, copies in the frozen config and lockfile, and sets the container to run `ufoctl serve` by default.

**Data flow**: It uses fixed bundle filenames and asks `wheel_name` for the expected wheel package filename. It then assembles a multi-line Dockerfile string: start from a Python base image, work in `/app`, set environment variables pointing UFO at the bundled config and lockfile, copy and install the wheel, copy the config and lockfile, and define the startup command. The output is plain text ready to be written to `Dockerfile`.

**Call relations**: It is called by `Bundle.build` during bundle creation. It hands back the container build recipe, while `Bundle.build` is responsible for writing that recipe into the output directory.

*Call graph*: calls 1 internal fn (wheel_name); called by 1 (build).


### `core/src/ufo/onboarding.py`

`orchestration` · `startup / first-run initialization`

This file is the “new office setup” checklist for a fresh UFO installation. Before anyone can use the system, UFO needs a durable workspace, an initial administrator, and a main agent to talk to. It also needs to know that required API keys or credential storage are available, so the first real use does not fail immediately.

The flow is deliberately split into two parts. First, the core system is created: it checks that the chosen model has its required environment variable set, checks that extension setup will have credential support if needed, then opens a database transaction and creates the workspace, admin member, and main agent together. A database transaction means “all of this succeeds as one unit, or none of it is kept,” like signing all pages of a contract before filing it. If there is already any member in the database, it raises `AlreadyInitialized` instead of making a second first workspace.

After the core exists, extension onboarding steps run. Each extension gets its own scoped context, meaning it only sees the credential slots it declared. If an extension setup step fails, the error is logged and UFO continues with the next step, so one add-on cannot ruin the core installation.

#### Function details

##### `run_onboarding_steps`  (lines 46–74)

```
async def run_onboarding_steps(manifests: tuple[Manifest, ...], workspace_id: UUID, credentials: CredentialStore | None) -> None
```

**Purpose**: Runs the setup steps supplied by installed extensions after the main workspace has already been created. It gives each extension its own limited setup context and makes sure one failing extension does not stop the rest.

**Data flow**: It receives the installed extension manifests, the new workspace ID, and an optional credential store. It enters that workspace’s context, skips extensions with no setup steps, and skips step execution if credentials are required but unavailable. For each step it builds the extension-specific context, calls the step, and logs failures instead of returning an error. It does not return data; its effect is any setup work the extension performs.

**Call relations**: After `Onboarding.run` has created the core workspace, `Onboarding.run_steps` calls this function to perform optional add-on setup. Inside, it uses the workspace context so extension code runs as if it belongs to the new workspace, asks `context_for` for the extension’s scoped handle, and uses logging when a step must be skipped or fails.

*Call graph*: called by 1 (run_steps); 3 external calls (context_for, log, ws).


##### `Onboarding.run`  (lines 89–92)

```
async def run(self) -> Onboarded
```

**Purpose**: Runs the complete first-run setup from start to finish. It creates the core workspace first, then runs extension setup, and finally reports which workspace and member were created.

**Data flow**: It starts with the information stored on the `Onboarding` object: configuration, admin email, chosen model, credential store, and extension manifests. It calls `create` to produce an `Onboarded` record, passes that record to `run_steps`, and returns the same `Onboarded` record to the caller.

**Call relations**: This is the high-level path used by the initialization command or surface. It delegates the durable core creation to `Onboarding.create`, then delegates extension setup to `Onboarding.run_steps`, keeping the overall order clear and safe.

*Call graph*: calls 2 internal fn (create, run_steps).


##### `Onboarding.create`  (lines 94–100)

```
async def create(self) -> Onboarded
```

**Purpose**: Creates only the core UFO installation pieces: model readiness, credential readiness, workspace, initial admin, and main agent. It intentionally avoids depending on extension setup so the basic workspace can be established before optional add-ons run.

**Data flow**: It reads the selected model, configuration, credentials, manifests, and admin email from the `Onboarding` object. It first checks for the model key, then checks whether extension steps need credential support, then writes the workspace records to the database. It returns an `Onboarded` value containing the new workspace ID and admin member ID.

**Call relations**: `Onboarding.run` calls this before any extension setup happens. This method coordinates three smaller checks/actions: `_require_model_key`, `_require_credentials_for_steps`, and `_create_workspace`.

*Call graph*: calls 3 internal fn (_create_workspace, _require_credentials_for_steps, _require_model_key); called by 1 (run).


##### `Onboarding.run_steps`  (lines 102–103)

```
async def run_steps(self, onboarded: Onboarded) -> None
```

**Purpose**: Runs extension onboarding for a workspace that has already been created. It is a small bridge between the `Onboarding` object and the standalone extension-step runner.

**Data flow**: It takes the `Onboarded` result, reads the workspace ID from it, and combines that with the object’s manifests and credential store. It passes those values to `run_onboarding_steps`. It returns nothing directly; any useful work happens inside the extension steps.

**Call relations**: `Onboarding.run` calls this after `Onboarding.create` succeeds. It hands off to `run_onboarding_steps`, which contains the actual loop over installed extensions.

*Call graph*: calls 1 internal fn (run_onboarding_steps); called by 1 (run).


##### `Onboarding._require_credentials_for_steps`  (lines 105–116)

```
def _require_credentials_for_steps(self) -> None
```

**Purpose**: Checks, before touching the database, that extension setup can store secrets if any installed extension has onboarding steps. This avoids creating a workspace and only then discovering that required credential support is missing.

**Data flow**: It reads the credential store and extension manifests from the `Onboarding` object. If a credential store exists, it allows setup to continue. If no credential store exists but at least one extension has onboarding steps, it raises an error telling the operator which environment variable must be set. It returns nothing when the check passes.

**Call relations**: `Onboarding.create` calls this before `_create_workspace`. It sits alongside the model-key check as an early safety gate, so failed prerequisites leave no half-initialized database behind.

*Call graph*: called by 1 (create).


##### `Onboarding._require_model_key`  (lines 118–125)

```
def _require_model_key(self) -> None
```

**Purpose**: Checks whether the selected model needs an environment variable, such as an API key, and stops initialization if that variable is missing. This prevents creating a workspace that cannot take its first assistant turn.

**Data flow**: It asks `_model_key_env` for the name of the required environment variable, if one is known. If there is no required variable, it allows setup to continue. If there is a required variable but the operating system environment does not contain a value for it, it raises an error. It returns nothing when the check passes.

**Call relations**: `Onboarding.create` calls this as the first readiness check. It relies on `_model_key_env` to understand the configured model provider, then uses the process environment as the source of truth for whether the key is present.

*Call graph*: calls 1 internal fn (_model_key_env); called by 1 (create).


##### `Onboarding._model_key_env`  (lines 127–130)

```
def _model_key_env(self) -> str | None
```

**Purpose**: Finds the environment variable name that should contain the key for the selected model, when UFO can know that name ahead of time. Some extension-provided models resolve their own keys later, so this can also return no name.

**Data flow**: It reads the configuration, installed manifests, and chosen model from the `Onboarding` object. It builds or consults the model registry and asks it which environment variable belongs to that model. It returns the variable name as text, or `None` if there is no eager check to perform.

**Call relations**: `Onboarding._require_model_key` calls this to decide what to check in the operating system environment. The model registry is the outside helper that knows about built-in and extension-contributed model providers.

*Call graph*: called by 1 (_require_model_key); 1 external calls (model_registry).


##### `Onboarding._create_workspace`  (lines 132–156)

```
async def _create_workspace(self) -> Onboarded
```

**Purpose**: Writes the first durable UFO records into the database: the workspace, the initial admin member, and the main agent. It also enforces that this can only happen once for an empty installation.

**Data flow**: It opens a workspace database transaction, then looks for any existing member. If a member already exists, it raises `AlreadyInitialized`. Otherwise it creates new unique IDs, inserts a workspace row, calls `create_member` to add the admin user, inserts the main agent with the chosen model and default prompt, and returns an `Onboarded` record containing the new workspace and member IDs.

**Call relations**: `Onboarding.create` calls this only after prerequisite checks pass. It relies on the database transaction helper to keep the writes together, on `create_member` to create the first administrator correctly, and on SQLAlchemy database commands to insert the workspace and agent records.

*Call graph*: called by 1 (create); 7 external calls (__init__, __init__, insert, select, workspace_tx, create_member, uuid4).


### Runtime assembly
These files choose the sandbox backend and assemble the configured UFO service into a running process.

### `core/src/ufo/sandbox/select.py`

`orchestration` · `startup/config load`

A sandbox is an isolated place where work can run without freely touching the host system. This file is the small but important gatekeeper that decides which sandbox backend is active for this process. Without it, the system could accidentally pick the wrong backend, silently ignore an unknown backend name, or run a remote sandbox without the safe network proxy it needs.

The built-in choice is called `local`, which uses `LocalCarrier`. Extensions can add more choices, such as a Docker-based runner or a remote runner. The file collects all these choices into one name-to-factory map. A factory is just a callable that builds the carrier when needed. If two carriers try to claim the same backend name, the code stops immediately, because that would make the configuration ambiguous.

After building the list of available carriers, it looks up the backend named in `[sandbox] backend`. If no carrier registered that name, it raises a clear error. It also tracks which carriers run “off-cluster,” meaning outside the local process or local environment. Those remote carriers need an externally reachable HTTPS proxy URL so sandbox network traffic can be routed safely, with credentials injected and access controlled. If that URL is missing or not HTTPS, the file refuses to continue. In the end, it returns the chosen carrier and a true-or-false flag saying whether it is off-cluster.

#### Function details

##### `select_carrier`  (lines 13–54)

```
def select_carrier(config: Config, manifests: tuple[Manifest, ...]) -> tuple[Carrier, bool]
```

**Purpose**: This function chooses exactly one sandbox backend from the built-in backend and any extension-provided backends. It also checks that remote sandbox backends have a safe public HTTPS proxy URL before they are allowed to run.

**Data flow**: It receives the loaded configuration and a tuple of extension manifests. It starts with the built-in `local` carrier, adds each carrier advertised by the manifests, and records which ones are remote or off-cluster. It then reads `config.sandbox.backend`, finds the matching carrier factory, validates any needed remote proxy URL, builds the carrier, and returns it together with a boolean saying whether the selected backend is off-cluster. If the backend name is unknown, duplicated, missing required remote settings, or configured with a non-HTTPS proxy URL, it raises an error instead of returning.

**Call relations**: This function is used when the runtime needs to turn configuration into the actual sandbox carrier it will hold and use. If the configured backend name is not registered, it creates a `NotRegisteredError` to explain the available choices. If the chosen backend is remote, it calls `urllib.parse.urlparse` to inspect the public proxy URL and make sure it is a real HTTPS URL before handing the carrier back to the caller.

*Call graph*: 2 external calls (__init__, urlparse).


### `core/src/ufo/serve.py`

`entrypoint` · `startup, request handling, background work, shutdown`

Think of this file as the control room for a multi-tenant service: one server process serves many workspaces, but each request and background job must be carefully tied to the right workspace so data does not leak across boundaries. Without this file, the pieces might exist, but nothing would reliably start in the right order or share the same runtime objects.

At startup, `run` loads configuration, opens database access, verifies credentials, loads extensions, starts a heartbeat for this service instance, builds storage, model, search, connector, browser, sandbox, and memory services, and then registers background jobs. It also creates the FastAPI web app, mounts built-in routes, extension routes, and shared surface routes, and finally starts Uvicorn, the web server.

A major theme is “fail early.” If two extensions claim the same backend name, if a required provider is missing, if a public OAuth callback URL is not really public, or if credential encryption is unavailable, startup stops with a clear error instead of failing later during a user action.

Another major theme is workspace safety. The `WorkspaceScopeBoundary` middleware clears any leftover workspace setting at the start and end of each HTTP request. Surface endpoints identify the caller’s workspace before touching data, like checking a badge before opening a filing cabinet. Background pollers and jobs also re-bind each operation to the workspace they are acting for.

#### Function details

##### `_assert_no_reserved_routes`  (lines 135–151)

```
def _assert_no_reserved_routes(app: FastAPI) -> None
```

**Purpose**: Checks that this service has not mounted web routes under URL prefixes reserved for the onboarding gateway. This prevents a quiet routing conflict where a route appears to exist here but is actually hidden by the front-door proxy.

**Data flow**: It reads the FastAPI app’s registered routes → looks for paths starting with reserved prefixes like `/login`, `/v1/onboard`, or `/ufo` → either returns normally or raises an error listing the conflicting routes.

**Call relations**: Near the end of `run`, after all built-in, extension, and surface routes have been mounted, this function is called as a final safety check before the web server starts.

*Call graph*: called by 1 (run).


##### `run`  (lines 154–299)

```
def run() -> None
```

**Purpose**: Starts the shared UFO fleet process. It is the top-level boot sequence that turns configuration and extension manifests into a live web service with workers, routes, sandboxes, credentials, and background jobs.

**Data flow**: It reads configuration and environment variables → initializes observability, databases, credentials, extensions, blob storage, runtime services, DBOS workers, web routes, and background tasks → starts Uvicorn to serve HTTP traffic → on exit, shuts down workers and the heartbeat safely.

**Call relations**: This is the main conductor. It calls most helper functions in this file to select providers, mount routes, launch jobs, build connector and connect-flow support, start the sandbox proxy, and validate the final web app before handing control to Uvicorn.

*Call graph*: calls 17 internal fn (from_env, _assert_no_reserved_routes, _connect_flow, _connector_registry, _launch_jobs, _mount_ext_routes, _mount_shared_surfaces, _one_shot, _proxy_endpoint, _select_cdp_provider (+7 more)); 43 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


##### `_one_shot`  (lines 302–314)

```
def _one_shot(coro: Coroutine[Any, Any, T]) -> T
```

**Purpose**: Runs one asynchronous setup or teardown action on a temporary event loop, then cleans up database engines tied to that loop. This avoids leaving database connections attached to a loop that is about to disappear.

**Data flow**: It receives a coroutine, meaning an async operation waiting to be run → wraps it in a cleanup step → runs it to completion with `asyncio.run` → returns the operation’s result after disposing loop-owned database resources.

**Call relations**: `run` uses this for startup database checks and instance-seat recording. `_stop_executor` uses it during shutdown to retire the heartbeat seat safely.

*Call graph*: called by 2 (_stop_executor, run); 1 external calls (run).


##### `_one_shot.step`  (lines 308–312)

```
async def step() -> T
```

**Purpose**: Performs the actual wrapped async operation and guarantees cleanup afterward. It exists so `_one_shot` can always dispose event-loop database engines, whether the operation succeeds or fails.

**Data flow**: It awaits the original coroutine → captures and returns its result if successful → always calls database engine disposal before the temporary loop closes.

**Call relations**: This inner helper is created and run only by `_one_shot`. It hands cleanup to `dispose_loop_engines` so short-lived startup loops do not strand pooled database connections.

*Call graph*: 1 external calls (dispose_loop_engines).


##### `_stop_executor`  (lines 317–331)

```
def _stop_executor(dbos: DBOS, heartbeat: Heartbeat, graceful_shutdown_seconds: int) -> None
```

**Purpose**: Shuts down DBOS workflow execution without accidentally letting another process run the same still-active work. It only retires this service instance’s seat if no workflows are still active locally.

**Data flow**: It receives the DBOS executor, heartbeat, and drain timeout → asks DBOS to stop and wait for work to finish → checks whether workflows are still active → either keeps the seat alive and logs that fact, or retires the heartbeat seat.

**Call relations**: `run` calls this in its `finally` block after Uvicorn stops. It uses `_one_shot` to run the async heartbeat retirement step and logs when active workflows prevent retirement.

*Call graph*: calls 2 internal fn (retire, _one_shot); called by 1 (run); 2 external calls (destroy, log).


##### `_shared_owner_dsn`  (lines 334–348)

```
def _shared_owner_dsn(config: Config) -> str
```

**Purpose**: Finds the database connection string used for owner-level cross-workspace reads. This is needed for jobs that must first list work across all workspaces before doing each piece under the correct workspace.

**Data flow**: It reads the owner database URL from an environment variable or config → fails if neither is set → normalizes a plain PostgreSQL URL to the async driver form → returns the usable connection string.

**Call relations**: `run` calls this before initializing the owner database connection. Its result allows shared background sweeps to enumerate workspaces safely before rebinding each operation.

*Call graph*: called by 1 (run).


##### `_launch_jobs`  (lines 351–395)

```
def _launch_jobs(runtime: Runtime, sync_driver: SyncDriver, page_feed: CorePageFeed) -> None
```

**Purpose**: Registers and starts the background jobs for syncing sources, dispatching turns, and reacting to page changes. These jobs keep the system moving even when no HTTP request is currently active.

**Data flow**: It receives the runtime, sync driver, and page feed → builds admission helpers, page-change runners, core job bindings, and extension job bindings → creates a `JobRunner` → launches the jobs with access to shared runtime services.

**Call relations**: `run` calls this after the runtime and DBOS client are ready. It hands work to job classes such as `TurnDispatcher`, `PageChangeRunner`, and `JobRunner`, which take over scheduled and queued background execution.

*Call graph*: called by 1 (run); 7 external calls (__init__, __init__, __init__, __init__, durable_surfaces, bindings_from, core_jobs).


##### `_launch_jobs.invoker_for`  (lines 365–366)

```
def invoker_for(workspace_id: UUID) -> AdmissionInvoker
```

**Purpose**: Creates a workspace-specific admission invoker for background jobs. An admission invoker is the object that lets a job submit or re-admit work for one chosen workspace.

**Data flow**: It receives a workspace ID → combines it with the shared admission object → returns an `AdmissionInvoker` tied to that workspace.

**Call relations**: This small factory is passed into job runners from `_launch_jobs`. When a job needs to act for a particular workspace, the runner calls this factory to get the correctly scoped invoker.

*Call graph*: 1 external calls (__init__).


##### `_source_backends`  (lines 398–412)

```
def _source_backends(manifests: tuple[Manifest, ...]) -> dict[str, SourceBackend]
```

**Purpose**: Builds the list of source-sync backends available to the sync driver. A source backend is the code that knows how to read from a specific kind of source, such as the built-in folder source or an extension-provided source.

**Data flow**: It starts with the built-in folder backend → walks all extension manifests → gives each extension a credential reader limited to its declared credential slots → adds each declared source backend → raises an error if two backends use the same name.

**Call relations**: `run` calls this while building the `SyncDriver`. The returned map lets the sync system resolve a stored source row to exactly one implementation.

*Call graph*: called by 1 (run); 2 external calls (__init__, __init__).


##### `_source_identity_resolvers`  (lines 415–455)

```
def _source_identity_resolvers(manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: BlobStore) -> dict[str, SourceIdentityResolver]
```

**Purpose**: Builds helper functions that can ask a surface, such as an extension UI endpoint, who the current user is in that external source. This is used to connect synced source data with the right external identity.

**Data flow**: It scans extension surfaces that declare a `self_user_id` function → creates one resolver per surface name → each resolver later receives a workspace ID and may read allowed credentials and blob storage → returns a map from surface name to resolver.

**Call relations**: `run` passes these resolvers into the `SyncDriver`. The nested `resolve` function is what the sync system later calls when it needs a source identity for a workspace.

*Call graph*: called by 1 (run).


##### `_source_identity_resolvers.resolve`  (lines 429–452)

```
async def resolve(workspace_id: UUID, handler=surface.self_user_id, slots=declared, store=credentials) -> str | None
```

**Purpose**: Runs one surface’s identity lookup for one workspace. It wraps the surface-provided handler with a safe context containing only the blob store and a guarded credential reader.

**Data flow**: It receives a workspace ID → prepares a `SurfaceIdentityContext` with a credential callback → calls the surface’s identity handler → returns that handler’s user ID string or `None`.

**Call relations**: This function is produced by `_source_identity_resolvers` and stored in the resolver map used by the sync driver. It hands credential reads to its nested `credential` helper.

*Call graph*: 1 external calls (__init__).


##### `_source_identity_resolvers.resolve.credential`  (lines 435–444)

```
async def credential(credential_slot: str) -> str
```

**Purpose**: Safely reads one credential for a surface identity lookup. It enforces that the surface can only read credential slots its extension declared.

**Data flow**: It receives a credential slot name → checks that the slot is declared by the extension → checks that a credential store exists → reads the credential for the current workspace → returns the secret value.

**Call relations**: This nested helper is used inside `_source_identity_resolvers.resolve` when an extension’s identity handler needs a credential. It prevents accidental or unauthorized reads of unrelated credential slots.


##### `_select_hub`  (lines 458–476)

```
def _select_hub(config: Config, manifests: tuple[Manifest, ...]) -> Hub
```

**Purpose**: Chooses the live-message hub backend for this deployment. The hub is the process-wide channel used to publish and tail live updates, with an in-process default unless an extension provides another backend.

**Data flow**: It starts with the built-in in-process hub builder → adds hub builders declared by extensions → rejects duplicate backend names → looks up the backend selected in config → returns the built hub or raises an error if missing.

**Call relations**: `run` calls this during startup and stores the hub in both the runtime and FastAPI app state. Later, shared surfaces use it through `HubTailer` to stream live updates.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_cdp_provider`  (lines 479–507)

```
def _select_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> CdpProvider | None
```

**Purpose**: Chooses the browser-control provider, if one is configured and registered. CDP means Chrome DevTools Protocol, a way for software to drive a browser; this provider lets sandboxes or tools browse when an extension supplies support.

**Data flow**: It scans extension manifests for CDP provider specs → rejects duplicate backend names → looks up the configured backend → returns `None` if none is registered for that name → otherwise builds the provider with a credential reader limited to that extension’s declared slots.

**Call relations**: `run` calls this to put the selected provider into the runtime. `_require_cdp_provider` also calls it during required-feature validation so browser extensions fail at startup if no provider is available.

*Call graph*: called by 2 (_require_cdp_provider, run); 1 external calls (__init__).


##### `_validate_requires`  (lines 510–534)

```
def _validate_requires(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Checks every extension’s declared required seams before the service starts. A seam is a plug-in point, such as search or browser control, that an extension depends on.

**Data flow**: It reads each manifest’s `requires` list → looks up the matching checker in `_REQUIRED_SEAM_CHECKS` → runs that checker → wraps any failure with a message naming the extension and unavailable seam.

**Call relations**: `run` calls this after extension tools are validated and before building the rest of the runtime. It delegates actual checks to functions such as `_require_cdp_provider`, `_require_search_provider`, and `_require_memory_search`.

*Call graph*: called by 1 (run).


##### `_require_cdp_provider`  (lines 537–551)

```
def _require_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that a browser-control provider is available when an active extension requires one. It turns a missing optional browser backend into a clear startup error.

**Data flow**: It receives config, manifests, and credentials → calls `_select_cdp_provider` → raises an error if the result is `None` → otherwise returns successfully.

**Call relations**: _validate_requires calls this when an extension declares the `cdp_providers` seam. It relies on `_select_cdp_provider` to do the actual provider lookup and credential checks.

*Call graph*: calls 1 internal fn (_select_cdp_provider).


##### `_select_search_provider`  (lines 554–589)

```
def _select_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> SearchProvider | None
```

**Purpose**: Chooses the research search provider for this deployment. A search provider is the backend that external research tools use to search the web or another index.

**Data flow**: It scans extension manifests for search provider specs → rejects duplicate backend names → returns `None` if config leaves search unset → otherwise verifies the configured provider exists and credentials are available → builds and returns the provider.

**Call relations**: `run` calls this to put the provider into the runtime. `_require_search_provider` calls it when an extension requires search, so missing or misconfigured search fails during startup.

*Call graph*: called by 2 (_require_search_provider, run); 2 external calls (__init__, __init__).


##### `_require_search_provider`  (lines 592–605)

```
def _require_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that research search is configured and usable when an extension requires it. This avoids a user discovering the missing provider only after starting a research task.

**Data flow**: It checks whether the search provider config value is set → raises a clear error if not → calls `_select_search_provider` to validate and build the selected backend.

**Call relations**: _validate_requires calls this for extensions that declare the `search_providers` seam. It hands the detailed backend lookup to `_select_search_provider`.

*Call graph*: calls 1 internal fn (_select_search_provider).


##### `_require_memory_search`  (lines 608–634)

```
def _require_memory_search(_config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that exactly one usable default memory-search provider is installed when an extension requires memory search. Memory search lets the system retrieve stored contextual information.

**Data flow**: It scans manifests for the default memory-search provider name → fails if none or more than one exists → checks whether declared credentials need a credential store → returns successfully only when the provider is unambiguous and usable.

**Call relations**: _validate_requires calls this for extensions that declare the `memory_search` seam. It does not build the provider itself; it checks that the later memory-search selection can work.


##### `_select_auth_proxy`  (lines 646–683)

```
def _select_auth_proxy(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> AuthProxy | None
```

**Purpose**: Chooses the fallback authentication proxy used by connector feed-sync credentials when a connector does not provide its own broker. An auth proxy is a host-side helper that can supply or exchange credentials without exposing them directly to sandboxes.

**Data flow**: It scans manifests for auth proxy specs → rejects duplicate names → chooses the configured backend, or the only installed backend if config is unset → fails if selection is ambiguous or unknown → builds the proxy with a restricted credential reader.

**Call relations**: _connector_registry calls this while building the connector registry. The selected proxy becomes the fallback path for connector credential resolution.

*Call graph*: called by 1 (_connector_registry); 2 external calls (__init__, __init__).


##### `_mount_ext_routes`  (lines 686–724)

```
def _mount_ext_routes(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, index: IndexBackend, embed: EmbedClient) -> None
```

**Purpose**: Adds extension-defined HTTP routes to the FastAPI app under `/ext/<extension-name>/...`. Each route must identify a workspace before the handler can run.

**Data flow**: It walks every manifest’s route specs → builds an extension context with declared credential limits and index/embed access → creates an endpoint wrapper for each route → registers the route on the app.

**Call relations**: `run` calls this after jobs are launched and before shared surfaces are mounted. The nested endpoint function becomes the actual FastAPI handler for each extension route.

*Call graph*: called by 1 (run); 2 external calls (add_route, context_for).


##### `_mount_ext_routes.endpoint`  (lines 708–718)

```
async def endpoint(request: Request, handler=spec.handler, identify=spec.identify, extension_context=context) -> Response
```

**Purpose**: Authorizes and runs one extension route request. It refuses the request if the extension cannot identify a workspace, and otherwise runs the handler inside that workspace’s scope.

**Data flow**: It receives an HTTP request → calls the route’s identify function → returns a 401 response if no workspace is found → otherwise enters the workspace context and calls the extension’s handler → returns the handler’s response.

**Call relations**: This wrapper is registered by `_mount_ext_routes` as the route handler. It hands successful requests to extension code only after binding the workspace with `ws(...)`.

*Call graph*: 2 external calls (Response, ws).


##### `WorkspaceScopeBoundary.__call__`  (lines 745–753)

```
async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None
```

**Purpose**: Clears workspace context at the beginning and end of each HTTP request. This protects a shared process from accidentally reusing a workspace value left over from another request task.

**Data flow**: It receives the low-level ASGI request scope, receive function, and send function → passes non-HTTP traffic through untouched → for HTTP, clears the current workspace, runs the downstream app, and clears the workspace again in a final cleanup step.

**Call relations**: _mount_shared_surfaces installs this as middleware. Surface endpoints set `current_workspace` for valid requests, and this boundary guarantees that setting does not leak beyond the full streamed response.

*Call graph*: 1 external calls (set).


##### `_mount_shared_surfaces`  (lines 756–873)

```
def _mount_shared_surfaces(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: BlobStore, sandboxes: ConversationSandbox, hub: Hub, dbos_client: DBOSClient, artif
```

**Purpose**: Adds shared surface routes to the FastAPI app and ensures every request is tied to the workspace proven by that surface’s authentication. A surface is a user-facing integration point, such as a chat or product UI endpoint.

**Data flow**: It installs the workspace-scope middleware → builds shared admission, hub-tailing, extension, credential, model, skill, memory, and object-schema context pieces → walks every surface route in every manifest → wraps each route with workspace identification → registers routes and optionally starts a writeback poller for durable surfaces.

**Call relations**: `run` calls this after extension routes are mounted. It creates the nested `context_for` helper used by route handlers and writeback polling, and the nested endpoint wrapper used for each surface route.

*Call graph*: called by 1 (run); 13 external calls (__init__, __init__, __init__, __init__, __init__, add_middleware, add_route, core_object_kinds, durable_surfaces, declared_slots (+3 more)).


##### `_mount_shared_surfaces.context_for`  (lines 808–829)

```
def context_for(workspace_id: UUID, surface: str) -> SurfaceContext
```

**Purpose**: Builds the per-request context object that a surface handler uses. This context is like a backpack containing the tools and limits for one surface acting in one workspace.

**Data flow**: It receives a workspace ID and surface name → combines them with blob storage, sandbox access, admission, hub tailing, credentials, public URLs, models, skills, memory, and object schemas → returns a `SurfaceContext`.

**Call relations**: This helper is created inside `_mount_shared_surfaces`. The surface endpoint wrapper calls it for each authorized request, and the writeback poller uses it when delivering durable surface updates.

*Call graph*: 2 external calls (__init__, __init__).


##### `_mount_shared_surfaces.endpoint`  (lines 845–859)

```
async def endpoint(request: Request, handler=route.handler, identify=resolver, surface=spec.name, surface_auth=auth) -> Response
```

**Purpose**: Authorizes and runs one shared surface route request. It lets the surface decide which workspace the request belongs to, then binds that workspace for the full handler execution.

**Data flow**: It receives an HTTP request → asks the surface identify function to authenticate it using `SurfaceAuth` → returns a custom response or 401 if identification fails → sets the current workspace → builds a `SurfaceContext` → calls the surface route handler and returns its response.

**Call relations**: _mount_shared_surfaces registers this wrapper for each surface route. It relies on `context_for` to supply the handler with workspace-specific tools and on `WorkspaceScopeBoundary` to clear the workspace afterward.

*Call graph*: 3 external calls (Response, set, context_for).


##### `_serve_lifespan`  (lines 877–901)

```
async def _serve_lifespan(app: FastAPI) -> AsyncIterator[None]
```

**Purpose**: Runs app-loop background tasks for the lifetime of the FastAPI app. These tasks recover stranded workflows, reconcile cancellations, and, when needed, deliver durable surface writebacks.

**Data flow**: When the app starts, it creates an async task group → starts executor recovery and cancel reconciliation tasks, plus a writeback poller if one exists → yields control while the app runs → on shutdown, cancels those tasks.

**Call relations**: `run` passes this function as the FastAPI lifespan handler. It uses objects placed in `app.state` during startup, such as recovery, DBOS client, and optional writeback poller.

*Call graph*: 2 external calls (__init__, TaskGroup).


##### `_proxy_endpoint`  (lines 904–932)

```
def _proxy_endpoint(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, pricing: Pricing, run_tokens: RunTokenCodec, blob: BlobStore) -> ProxyEndpoint
```

**Purpose**: Chooses how sandboxes reach the egress proxy, which is the controlled gateway for network access from sandboxed runs. It supports either an in-process local proxy or a separately deployed shared proxy.

**Data flow**: It reads sandbox proxy configuration → if no public proxy URL is configured, starts a local proxy via `_local_egress_proxy` → otherwise reads the shared proxy CA certificate from the environment → returns a `ProxyEndpoint` describing the proxy port, trust certificate, and public URL.

**Call relations**: `run` calls this while building the `ConversationSandbox`. It delegates local single-node setup to `_local_egress_proxy` and directly builds the endpoint for hosted multi-node deployments.

*Call graph*: calls 1 internal fn (_local_egress_proxy); called by 1 (run); 1 external calls (__init__).


##### `_local_egress_proxy`  (lines 935–973)

```
def _local_egress_proxy(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, pricing: Pricing, run_tokens: RunTokenCodec, blob: BlobStore) -> ProxyEndpoint
```

**Purpose**: Starts an in-process egress proxy for local or single-node deployments. It runs on its own event loop in a background thread so sandbox network control is separate from normal request and turn handling.

**Data flow**: It creates a new async event loop → starts that loop in a daemon thread → schedules the nested `_boot` coroutine on it → waits up to the startup timeout → returns the proxy endpoint produced by `_boot`.

**Call relations**: _proxy_endpoint calls this when there is no configured external proxy URL. Its nested `_boot` function builds the proxy rules, certificate authority, and server.

*Call graph*: called by 1 (_proxy_endpoint); 3 external calls (new_event_loop, run_coroutine_threadsafe, Thread).


##### `_local_egress_proxy._boot`  (lines 953–971)

```
async def _boot() -> ProxyEndpoint
```

**Purpose**: Builds and starts the actual local egress proxy service. It creates the rule resolver that decides what each sandboxed agent may access, then starts the proxy with fresh local trust material.

**Data flow**: It derives model, artifact, manifest, connector, credential-injection, and transfer-host rules → creates grant and credential-aware per-agent rules → generates a temporary certificate authority → starts `EgressProxy` on the configured port → returns its `ProxyEndpoint`.

**Call relations**: This coroutine is scheduled by `_local_egress_proxy` on the proxy’s separate event loop. It hands rule resolution and live-turn authorization into `EgressProxy` so network requests can be checked as they happen.

*Call graph*: 10 external calls (__init__, __init__, __init__, connector_clis, injecting_slots, model_rule_base, connector_transfer_hosts, derive_artifact_store_rules, derive_manifest_rules, generate_ca).


##### `_connector_registry`  (lines 979–1002)

```
def _connector_registry(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> ConnectorRegistry
```

**Purpose**: Builds the central registry of installed connector providers. Connectors are integrations that can authenticate to outside services and provide tools or synced feeds.

**Data flow**: It scans all manifests for connector declarations → rejects duplicate provider names → creates one registry entry per provider with its label and broker information → selects a fallback auth proxy → returns a `ConnectorRegistry` with namespace resolution.

**Call relations**: `run` calls this before building the runtime. It calls `_select_auth_proxy` for the fallback credential path and supplies the resulting registry to runtime tools and source syncing.

*Call graph*: calls 1 internal fn (_select_auth_proxy); called by 1 (run); 3 external calls (__init__, __init__, open_connector_namespace).


##### `_connect_flow`  (lines 1005–1030)

```
def _connect_flow(credentials: CredentialStore | None, config: Config, manifests: tuple[Manifest, ...]) -> ConnectFlow | None
```

**Purpose**: Builds the OAuth connect flow for installed connectors. OAuth is the common browser-based permission handoff where a user authorizes access to an external service.

**Data flow**: It receives credentials, config, and manifests → returns `None` if no credential store exists → collects OAuth provider descriptors from connectors while rejecting duplicates → computes the public callback URI → creates a `ConnectFlow` with encryption, grant storage, and namespace resolution.

**Call relations**: `run` calls this and installs the result globally with `install_connect_flow`. It calls `_connect_redirect_uri` to make sure the callback URL is valid before OAuth can be used.

*Call graph*: calls 1 internal fn (_connect_redirect_uri); called by 1 (run); 3 external calls (__init__, __init__, open_connector_namespace).


##### `_connect_redirect_uri`  (lines 1033–1057)

```
def _connect_redirect_uri(config: Config, providers: Mapping[str, OAuthProvider]) -> str
```

**Purpose**: Builds and validates the public OAuth callback URL for connector authentication. This URL must be reachable by external providers after the user approves access.

**Data flow**: It reads `connect.public_base_url` from config and the provider map → if no providers exist, returns an inert callback path or empty string → otherwise requires a URL with `http` or `https`, a host, and a non-local bind address → returns the base URL plus the callback path.

**Call relations**: _connect_flow calls this while creating the OAuth connect flow. Its validation prevents connector setup from using addresses like `127.0.0.1` or `0.0.0.0` that an external provider cannot redirect to.

*Call graph*: called by 1 (_connect_flow); 1 external calls (urlparse).


### Assistant pack variants
These manifests define the main assistant-oriented bundles for development, billing, evaluation, and hosted deployments.

### `packs/assistant_billing/ufo_pack_assistant_billing.py`

`config` · `config load / startup`

This file is a small configuration bridge between the local assistant setup and the hosted billing system. In this project, a “pack” is a named bundle of extensions that tells the system which capabilities to turn on. The regular local assistant pack does not include Metronome, the billing provider, because most development setups should not send usage data to a real billing service. The hosted assistant pack does include billing, but it also includes hosted-only services that are not useful on a laptop.

This file creates the missing middle option: `assistant_billing`. It starts with all extensions from the normal assistant pack, then adds `metronome`. Choosing this pack enables billing-related behavior such as billing setup, billing activation, and shipping usage or seat information to Metronome. Because that requires real external credentials, it is deliberately not the default. A developer must opt in and should use sandbox or test-mode keys.

Without this file, the “Set up billing” action offered during hosted onboarding would have no simple local equivalent to exercise end to end. It is like adding one special adapter to a test bench: the rest of the assistant stays local, but the billing wire is connected so the full circuit can be checked.

#### Function details

##### `pack`  (lines 25–26)

```
def pack() -> Pack
```

**Purpose**: This function builds and returns the pack definition for the `assistant_billing` bundle. The system uses it to learn the pack’s name, version, and which extensions should be enabled.

**Data flow**: It reads the fixed pack name and version from this file, and reads the assistant extension list imported from the normal assistant pack. It adds the `metronome` extension to that list, then creates a `Pack` object containing the combined information and returns it.

**Call relations**: When the pack system loads this file, it calls `pack` to get the concrete pack description. Inside, `pack` hands the name, version, and extension list to `Pack.__init__`, which turns those plain values into the structured object the rest of the system can use during startup.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_dev/ufo_pack_assistant.py`

`config` · `config load`

A pack is like a pre-packed toolbox. Instead of asking an operator to turn on memory, web research, browser tools, document creation, connectors, coding help, and other features one by one, this file names one bundle called “assistant” that brings them up together.

The file is intentionally small. It sets a pack name, a version, and a fixed list of extensions. Each extension is a separate capability area, such as durable memory, search-backed research, browser automation, scheduled tasks, website building, document generation, connector integrations, coding support, and debugging tools. The long comment at the top explains the important design choice: this pack runs on the project’s own local carrier and index, rather than relying on managed hosted infrastructure. That is what separates it from a hosted assistant pack.

This file does not add its own tools, skills, or onboarding text. It only chooses which extension manifests are included. In everyday terms, it is not building the tools; it is deciding which tools are placed in the assistant’s kit. Without this file, users could not activate this exact assistant setup by simply choosing the “assistant” pack name.

#### Function details

##### `pack`  (lines 48–49)

```
def pack() -> Pack
```

**Purpose**: This function creates the pack description that the larger system can load. Someone would use it when they want the system to know the pack’s name, version, and exact list of extensions to activate.

**Data flow**: It reads the file’s constants: the pack name, version, and extension list. It passes those values into a `Pack` object, which is the structured form the rest of the system understands. The result is a ready-to-use pack manifest; it does not change anything else by itself.

**Call relations**: When the pack system loads this file, it calls `pack` to obtain the manifest. Inside, `pack` hands the name, version, and extension list to `Pack.__init__`, which packages that information into the standard object used by the rest of the configuration flow.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_eval/ufo_pack_assistant_eval.py`

`config` · `startup/config load`

This file is a small manifest for an evaluation-only tool pack. A “pack” is a named bundle of extensions that the system can load from configuration. Here, the goal is to create a safe and predictable environment for testing assistant conversations.

It starts from the regular assistant pack, then filters out real broker providers such as Composio and Pipedream. Those services normally need real API keys and live accounts. In an evaluation run, they would only create confusing dead ends, because the assistant could see them but could not successfully use them. Instead, this pack adds “eval_env”, which provides deterministic fake services like email, calendar, and code search, and “docker”, which lets each evaluated conversation use a real bind-mounted workspace directory.

The important idea is separation. The fake evaluation providers should not be included in the normal product assistant pack, because tool registry entries may be visible even when the user has not granted access. This eval pack keeps test-only tools in the test-only environment, like putting practice equipment in a training room rather than in the real workplace.

#### Function details

##### `pack`  (lines 26–27)

```
def pack() -> Pack
```

**Purpose**: This function builds and returns the pack description for the evaluation assistant environment. The system can call it to learn the pack’s name, version, and the exact extension list it should load.

**Data flow**: It reads the file-level constants for the pack name, version, and chosen extensions. It passes those values into the Pack object, which becomes the final structured description returned to the caller.

**Call relations**: When the pack-loading system discovers this module, it calls `pack` to get the manifest. `pack` then hands the prepared values to `Pack.__init__`, so the rest of the system receives a standard Pack object rather than having to understand this file’s filtering rules itself.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_hosted/ufo_pack_assistant_hosted.py`

`config` · `config load / startup`

Think of this file like the packing list for a hosted product edition. It does not implement memory, Slack, browser use, billing, coding tools, or document generation itself. Instead, it names the pieces that should be turned on together so the rest of the system can assemble them consistently.

The hosted assistant pack includes many assistant capabilities: memory and recall, research tools, connectors to outside services, browser and computer-use tools, website and document creation, scheduled tasks, Slack support, a web portal, model providers, usage metering, and more. The important difference is that several heavy parts are backed by managed infrastructure. For example, search indexing uses Turbopuffer, live coordination uses Redis, browser sessions use Browserbase-hosted Chrome, and sandboxed code execution uses E2B.

The file also points to one packaged skill, `customer-onboarding-help`. A skill here is a bundled set of assistant knowledge or behavior. This one gives the hosted workspace a curated, read-only source of onboarding facts, so the assistant can answer product setup and billing questions from shipped content rather than relying on a customer’s own workspace memory.

Without this file, choosing `assistant_hosted` would not give the system a clear recipe for which hosted services, tools, and bundled knowledge to activate.

#### Function details

##### `pack`  (lines 64–70)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the pack description that the system uses to activate the hosted assistant. Someone would use it when loading this pack so the platform knows its name, version, enabled extensions, and included skill folders.

**Data flow**: It starts with constants in this file: the pack name, version, the list of extension names, the folder where skills live, and the skill names. It turns each skill name into a `SkillSpec`, which is a small description pointing at that skill’s directory. It then puts everything into a `Pack` object and returns that object to the caller.

**Call relations**: When the pack-loading part of the system asks this module for its pack definition, `pack` creates the manifest. During that work it hands each skill path to `SkillSpec.__init__` so the skill can be described, then hands the full name, version, extensions, and skill specs to `Pack.__init__` so the final pack object can be used by the wider system.

*Call graph*: 2 external calls (__init__, __init__).


### Specialized pack manifests
These manifests define domain-specific, evaluation, sample, and YC founder bundles that can be selected as named packs.

### `packs/chief_of_staff/ufo_pack_chief_of_staff.py`

`config` · `startup`

This file is the pack’s manifest, which is like a label on a toolbox saying what is inside and what it needs to work. The pack is meant to act as a chief-of-staff assistant for one person. It brings together meeting notes, Slack channels, people files, org charts, daily logs, todos, scheduled syncs, and self-improvement workflows so the user can review and approve suggested updates from Slack.

The file does not implement the actual workflows itself. Instead, it names the pack, gives it a version, lists the system extensions it depends on, and points to four skill folders: setup, sync, prep, and triage. Those skills are where the user-facing behavior lives. For example, setup is done through conversation, sync reviews new information and proposes updates, prep creates a one-on-one meeting brief, and triage captures the user’s judgment so future runs get better.

Without this file, the system would not know that these skills and extensions belong together as one deployable pack. It is the entry in the catalog that lets the broader UFO runtime load the chief-of-staff assistant as a coherent unit.

#### Function details

##### `pack`  (lines 40–46)

```
def pack() -> Pack
```

**Purpose**: This function builds and returns the pack description that the UFO system can load. Someone would use it when registering or starting this pack so the runtime knows its name, version, required extensions, and available skills.

**Data flow**: It starts with the constants in this file: the pack name, version, extension names, the skills directory, and the skill names. It turns each skill name into a SkillSpec pointing at that skill’s folder, then wraps everything in a Pack object. The result is a complete pack manifest; it does not write files or change outside state.

**Call relations**: When the UFO pack loader asks this module what it provides, this function is the answer. It creates SkillSpec objects for the four skill folders, then hands those along to Pack so the larger system can load the extensions and expose the skills together.

*Call graph*: 2 external calls (__init__, __init__).


### `packs/dsqa_eval/ufo_pack_dsqa_eval.py`

`config` · `startup`

This file is like a menu of tool bundles. Instead of making every part of the system choose extensions one by one, it gives names to three useful combinations: a core DSQA pack, a search-enabled pack, and a browser-enabled pack.

The shared version number keeps all three packs labeled consistently. The base extension list includes the default index, OpenAI embedding support, and OpenRouter model access. The core pack uses just those basics. The search pack builds on that by adding Exa and research tools, so it can look things up. The browser pack builds on the search pack again by adding browser and Chrome sandbox support, so it can interact with web pages more directly.

Each function returns a `Pack`, which is a manifest object from `ufo.sdk.manifest`. In plain terms, that object is the system’s written record of “this pack is called X, it is version Y, and it includes these extensions.” Without this file, users or automation would have to recreate these exact extension combinations elsewhere, making setup more error-prone and less consistent.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the basic DSQA pack. Someone would use this when they need the core DSQA evaluation setup without search or browser extras.

**Data flow**: It reads the fixed core pack name, version, and core extension list from this file. It passes those values into `Pack.__init__`, which builds a `Pack` manifest object. The result is returned to the caller as the ready-to-use core pack definition.

**Call relations**: This function is a simple pack factory. When called, it hands the name, version, and extension list to `Pack.__init__` so the shared manifest type can create the actual pack object.

*Call graph*: 1 external calls (__init__).


##### `search_pack`  (lines 17–18)

```
def search_pack() -> Pack
```

**Purpose**: Creates the DSQA pack that includes search and research capabilities. Someone would use this when evaluation work needs access to external information lookup tools.

**Data flow**: It reads the fixed search pack name, version, and search extension list. That list includes the base extensions plus search-related additions. It sends those values to `Pack.__init__` and returns the resulting `Pack` manifest object.

**Call relations**: This function builds on the same pattern as the core pack, but with a larger extension set. Its only handoff is to `Pack.__init__`, which turns the chosen settings into a manifest object.

*Call graph*: 1 external calls (__init__).


##### `browser_pack`  (lines 21–22)

```
def browser_pack() -> Pack
```

**Purpose**: Creates the most capable DSQA pack in this file, including browser automation support. Someone would use this when the evaluation needs both search tools and the ability to work through a browser environment.

**Data flow**: It reads the fixed browser pack name, version, and browser extension list. That list includes the search extensions plus browser and sandboxed Chrome support. It passes everything to `Pack.__init__` and returns the completed `Pack` manifest.

**Call relations**: This function is the final, largest bundle in the file. When called, it delegates object creation to `Pack.__init__`, giving it the browser pack’s name and full extension list.

*Call graph*: 1 external calls (__init__).


### `packs/gdpval_eval/ufo_pack_gdpval_eval.py`

`config` · `startup / pack discovery`

This file is like a menu for assembling the GDPVal evaluation environment. A “pack” is a bundle of capabilities that the UFO system can load together. Instead of making every user remember a long list of extension names, this file gives those lists clear names.

It starts by naming the shared version and the extension groups. The base group includes the default index, OpenAI embeddings, and OpenRouter access. Then there are optional groups: one for working with documents and code-like tasks, and another for web research and browser-based work.

The four functions each return a Pack object, which is the manifest object the wider system understands. The core pack gives only the common base tools. The documents pack adds document, REPL, and coding support. The research pack adds search, research, browser, and sandboxed Chrome support. The full pack combines everything.

Without this file, callers would need to manually recreate these exact extension combinations. That would make setup more error-prone, especially if different parts of the project accidentally used slightly different bundles.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the smallest GDPVal pack. Someone would use this when they only need the common base extensions and do not need document tools or research tools.

**Data flow**: It takes no input from the caller. It reads the fixed core name, version, and base extension list from this file, then builds a Pack object with those values. The result is a ready-to-load pack named for the core GDPVal setup.

**Call relations**: When the system or a user asks for the core GDPVal pack, this function constructs it by calling Pack.__init__. It hands Pack the name, version, and extension list so the wider UFO system can later load those extensions together.

*Call graph*: 1 external calls (__init__).


##### `documents_pack`  (lines 17–22)

```
def documents_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack for document-heavy work. It includes the base capabilities plus tools for documents, interactive execution, and coding support.

**Data flow**: It takes no caller input. It combines the shared base extensions with the document-related extensions, then passes that combined list along with the document pack name and version into a new Pack object. The output is a configured pack for document and coding workflows.

**Call relations**: When something needs the document-focused GDPVal setup, this function is the factory that builds it. It calls Pack.__init__ with the combined extension set, leaving the actual pack object creation to the Pack class.

*Call graph*: 1 external calls (__init__).


##### `research_pack`  (lines 25–30)

```
def research_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack for research-style work. It includes the base capabilities plus extensions for search, research, browsing, and sandboxed browser use.

**Data flow**: It receives no input. It joins the base extension list with the research extension list, then uses those values, the research pack name, and the shared version to create a Pack object. The output is a research-oriented pack ready for the system to load.

**Call relations**: When the research version of the GDPVal environment is requested, this function prepares the manifest. It delegates the actual object construction to Pack.__init__, supplying the exact extension bundle needed for research tasks.

*Call graph*: 1 external calls (__init__).


##### `full_pack`  (lines 33–38)

```
def full_pack() -> Pack
```

**Purpose**: Creates the most complete GDPVal pack. It includes the base tools, the document tools, and the research tools all together.

**Data flow**: It takes no input from the caller. It combines all three extension groups defined in the file, then creates a Pack object using the full pack name and shared version. The result is an all-in-one pack for users who want every listed capability available.

**Call relations**: When the full GDPVal setup is needed, this function assembles the complete extension bundle. It calls Pack.__init__ to turn the name, version, and combined extension list into the Pack object that the rest of the system can consume.

*Call graph*: 1 external calls (__init__).


### `packs/sample_pack/ufo_pack_sample.py`

`config` · `pack discovery and onboarding`

This is a conformance sample pack: a deliberately simple pack that acts like a real installed add-on. Its job is to exercise the boundary between the core system and external packs. That boundary matters because outside pack authors should only need the public `ufo.sdk` interface, not private project internals.

The file declares basic pack facts such as its name, version, bundled extension, skill name, skill folder, and onboarding marker. The `pack()` function is the public entry point. When the system loads this pack, `pack()` returns a `Pack` object saying: include the `sample` extension, add a skill from the pack’s `skills/sample_pack_skill` folder, and run one onboarding step named `sample_pack_setup`.

The onboarding step calls `_setup()`. That function writes `{"pack_onboarded": True}` into the pack’s scoped store under a fixed key. The store is durable project storage, not just a test log, so tests can later read the value back through the same public path the real system would use. In everyday terms, this file is like a smoke-test plug: if the plug fits and the light turns on, the pack seam is still working.

#### Function details

##### `_setup`  (lines 25–26)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: This asynchronous onboarding function records that the sample pack has been set up. It exists so the conformance test can prove that pack onboarding can write to the real scoped store.

**Data flow**: It receives an `ExtensionContext`, which is the pack’s view of shared services such as storage. It writes the fixed key `pack:onboarded` with the value `{"pack_onboarded": True}` into `ctx.store`. Nothing is returned, but the store is changed so later code can read back proof that onboarding ran.

**Call relations**: This function is not called directly in this file. Instead, `pack()` wraps it inside an `OnboardingStep`, and the wider pack-loading flow calls it when that onboarding step is executed.


##### `pack`  (lines 29–36)

```
def pack() -> Pack
```

**Purpose**: This is the pack entry point the UFO pack loader asks for. It builds and returns a `Pack` description that tells the system what extension, skill, and onboarding step this sample pack contributes.

**Data flow**: It reads the constants defined in this file, such as the pack name, version, bundled extension name, skill path, onboarding name, and setup function. It uses those values to create a `SkillSpec`, an `OnboardingStep`, and finally a `Pack`. The returned `Pack` is the complete public description of what this installed pack adds.

**Call relations**: When the system discovers this pack, it calls `pack()` to learn what to activate. Inside that build step, `pack()` creates a `SkillSpec` for the skill folder, creates an `OnboardingStep` that points at `_setup`, and passes all of that into `Pack` so the loader can register the extension, skill, and onboarding work together.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `packs/yc/ufo_pack_yc/manifest.py`

`config` · `startup`

This is a small “label on the box” file for a pack of YC-focused tools. A pack is a bundle of capabilities that the UFO system can install or load together. Without this manifest, the system would not know the pack’s identity, what supporting extensions it depends on, or where to find its skill folders.

The file sets a few simple facts: the pack is called “yc”, its version is “0.1.0”, and it needs several extensions such as command-line support, memory, document handling, scheduled tasks, and todos. It also points to a local skills directory and lists two skills: “founder-operations” and “company-diligence”.

The main function, `pack`, turns those plain constants into a `Pack` object the UFO framework can understand. For each skill name, it creates a `SkillSpec`, which is a small description saying where that skill lives on disk. In everyday terms, this file is like the table of contents and packing slip for this YC bundle: it does not perform the skills itself, but it tells the system what is inside and what must be available for the contents to work.

#### Function details

##### `pack`  (lines 23–29)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the pack description that the UFO system uses to load the YC founder pack. It gathers the pack’s name, version, required extensions, and skill folder locations into one structured object.

**Data flow**: It starts with the constants defined in this file: the pack name, version, extension names, the skills root folder, and the skill names. It converts each skill name into a path under the skills folder, wraps each path in a `SkillSpec`, and then places everything into a `Pack`. The result is a ready-to-use pack object; it does not change files or global state.

**Call relations**: When the larger UFO framework asks this module what pack it provides, this function is the answer. It creates `SkillSpec` objects for the listed skills, then hands those along to `Pack.__init__` so the framework receives one complete manifest for loading the pack.

*Call graph*: 2 external calls (__init__, __init__).

## 📊 State Registers Touched

- `reg-effective-configuration` — The final startup settings that decide how the service, security, sandbox, proxy, and enabled packs should behave.
- `reg-extension-catalog` — The loaded list of extensions and packs that tells the system which extra tools, routes, jobs, skills, and backends exist.
- `reg-durable-store-schema` — The shared database layout and migration version that all services rely on when saving or reading system records.
- `reg-workspace-tenant-state` — The saved customer workspace boundary, including its owners, admins, limits, main agent, and tenant separation rules.
- `reg-onboarding-claims-invites` — The temporary signup claims, email verification codes, invite records, and hosted gateway tokens used to admit new users.
- `reg-agent-profile` — The saved assistant setup for each workspace, including model choice, audience, internet access, skills, and control settings.
- `reg-credential-secret-store` — The encrypted store of workspace and connector secrets, plus the requests that say which secrets a tool or proxy may reveal.
- `reg-sandbox-workspace-state` — The remembered sandbox workspace for a conversation, including its backend handle, files, runtime folder, and cleanup ownership.
- `reg-egress-network-state` — The controlled network exit state, including proxy configuration, certificates, allowed destinations, metering, and last-moment credential injection.
- `reg-skill-inventory` — The declared and user-created skill inventory, including skill ownership, dependencies, files, and the load order copied into a sandbox.
- `reg-database-connection-pool` — The live database engine/session pool and transaction doorway shared by migrations, request handlers, workers, and shutdown cleanup.
- `reg-sandbox-image-cache` — The built or validated sandbox runtime image/backend artifact that later sandbox launches reuse.
- `reg-service-lifecycle-state` — The process-wide lifecycle state containing startup task handles, shutdown signals, and service cleanup hooks drained during teardown.
