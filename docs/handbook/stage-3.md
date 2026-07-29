# Process startup, service lifespan, and fleet presence  `stage-3`

This stage covers how UFO comes to life, stays visible while running, and cleans up after trouble. It is mostly startup and behind-the-scenes support. The HTTP application startup part is the main “open the shop” step. Command-line tools let a person create or inspect a workspace and start the service. The server builder reads settings, connects shared pieces like the database, credentials, extensions, sandbox access, and background workers, then opens the web app and attaches the routes that receive requests.

The hosted control service has its own command-line front door in control/src/ufo_control/main.py. It starts the gateway web server and can also run admin jobs such as setting up the database, creating invites, retrying Slack deliveries, and preparing row-level security, which means database rules that limit which rows each user may access.

Runtime supervision is the “watchman” after startup. It records live processes in fleet state, cancels work safely, recovers work left behind by dead processes, and starts observability: logs, metrics, and traces that explain what happened without leaking secrets.

## Sub-stages

- [HTTP application startup](stage-3.1.md) `stage-3.1` — 2 files
- [Runtime process supervision](stage-3.2.md) `stage-3.2` — 3 files

## Files in this stage

### Process startup, service lifespan, and fleet presence
### `control/src/ufo_control/main.py`

`entrypoint` · `startup and operator/admin commands`

This file is the place an operator or deployment system talks to the control service from the outside. It uses Click, a command-line tool library, to define several commands under one `main` command group. Think of it like a small control panel: one button starts the web gateway, another reshapes the database, another sends an invite, and others repair or bootstrap system state.

At startup, it sets up normal process logging. If an OpenTelemetry logs endpoint is configured, it also sends logs to that collector. OpenTelemetry is a standard way to ship logs and other system signals to monitoring tools. The file is careful not to feed OpenTelemetry's own errors back into that same reporting pipeline, which avoids a noisy loop if log export fails.

The `gateway` command starts the web application with Uvicorn, an ASGI web server for Python web apps. The database-oriented commands use the database owner connection string, check or shape the control schema, and then perform one focused job. Invite creation is especially careful: it checks that mail sending is configured before spending an invite grant, creates the grant in the database, then sends the email. If the email fails after the grant exists, it reports that clearly instead of silently undoing the grant.

#### Function details

##### `main`  (lines 38–41)

```
def main() -> None
```

**Purpose**: This is the top-level command group for operating the hosted shared-workspace service. It prepares logging before any specific command runs.

**Data flow**: It reads the optional log export endpoint from the process environment, sets a standard log format and log level, then passes that endpoint into the log export setup. It does not return useful data; it prepares the process so later commands have consistent logging.

**Call relations**: This is the outer wrapper for the commands in this file. When a user runs the command-line tool, Click enters here first, then the setup calls `_export_logs` before dispatching to a chosen subcommand such as `gateway`, `migrate`, or `invite`.

*Call graph*: calls 1 internal fn (_export_logs); 1 external calls (basicConfig).


##### `_export_logs`  (lines 44–54)

```
def _export_logs(otlp_endpoint: str | None) -> None
```

**Purpose**: This turns on remote log shipping when the deployment provides an OpenTelemetry endpoint. If no endpoint is set, it deliberately leaves logging on normal stdout/stderr only.

**Data flow**: It receives either a log collector URL or `None`. With `None`, it stops immediately. With a URL, it builds an OpenTelemetry logger provider, attaches a batch processor that sends records to the collector's logs path, and then asks `_install_root_handler` to connect Python's normal logging to that provider.

**Call relations**: The `main` command group calls this during command startup. If log exporting is enabled, `_export_logs` creates the OpenTelemetry pieces and hands them to `_install_root_handler`, which plugs them into the root logger used by the rest of the process.

*Call graph*: calls 1 internal fn (_install_root_handler); called by 1 (main); 4 external calls (OTLPLogExporter, LoggerProvider, BatchLogRecordProcessor, create).


##### `_install_root_handler`  (lines 57–62)

```
def _install_root_handler(logger_provider: LoggerProvider) -> None
```

**Purpose**: This connects ordinary Python log messages to the OpenTelemetry logging pipeline. It also protects the process from a feedback loop by excluding OpenTelemetry's own internal logs.

**Data flow**: It receives an OpenTelemetry logger provider. It wraps that provider in a logging handler, adds a filter that rejects records whose logger name starts with `opentelemetry`, and attaches the handler to Python's root logger. After that, normal log records are also sent through the OpenTelemetry exporter.

**Call relations**: `_export_logs` calls this after building the remote logging setup. The root logger then becomes the bridge used by all later commands and server code when they write normal Python logs.

*Call graph*: called by 1 (_export_logs); 2 external calls (getLogger, LoggingHandler).


##### `gateway`  (lines 66–71)

```
def gateway() -> None
```

**Purpose**: This starts the hosted gateway web server. The gateway serves onboarding, fleet count information, and the terminal client.

**Data flow**: It reads the gateway port from the environment, falling back to the default port if none is set. It then starts Uvicorn on all network interfaces, pointing it at the `ufo_control.gateway:app` web application. The command keeps running as the web server process.

**Call relations**: Click runs this when the operator chooses the `gateway` command. It hands control to Uvicorn, which imports and serves the gateway application for incoming HTTP requests.

*Call graph*: 1 external calls (run).


##### `migrate`  (lines 75–78)

```
def migrate() -> None
```

**Purpose**: This updates the control database schema to the expected shape. It is used when deploying or upgrading the service so the database has the tables and ledgers the gateway needs.

**Data flow**: It gets the database owner connection string, runs the asynchronous schema-shaping operation to completion, and then prints a success message. The main change is in the database, not in Python memory.

**Call relations**: Click runs this for the `migrate` command. Because the schema function is asynchronous, `migrate` uses `asyncio.run` to execute it from this synchronous command-line function, then reports completion with Click.

*Call graph*: 4 external calls (run, echo, owner_dsn, shape_control_schema).


##### `invite`  (lines 84–91)

```
def invite(object_number: int, email: str) -> None
```

**Purpose**: This grants one waitlist object a new workspace invitation and emails that invitation to the given address. It is an operator-facing command for onboarding a specific recipient.

**Data flow**: It receives an object number and an email address from the command line. It calls `_mint_invite` to check configuration, create the database grant, and send the email. If invite validation or work-email validation fails, it turns that into a friendly command-line error; otherwise it prints who was granted access and when the invite expires.

**Call relations**: Click runs this for the `invite` command. It delegates the real asynchronous work to `_mint_invite`, then formats the returned invite details for the operator.

*Call graph*: calls 1 internal fn (_mint_invite); 3 external calls (run, ClickException, echo).


##### `_mint_invite`  (lines 94–116)

```
async def _mint_invite(object_number: int, email: str) -> MintedInvite
```

**Purpose**: This does the careful, multi-step work behind the invite command. It makes sure email sending is configured, records the invite grant in the database, and sends the invitation email.

**Data flow**: It receives a waitlist object number and email address. It reads the public host name and database owner connection string, verifies the control schema exists, builds an email sender from environment settings, opens a small database connection pool, and mints the invite through `InviteCodes`. After closing the pool, it builds the email subject and body and sends the message. It returns the minted invite details; if sending fails after the grant exists, it raises a clear command-line exception saying the grant still stands.

**Call relations**: `invite` calls this when an operator asks to create an invitation. `_mint_invite` coordinates helpers from email, schema, database, and invite-code modules: schema checks guard the database work, `InviteCodes` creates the grant, and the email helpers prepare and send the message.

*Call graph*: called by 1 (invite); 8 external calls (__init__, create_pool, ClickException, email_sender_from_env, invite_email, public_apex_host, owner_dsn, require_control_schema).


##### `slack_connect_retry`  (lines 121–127)

```
def slack_connect_retry(onboard_claim_id: uuid.UUID) -> None
```

**Purpose**: This lets an operator retry a failed Slack Connect signup delivery after fixing the original problem. Slack Connect is Slack's way to connect workspaces or channels across organizations.

**Data flow**: It receives an onboarding claim ID from the command line. It calls `_rearm_slack_connect` to mark a failed delivery as ready to try again. If no matching failed delivery exists, it raises a command-line error; otherwise it prints when the delivery originally failed.

**Call relations**: Click runs this for the `slack-connect-retry` command. It delegates the database work to `_rearm_slack_connect`, then turns the returned timestamp into a human-readable operator message.

*Call graph*: calls 1 internal fn (_rearm_slack_connect); 3 external calls (run, ClickException, echo).


##### `_rearm_slack_connect`  (lines 130–137)

```
async def _rearm_slack_connect(onboard_claim_id: uuid.UUID) -> datetime | None
```

**Purpose**: This performs the database work needed to re-arm one failed Slack Connect delivery. Re-arming means making the system eligible to try the delivery again.

**Data flow**: It receives an onboarding claim ID. It gets the database owner connection string, checks that the control schema is present, opens a small database connection pool, and calls the Slack Connect helper to re-arm the failed delivery. It closes the pool afterward and returns the original failure time, or `None` if there was no matching failed delivery.

**Call relations**: `slack_connect_retry` calls this from the command line flow. This function connects the command to the lower-level Slack Connect delivery repair logic, while taking responsibility for schema checking and opening and closing the database pool.

*Call graph*: called by 1 (slack_connect_retry); 4 external calls (create_pool, rearm_failed_delivery, owner_dsn, require_control_schema).


##### `rls_bootstrap`  (lines 141–144)

```
def rls_bootstrap() -> None
```

**Purpose**: This command creates or updates the shared database role and workspace access policies. Row-level security means the database itself helps decide which rows each role may see or change.

**Data flow**: It runs `_bootstrap` to apply the needed database role and policy setup, then prints a success message. The lasting effects are changes in database security configuration.

**Call relations**: Click runs this for the `rls-bootstrap` command. It uses `asyncio.run` because `_bootstrap` performs asynchronous database operations.

*Call graph*: calls 1 internal fn (_bootstrap); 2 external calls (run, echo).


##### `_bootstrap`  (lines 147–150)

```
async def _bootstrap() -> None
```

**Purpose**: This is the asynchronous worker behind the row-level security bootstrap command. It applies the database policies and ensures the service role exists.

**Data flow**: It reads the owner database connection string, then calls the policy bootstrap function and the serve-role creation/check function. It returns no value; the important result is that the database has the expected security role and policies.

**Call relations**: `rls_bootstrap` calls this when an operator runs the bootstrap command. `_bootstrap` hands off to the RLS module, which contains the actual database policy and role definitions.

*Call graph*: called by 1 (rls_bootstrap); 3 external calls (bootstrap_policies, ensure_serve_role, owner_dsn).

## 📊 State Registers Touched

- `reg-database-schema` — The shared database layout and migration version that define which long-term records the system can store.
- `reg-workspace-boundary` — The current workspace or tenant boundary used to keep each customer’s data and actions separate.
- `reg-effective-configuration` — The chosen runtime settings that tell the service how this deployment should behave.
- `reg-pack-selection` — The selected product pack that decides which bundle of extensions, skills, and infrastructure is enabled.
- `reg-extension-inventory` — The installed extension set and their declared capabilities, such as tools, routes, jobs, skills, and storage.
- `reg-credential-store` — The encrypted store of API keys, service secrets, and owner-provided credentials.
- `reg-sandbox-session` — The saved or live sandbox workspace where an agent can run commands and keep files across tool calls.
- `reg-egress-policy` — The network access rules that decide which outside sites sandboxed work may contact and which secrets may be injected.
- `reg-background-jobs` — The shared registry and saved queue of scheduled, recurring, delayed, and administrative background work.
- `reg-fleet-presence` — The shared record of live runtime processes used for supervision, cancellation, and recovery after crashes.
- `reg-observability-context` — The shared logging, metrics, tracing, and trace-link state used to understand work across requests and subagents.
- `reg-onboarding-claims` — The hosted signup state for email claims, invitations, company-domain workspace mapping, and temporary access tokens.
- `reg-database-connection-pool` — The shared database engine, session factory, connection pool, and transaction context reused by migrations, request handlers, workers, and background jobs.
