# Hosted control-plane onboarding and workspace provisioning  `stage-2`

This stage is the front door for hosted UFO onboarding. It is used when the control service starts, when a new person signs up, and when operators run maintenance commands. The main command file starts the public web gateway, prepares the database, creates invite emails, retries Slack Connect setup, and installs database rules. The package file simply makes these pieces importable.

The gateway is the small public server that guides new users. It serves the terminal client and browser sign-in page, sends simple instructions back to the client, and decides whether a new curl | sh install should be suggested. It checks work email addresses, sends short-lived verification codes, stores only safe hashed proof in Postgres, and confirms the code later. Invite code logic does the same for one-time workspace creation invites.

Once a user is verified, shared-domain logic maps their company email domain to an existing workspace or safely creates one. The onboarding setup creates the workspace basics and lets extensions prepare themselves. Token code signs the user in for 30 days. Slack Connect jobs then create customer Slack channels, with retries and operator visibility if something fails.

## Files in this stage

### Service entrypoints
These files establish the hosted control package and provide the CLI and public gateway entrypoints for onboarding and maintenance.

### `control/src/ufo_control/__init__.py`

`other` · `import time`

In Python, a folder often needs an `__init__.py` file to be treated as a package: a named bundle of code that can be imported elsewhere. This file is that marker for the `ufo_control` package. Think of it like a label on a drawer: the drawer may contain useful tools, but the label itself does not do the work. Because this file is empty, it does not run setup code, expose shortcut names, or change how the package behaves. Its main value is structural: it helps Python and developers recognize that files under `ufo_control` belong together and can be referred to with imports such as `ufo_control.some_module`. Without this file, some Python environments or tools might not recognize the directory as an importable package, which could make imports fail or behave inconsistently.


### `control/src/ufo_control/main.py`

`entrypoint` · `startup and operator maintenance commands`

This file is like the service desk for the hosted shared-workspace system. It does not contain the web app itself or the database rules themselves; instead, it gives operators simple commands that start those larger pieces or run one-time maintenance jobs. Without this file, there would be no single, standard way to launch the gateway server or safely perform important setup tasks such as shaping the control database schema, creating invite codes, or bootstrapping row-level security policies. Row-level security means database rules that limit which rows a user or role is allowed to see or change.

When the command-line tool starts, it sets up normal logging and, if an OpenTelemetry collector address is provided, also ships logs to the platform log collector. OpenTelemetry is a common observability system for collecting logs and traces from services.

The file then defines several commands. The gateway command runs the HTTP server through Uvicorn, using an environment variable to choose the port. The migrate and rls-bootstrap commands prepare the database as the owner user. The invite command creates a one-time onboarding code and formats the email text to send it. The Slack retry command re-arms a failed Slack Connect delivery after an operator has fixed the underlying problem. For async database work, the visible command wraps a smaller async helper, so command-line users get a simple synchronous experience while the code can still use efficient async database connections.

#### Function details

##### `main`  (lines 33–36)

```
def main() -> None
```

**Purpose**: This is the top-level command group for the control service command-line tool. It prepares logging before any specific subcommand, so every command has consistent output and optional remote log export.

**Data flow**: It reads the OpenTelemetry endpoint from the process environment, sets the basic log format and level, then passes that endpoint to the log export setup. It does not return useful data; it prepares the process before a subcommand runs.

**Call relations**: This function is the outer wrapper that Click uses to organize the commands in this file. As part of that startup path, it calls _export_logs so that any later command, such as gateway or migrate, writes logs in the configured way.

*Call graph*: calls 1 internal fn (_export_logs); 1 external calls (basicConfig).


##### `_export_logs`  (lines 39–49)

```
def _export_logs(otlp_endpoint: str | None) -> None
```

**Purpose**: This turns on remote log shipping when the operator has provided a log collector address. If no address is configured, it deliberately leaves logging as local console output only.

**Data flow**: It receives either a collector endpoint string or None. With None, it stops immediately. With an endpoint, it creates an OpenTelemetry logger provider for the service, points it at the logs URL, batches log records for efficient sending, and then asks _install_root_handler to connect normal Python logging to that provider.

**Call relations**: main calls this during command-line startup. When remote exporting is needed, this function builds the OpenTelemetry pieces and hands the finished logger provider to _install_root_handler, which attaches it to the process-wide logger.

*Call graph*: calls 1 internal fn (_install_root_handler); called by 1 (main); 4 external calls (OTLPLogExporter, LoggerProvider, BatchLogRecordProcessor, create).


##### `_install_root_handler`  (lines 52–57)

```
def _install_root_handler(logger_provider: LoggerProvider) -> None
```

**Purpose**: This connects standard Python logging to OpenTelemetry so ordinary log messages can be exported. It also protects against a feedback loop by excluding logs from OpenTelemetry itself.

**Data flow**: It receives a prepared logger provider. It builds a logging handler around that provider, adds a filter that rejects records whose logger name starts with 'opentelemetry', and attaches the handler to the root logger, which is the parent logger used by the whole process.

**Call relations**: _export_logs calls this after it has created the OpenTelemetry logger provider. From then on, any later command that logs through Python's normal logging system can also send records through the OpenTelemetry pipeline.

*Call graph*: called by 1 (_export_logs); 2 external calls (getLogger, LoggingHandler).


##### `gateway`  (lines 61–66)

```
def gateway() -> None
```

**Purpose**: This starts the hosted gateway web server. The gateway is the HTTP-facing part that serves onboarding, fleet count information, and the terminal client.

**Data flow**: It reads the desired port from the environment, falling back to the default port if none is set. It then starts Uvicorn, an ASGI web server, pointed at the gateway application, bound to all network interfaces on that port. The running server becomes the visible result.

**Call relations**: This command is invoked directly by an operator or deployment system through the Click command group. It hands control to Uvicorn, which imports and serves ufo_control.gateway:app for incoming web requests.

*Call graph*: 1 external calls (run).


##### `migrate`  (lines 70–73)

```
def migrate() -> None
```

**Purpose**: This command brings the control database schema up to the shape the service expects. It is used when installing or updating the hosted platform database.

**Data flow**: It asks for the database owner connection string, runs the schema-shaping routine inside an async event loop, and then prints a confirmation message. The database is changed so the control tables and related structures match the current code.

**Call relations**: An operator runs this as a maintenance command. It delegates the actual database work to shape_control_schema and uses owner_dsn to connect with the high-privilege database owner identity needed for schema changes.

*Call graph*: 4 external calls (run, echo, owner_dsn, shape_control_schema).


##### `invite`  (lines 78–83)

```
def invite(object_number: int) -> None
```

**Purpose**: This command creates a one-time invite for a new workspace and prints the complete email text containing the code. It is meant for an operator who needs to onboard a specific object number.

**Data flow**: It receives an object number from the command line. It runs _mint_invite to create the invite and build the email text, prints that text on success, or turns an invite-specific error into a Click-friendly command-line error message.

**Call relations**: This is the user-facing wrapper around _mint_invite. It keeps the command-line behavior simple while the helper does the async database and email-formatting work.

*Call graph*: calls 1 internal fn (_mint_invite); 3 external calls (run, ClickException, echo).


##### `_mint_invite`  (lines 86–96)

```
async def _mint_invite(object_number: int) -> str
```

**Purpose**: This creates the actual invite code and formats it as an email that can be sent to the invited workspace owner. It also verifies that the control database is ready before trying to write the invite.

**Data flow**: It receives an object number. It reads the public host name and owner database connection string, checks that the control schema exists, opens a small database connection pool, mints an invite code through InviteCodes, closes the pool, and then turns the minted code, object number, expiry time, and host into a subject and body. It returns one printable string containing the email subject and body.

**Call relations**: invite calls this when an operator requests a new invite. This helper coordinates several lower-level services: schema checking, database pooling, invite-code creation, host lookup, and email text generation.

*Call graph*: called by 1 (invite); 6 external calls (__init__, create_pool, invite_email, public_apex_host, owner_dsn, require_control_schema).


##### `slack_connect_retry`  (lines 101–107)

```
def slack_connect_retry(onboard_claim_id: uuid.UUID) -> None
```

**Purpose**: This command retries, or more precisely re-arms, a failed Slack Connect delivery for an onboarding claim after the original cause has been fixed. It gives the operator a clear success or failure message.

**Data flow**: It receives an onboarding claim ID from the command line. It runs _rearm_slack_connect to look for and re-arm a failed delivery. If none is found, it raises a command-line error. If one is found, it formats the original failure time in UTC and prints a confirmation.

**Call relations**: This is the command-line wrapper around _rearm_slack_connect. It translates the helper's result into operator-facing text and errors.

*Call graph*: calls 1 internal fn (_rearm_slack_connect); 3 external calls (run, ClickException, echo).


##### `_rearm_slack_connect`  (lines 110–117)

```
async def _rearm_slack_connect(onboard_claim_id: uuid.UUID) -> datetime | None
```

**Purpose**: This performs the database-backed work of making one failed Slack Connect delivery eligible to be sent again. It returns the time when that delivery originally failed, if such a failed delivery exists.

**Data flow**: It receives an onboarding claim ID. It gets the owner database connection string, checks that the control schema is present, opens a small connection pool, asks rearm_failed_delivery to update the failed delivery state, closes the pool, and returns either the failure timestamp or None.

**Call relations**: slack_connect_retry calls this after parsing the claim ID. This helper hands the specific retry decision and database update to rearm_failed_delivery, while taking care of connection setup and cleanup.

*Call graph*: called by 1 (slack_connect_retry); 4 external calls (create_pool, rearm_failed_delivery, owner_dsn, require_control_schema).


##### `rls_bootstrap`  (lines 121–124)

```
def rls_bootstrap() -> None
```

**Purpose**: This command installs the database role and row-level security policies needed for the shared service to safely serve multiple workspaces. It is a setup or maintenance command run by an operator.

**Data flow**: It runs _bootstrap inside an async event loop. Once the database role and policies have been created or confirmed, it prints a short success message.

**Call relations**: This is the command-line entry for database security bootstrapping. It delegates the actual work to _bootstrap, then reports completion to the operator.

*Call graph*: calls 1 internal fn (_bootstrap); 2 external calls (run, echo).


##### `_bootstrap`  (lines 127–130)

```
async def _bootstrap() -> None
```

**Purpose**: This applies the database security setup needed by the hosted gateway. It creates or updates shared access policies and ensures the serving role exists.

**Data flow**: It reads the owner database connection string. It then applies the row-level security policies and ensures the serve role is present. The output is not a returned value; the important result is the updated database security configuration.

**Call relations**: rls_bootstrap calls this as its async worker. This helper sequences bootstrap_policies before ensure_serve_role so the database is prepared for safe multi-workspace access.

*Call graph*: called by 1 (rls_bootstrap); 3 external calls (bootstrap_policies, ensure_serve_role, owner_dsn).


### `control/src/ufo_control/gateway.py`

`entrypoint` · `startup and request handling`

This file is the front door for UFO onboarding. It exposes a FastAPI web app, which is a Python web server framework, and uses it to answer health checks, serve the install script, show the web login page, and process onboarding messages from both terminal and web clients.

The main flow is like a receptionist at a secure office. First, the server asks for a work email. Then it sends and checks a verification code. After the email is proven, it decides whether the person's email domain already has a workspace. If not, and invites are required, it asks for an invite code before creating the workspace. Finally, it issues a signed token, gives the client the workspace address, and tells the user what to do next.

The `Onboarding` class holds that step-by-step conversation. `GatewayState` holds the live database pool and onboarding machinery, and can check whether the server is connected using the expected database roles. Startup is deliberately strict: required environment variables, database schema, and connection pools are prepared before the server accepts traffic. A background Slack Connect inviter may also be started, but onboarding still works if Slack is not configured. Request size limits are enforced so a client cannot send unexpectedly large channel names, sessions, or bodies.

#### Function details

##### `_stamp_script`  (lines 66–70)

```
def _stamp_script(text: str) -> str
```

**Purpose**: This prepares the install script that users download. It gives the script a content-based version number and replaces the default public URL with the configured public gateway URL.

**Data flow**: It takes the raw script text in. It hashes that text to make a short version stamp, reads the public base URL from the environment or uses the default, then substitutes those values into the script. The result is a ready-to-serve script string.

**Call relations**: This runs when the module is loaded to create `STAMPED_SCRIPT`. Later, the script-serving route returns that prepared text directly, so each request does not have to restamp it.

*Call graph*: 1 external calls (sha1).


##### `Onboarding.advance`  (lines 86–92)

```
async def advance(self, channel: str, session: str, body: str, install: bytes) -> bytes
```

**Purpose**: This is the traffic director for one step of the onboarding conversation. It looks at the user's current claim record and decides whether to ask for email, verify a code, or finish workspace sign-in.

**Data flow**: It receives a channel, session id, user message body, and optional install instructions. It asks the store whether there is an active claim for that channel and session. If there is no claim, it starts email collection; if the claim is not verified, it checks the code; if verified, it resolves the workspace and signs the user in. It returns bytes containing client directives, which are instructions the client can display or act on.

**Call relations**: The web and terminal onboarding routes call this after validating request size and session headers. It delegates to `_collect_email`, `_verify_code`, or `_resolve` depending on where the user is in the conversation.

*Call graph*: calls 3 internal fn (_collect_email, _resolve, _verify_code).


##### `Onboarding._collect_email`  (lines 94–112)

```
async def _collect_email(self, channel: str, session: str, body: str, install: bytes) -> bytes
```

**Purpose**: This starts onboarding by asking for a work email, or by accepting the email and sending a verification code. It protects the system from non-work or invalid email addresses through the claim workflow.

**Data flow**: It receives the channel, session, message body, and install bytes. If the body is empty, it returns directions that greet the user and ask for an email. If the body contains an email, it asks the claim workflow to start a claim and send a code. On errors, it returns the error message and asks again; on success, it tells the user a code was emailed and asks for that code.

**Call relations**: `Onboarding.advance` calls this when no live claim exists yet. It uses directive-building helpers to produce the small command-like messages understood by the terminal or web client.

*Call graph*: called by 1 (advance); 2 external calls (directive, render).


##### `Onboarding._verify_code`  (lines 114–121)

```
async def _verify_code(self, claim: OnboardClaim, body: str, install: bytes) -> bytes
```

**Purpose**: This checks the verification code that was emailed to the user. If the code is right, onboarding can move from identity proof to workspace access.

**Data flow**: It receives the saved claim, the user's message body, and install bytes. It asks the claim workflow to verify the claim against the submitted code. If verification fails, it returns a message asking for the code again. If it succeeds, it immediately continues into workspace resolution and returns that result.

**Call relations**: `Onboarding.advance` calls this when a claim exists but is not yet verified. On success it hands control to `_resolve`, so the user does not need to make an extra request just to continue.

*Call graph*: calls 1 internal fn (_resolve); called by 1 (advance); 2 external calls (directive, render).


##### `Onboarding._resolve`  (lines 123–138)

```
async def _resolve(self, claim: OnboardClaim, answer: str | None, install: bytes) -> bytes
```

**Purpose**: This turns a verified email claim into actual workspace access. It applies invite rules when needed, creates or finds the shared workspace, records completion, and prepares the signed-in response.

**Data flow**: It receives a verified claim, the user's current answer if any, and install bytes. If invites are required and the email domain has no workspace yet, it asks `_invite_gate` whether the user may continue. Once allowed, it ensures a workspace exists for the email domain, marks the claim complete with that workspace id, and returns the signed-in directives.

**Call relations**: This is reached from `Onboarding.advance` for already verified claims and from `_verify_code` after a successful code check. It may call `_invite_gate` first, then always finishes through `_signed_in` when access is granted.

*Call graph*: calls 2 internal fn (_invite_gate, _signed_in); called by 2 (_verify_code, advance); 1 external calls (directive).


##### `Onboarding._invite_gate`  (lines 140–181)

```
async def _invite_gate(self, claim: OnboardClaim, answer: str | None, install: bytes) -> bytes | InviteAccepted | None
```

**Purpose**: This enforces invite codes for brand-new workspaces. It lets existing invited claims continue, asks for a code when none was supplied, and explains expired, reused, unknown, or accepted codes.

**Data flow**: It receives the verified claim, the user's answer, and install bytes. If the claim already has an invite attached, it allows resolution to continue by returning nothing. If no answer was provided, it returns instructions asking for an invite. If a code was provided, it redeems it and returns either an accepted invite record or a response explaining why the code cannot be used.

**Call relations**: `_resolve` calls this only when invite gating applies: invites are required and no workspace exists yet for the email domain. It uses rendered directives to keep the onboarding conversation going until a valid invite is accepted.

*Call graph*: called by 1 (_resolve); 2 external calls (directive, render).


##### `Onboarding._signed_in`  (lines 183–211)

```
def _signed_in(self, claim: OnboardClaim, ensured: EnsuredWorkspace, install: bytes, accepted: bytes) -> bytes
```

**Purpose**: This creates the final successful onboarding response. It gives the client a login token, the workspace URL, and the next prompt or choice the user should see.

**Data flow**: It receives the completed claim, the ensured workspace record, install bytes, and any message about an accepted invite. It mints a signed token for the user's email and workspace, checks whether the email domain belongs to the operator, and renders directives for token, workspace, optional debugger URL, signed-in message, and next action. The output is the byte response sent back to the client.

**Call relations**: `_resolve` calls this after workspace creation or lookup and claim completion. It uses the token helper to create credentials and directive helpers to package the result for terminal or web clients.

*Call graph*: called by 1 (_resolve); 3 external calls (directive, render, mint_token).


##### `GatewayState.healthy`  (lines 221–229)

```
async def healthy(self) -> bool
```

**Purpose**: This checks whether the running gateway is connected to the database in the expected way. It verifies both the owner database role and the serving database role, so a miswired deployment can be marked unhealthy.

**Data flow**: It reads from the gateway's async database pool and from the shared workspace database connection. It asks each connection which database user is active. If either query fails, it logs the failure and returns false; otherwise it returns true only when both roles match the roles recorded at startup.

**Call relations**: The `/healthz` route calls this when a load balancer or operator checks the service. It reaches into the database layer through the async pool and `workspace_tx` transaction helper.

*Call graph*: 2 external calls (text, workspace_tx).


##### `_require_env`  (lines 232–236)

```
def _require_env(name: str) -> str
```

**Purpose**: This reads a required environment variable and fails clearly if it is missing. It prevents the gateway from starting in a half-configured state.

**Data flow**: It receives the environment variable name. It looks up that name in the process environment. If a non-empty value exists, it returns it; otherwise it raises a runtime error naming the missing setting.

**Call relations**: The startup lifespan calls this for settings such as the workspace base URL and token secret. That means configuration mistakes are caught before the app begins serving requests.

*Call graph*: called by 1 (lifespan).


##### `_invite_required`  (lines 239–248)

```
def _invite_required() -> bool
```

**Purpose**: This decides whether new workspace creation must be protected by invite codes. It defaults to requiring invites, so forgetting the setting does not accidentally open signup.

**Data flow**: It reads the invite-required environment variable, or uses `true` if it is not set. It accepts common true and false strings. It returns a boolean, and raises an error if the value is unclear.

**Call relations**: The startup lifespan calls this while building the onboarding object. The result controls whether `_resolve` asks `_invite_gate` to require an invite for new workspaces.

*Call graph*: called by 1 (lifespan).


##### `_dsn_role`  (lines 251–255)

```
def _dsn_role(dsn: str) -> str
```

**Purpose**: This extracts the database username, also called the role, from a database connection string. The gateway later uses that role name to confirm it is connected with the intended permissions.

**Data flow**: It receives a database DSN, which is a connection string. It parses the string with SQLAlchemy's URL parser and reads the username. It returns that username, or raises an error if the connection string has none.

**Call relations**: The startup lifespan calls this for both owner and serving database URLs. The saved role names are then used by `GatewayState.healthy` during health checks.

*Call graph*: called by 1 (lifespan).


##### `_request_body`  (lines 258–264)

```
async def _request_body(request: Request) -> str
```

**Purpose**: This safely reads the body of an onboarding HTTP request. It limits the body size so a client cannot send an unbounded amount of data to this endpoint.

**Data flow**: It receives a FastAPI request. It streams the request body in chunks, keeping only up to the allowed limit plus one byte for detection. If the body is too large, it raises an input error. Otherwise it decodes the bytes as UTF-8, replacing invalid characters, trims surrounding whitespace, and returns the resulting string.

**Call relations**: Both onboarding routes call this after checking headers and simple length limits. If it raises an input error, the route turns that into a friendly client directive instead of crashing the server.

*Call graph*: called by 2 (onboard, onboard_web); 2 external calls (__init__, stream).


##### `gateway_app`  (lines 267–393)

```
def gateway_app() -> FastAPI
```

**Purpose**: This builds and returns the FastAPI application used by the gateway service. It defines startup and shutdown behavior plus all HTTP routes in one place.

**Data flow**: It creates an initially empty shared state, defines the app lifespan, registers routes for health, install script, fleet count, login page, and onboarding, then returns the configured FastAPI app. The module assigns this return value to `app`, which is what an application server can run.

**Call relations**: This is the top-level factory for the whole file. Its nested lifespan function prepares shared resources, and its nested route functions use those resources when HTTP requests arrive.

*Call graph*: 1 external calls (FastAPI).


##### `gateway_app.lifespan`  (lines 271–323)

```
async def lifespan(app: FastAPI)
```

**Purpose**: This is the startup and shutdown wrapper for the gateway. It prepares configuration, database connections, onboarding services, and optional Slack invitation delivery before traffic is served, then cleans them up when the app stops.

**Data flow**: On startup, it reads database URLs and required environment settings, verifies the control schema, creates an async database pool, builds the store, invite system, claim workflow, shared workspace resolver, and gateway state, initializes the serving database connection, and optionally starts a background Slack task. It yields control while the app runs. On shutdown, it cancels the background task if present, clears state, disposes shared database resources, and closes the pool.

**Call relations**: FastAPI calls this automatically around the app's lifetime. It calls helpers such as `_invite_required`, `_require_env`, and `_dsn_role`, and it constructs the objects later used by the route functions.

*Call graph*: calls 3 internal fn (_dsn_role, _invite_required, _require_env); 18 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, create_task, gather, create_pool (+8 more)).


##### `gateway_app.healthz`  (lines 328–331)

```
async def healthz() -> Response
```

**Purpose**: This answers the service health check endpoint. It reports whether the gateway is ready and connected with the expected database roles.

**Data flow**: It reads the current gateway state. If state is missing or the health check fails, it returns a JSON response with status `unavailable` and HTTP status 503. If all checks pass, it returns JSON saying `ok`.

**Call relations**: External monitors or load balancers call this route. It depends on `GatewayState.healthy` to do the real database verification.

*Call graph*: 1 external calls (JSONResponse).


##### `gateway_app.serve_script`  (lines 334–335)

```
async def serve_script() -> Response
```

**Purpose**: This serves the UFO client install script. It gives users a shell script with the correct public URL and version stamp already inserted.

**Data flow**: It does not need request data. It returns the precomputed `STAMPED_SCRIPT` as plain text using a shell-script media type, so clients and browsers can recognize what kind of file it is.

**Call relations**: This route relies on `_stamp_script` having prepared the script when the module loaded. It is independent of the onboarding database state.

*Call graph*: 1 external calls (PlainTextResponse).


##### `gateway_app.fleet`  (lines 338–341)

```
async def fleet() -> Response
```

**Purpose**: This reports how many workspaces exist. It is a small status endpoint that exposes the current fleet size as a JSON number.

**Data flow**: It uses the gateway state's database pool to run a count query on the workspace table. It wraps the count in a JSON object named `craft` and returns it.

**Call relations**: This route is called through HTTP when someone wants a simple workspace count. It assumes startup has completed and the shared state exists.

*Call graph*: 1 external calls (JSONResponse).


##### `gateway_app.login`  (lines 344–345)

```
async def login() -> Response
```

**Purpose**: This serves the browser login page. It gives web users the HTML page that can drive the onboarding flow from a browser.

**Data flow**: It reads the imported login page constant and returns it as an HTML response. No database access or request body is needed.

**Call relations**: This route is part of the public web surface. The page it returns can later call the web onboarding route to continue sign-in.

*Call graph*: 1 external calls (HTMLResponse).


##### `gateway_app.onboard_web`  (lines 348–363)

```
async def onboard_web(request: Request) -> Response
```

**Purpose**: This processes one onboarding step from the web client. It returns structured JSON directives instead of the plain text directive stream used by the terminal client.

**Data flow**: It reads the `x-ufo-session` header, validates its length, reads the limited request body, and passes the web channel, session, and body to `state.onboarding.advance`. If input is bad or onboarding fails unexpectedly, it creates error directives. Finally it parses the directive bytes into JSON-friendly objects and returns them.

**Call relations**: The browser login experience calls this route. It uses `_request_body` for safe input reading and `Onboarding.advance` for the actual onboarding conversation.

*Call graph*: calls 1 internal fn (_request_body); 5 external calls (__init__, JSONResponse, directive, render, parse_directives).


##### `gateway_app.onboard`  (lines 366–391)

```
async def onboard(channel: str, request: Request) -> Response
```

**Purpose**: This processes one onboarding step from a non-web channel, especially the terminal client. It returns plain text directives that the client can read and act on.

**Data flow**: It reads the channel from the URL, the `x-ufo-session` header, and first-run install information from headers. It rejects missing sessions and overlong channel or session values, then safely reads the body and passes everything to `state.onboarding.advance`. If validation or unexpected errors occur, it renders a plain text response explaining the failure and telling the client to exit.

**Call relations**: Terminal or other channel clients call this route during onboarding. It uses `first_run_install` to include install-time instructions when needed, `_request_body` to protect the server from large bodies, and `Onboarding.advance` to move the user through the email, code, invite, and sign-in steps.

*Call graph*: calls 1 internal fn (_request_body); 5 external calls (__init__, PlainTextResponse, directive, first_run_install, render).


### Client conversation surfaces
These files provide the browser and terminal-client messaging surfaces used during the onboarding conversation.

### `control/src/ufo_control/gateway_directives.py`

`io_transport` · `request handling, especially the first screen of a terminal session`

This file is the server side of a tiny text-based instruction format. The terminal client receives lines like commands from the server, and each line starts with a verb such as `install`, followed by optional fields separated by tabs. Think of it like writing labeled notes to a helper program: each note says what action to take, and any extra details are packed safely into the same line.

The key job here is to turn ordinary strings into safe bytes that can travel over the network or HTTP response body. The `directive` function escapes special characters, such as tabs and newlines, so one field cannot accidentally look like two fields or two separate commands. `render` then glues multiple byte messages together into one response.

The file also contains a small HTTP header helper. HTTP headers are name-value pairs sent with a request, and their names are meant to be case-insensitive. `header_value` looks up a header without caring about uppercase or lowercase spelling.

Finally, `first_run_install` uses that header lookup to detect whether the UFO binary is already installed. If the client has not reported `x-ufo-installed: 1`, the server prepends an `install` directive. Without this, a first-time `curl | sh` session would not know to download the proper local binary before continuing.

#### Function details

##### `directive`  (lines 8–13)

```
def directive(verb: str, *fields: str) -> bytes
```

**Purpose**: Builds one server-to-client instruction line as bytes. It makes the fields safe to send by escaping characters that would otherwise break the simple tab-and-newline format.

**Data flow**: It receives a command word, called the verb, plus any number of text fields. It replaces backslashes, tabs, and newlines inside those fields with safe escaped versions, removes carriage returns, joins everything with tab characters, adds a final newline, and returns the result as bytes ready to send.

**Call relations**: When `first_run_install` decides a new user needs setup, it calls `directive` to create the actual `install` message. Other code can also use this as the basic builder for any directive sent to the terminal client.

*Call graph*: called by 1 (first_run_install).


##### `render`  (lines 16–17)

```
def render(*lines: bytes) -> bytes
```

**Purpose**: Combines several already-built directive byte strings into one byte string. This is useful when the server wants to send a screen or response containing multiple instructions.

**Data flow**: It receives any number of byte chunks. It joins them in order without adding anything extra, and returns the combined bytes.

**Call relations**: This is a simple packing step for the directive format. It does not call other helpers in this file; it assumes each input line has already been prepared, usually by something like `directive`.


##### `header_value`  (lines 20–25)

```
def header_value(headers: Mapping[str, str], name: str) -> str | None
```

**Purpose**: Finds a named HTTP header without caring about letter case. This matters because headers like `X-UFO-Installed` and `x-ufo-installed` should mean the same thing.

**Data flow**: It receives a mapping of header names to values and the header name to search for. It compares each existing header name in lowercase form, returns the matching value if it finds one, and returns `None` if the header is absent.

**Call relations**: The install decision in `first_run_install` relies on this helper to read `x-ufo-installed` reliably. By centralizing the case-insensitive lookup here, the rest of the code can ask a simple yes-or-no question without worrying about header spelling.

*Call graph*: called by 1 (first_run_install).


##### `first_run_install`  (lines 28–35)

```
def first_run_install(headers: Mapping[str, str]) -> bytes
```

**Purpose**: Decides whether to send an automatic install instruction to a fresh shell session. It prevents repeat installs by checking whether the client has already reported that UFO is installed.

**Data flow**: It receives the request headers. It looks for `x-ufo-installed`; if the value is exactly `1`, it returns empty bytes, meaning no extra instruction is needed. Otherwise, it returns an `install` directive as bytes, which tells the shell to download and set up the UFO binary before continuing.

**Call relations**: This function brings together `header_value` and `directive`. During the first response to a terminal session, it checks the client’s installation status, then either hands back nothing or hands off to `directive` to produce the `install` command the client will render and act on.

*Call graph*: calls 2 internal fn (directive, header_value).


### `control/src/ufo_control/gateway_web.py`

`io_transport` · `request handling`

This file is the web face of the onboarding flow. The important idea is that the browser does not run its own separate sign-in logic. Instead, it talks to the same onboarding state machine used by the terminal client. That keeps the rules in one place, so a user signing in from the web and a user signing in from the terminal follow the same path.

The file defines a web channel name, a helper that turns the onboarding machine’s plain text “directive” lines into JSON-friendly objects, and a complete HTML page stored as a string. A directive is a small instruction such as “say this message,” “ask this question,” “here is the token,” or “here is the workspace.” The browser page reads those instructions and updates the screen: it shows transcript lines, prompts for email or code, and finally displays the signed-in home card.

The page creates a random session id in the browser and sends it with each request. That session id ties the browser conversation to the correct onboarding claim row on the server. When onboarding succeeds, the page shows the user’s email, workspace URL, and a terminal install command. If the server sends a special debugger directive, the page shows a form that submits the token by POST, which is safer than putting the token in a URL.

#### Function details

##### `parse_directives`  (lines 15–24)

```
def parse_directives(payload: bytes) -> list[dict[str, object]]
```

**Purpose**: This function converts the onboarding machine’s line-based response into a list of simple dictionaries that can be returned as JSON to the browser. It preserves real tabs and newlines inside fields, even though the wire format uses tabs and newlines to separate pieces.

**Data flow**: It receives raw bytes from the onboarding response. It decodes them into text, splits the text into separate lines, skips empty lines, treats the first tab-separated part as the instruction word, and unescapes each remaining field. It returns a list like: instruction name plus its cleaned-up fields, ready for the web client to read.

**Call relations**: This is the bridge between the terminal-style directive stream and the browser’s JSON world. When it sees escaped field text, it hands each field to _unescape so the browser gets the original human-readable text rather than the safe-for-line-format version.

*Call graph*: calls 1 internal fn (_unescape).


##### `_unescape`  (lines 27–40)

```
def _unescape(field: str) -> str
```

**Purpose**: This helper reverses the small escaping scheme used inside directive fields. It turns sequences like backslash-t and backslash-n back into actual tab and newline characters.

**Data flow**: It receives one field as a string. It walks through the characters from left to right, replacing recognized escape pairs with their real character values and leaving everything else unchanged. It returns the restored string.

**Call relations**: This function is called by parse_directives for every field in every directive line. It is a small but important helper: without it, messages or values containing tabs, newlines, or backslashes would arrive in the browser in their encoded form instead of as the original text.

*Call graph*: called by 1 (parse_directives).


### Email verification claims
These files validate work emails, send verification codes, and persist temporary onboarding claims safely in Postgres.

### `control/src/ufo_control/gateway_claim.py`

`domain_logic` · `onboarding request handling`

This file protects the onboarding flow by proving that a person can receive email at a permitted work address. Without it, someone could claim a company domain or workspace connection without showing they control that email inbox.

The flow works like a temporary door code. When onboarding starts, `ClaimWorkflow.start` first asks the email policy whether the address is allowed. It then creates a random six-digit code, stores a claim record with an expiration time, and sends the plain code by email. Importantly, the system does not store the code itself. It stores a hash, which is a one-way fingerprint of the code. That means a database reader cannot simply see the verification code.

When the user submits a code, `ClaimWorkflow.verify` checks three things in order: whether too many attempts have already been made, whether the code has expired, and whether the submitted code matches the stored hash. Failed or expired claims are cleaned up when appropriate. Incorrect attempts are counted so guessing is limited.

The file also defines `ClaimError`, a user-safe failure type for cases like expired codes, too many attempts, or email delivery failure. The wider onboarding system can turn these errors into friendly messages without exposing secrets or low-level details.

#### Function details

##### `hash_code`  (lines 27–28)

```
def hash_code(code: str) -> str
```

**Purpose**: Turns a verification code into a one-way fingerprint using SHA-256, a standard hashing method. This lets the system compare codes later without storing the real code in the database.

**Data flow**: A plain text code goes in. The function encodes it as bytes, hashes it, and returns the hash as text. Nothing outside the function is changed.

**Call relations**: When a claim starts, `ClaimWorkflow.start` uses this to store only the code fingerprint. Later, `ClaimWorkflow.verify` uses it again on the user-entered code so the two fingerprints can be compared instead of comparing or storing the raw secret.

*Call graph*: called by 2 (start, verify); 1 external calls (sha256).


##### `ClaimWorkflow.start`  (lines 39–61)

```
async def start(self, email: str, surface: str, surface_ref: str) -> str
```

**Purpose**: Begins an email verification claim for onboarding. It checks that the email address is allowed, creates a short-lived code, records the claim, and sends the code to the user's email inbox.

**Data flow**: An email address, a surface name, and a surface reference go in. The function validates the email domain, creates a random six-digit code, hashes that code, builds an onboarding claim with an expiry time and zero attempts, saves it, and sends the code by email. If sending fails, it deletes the saved claim and raises a clear `ClaimError`; otherwise it returns the validated email domain.

**Call relations**: This is called when a user starts the onboarding verification step. It relies on `hash_code` so the stored claim contains only a code fingerprint, uses outside helpers to generate randomness, time, and a unique claim id, and hands the finished claim to the onboarding store and email sender. If something goes wrong during delivery, it converts that failure into a `ClaimError` the onboarding flow can show safely to the user.

*Call graph*: calls 1 internal fn (hash_code); 5 external calls (__init__, __init__, now, randbelow, uuid4).


##### `ClaimWorkflow.verify`  (lines 63–73)

```
async def verify(self, claim: OnboardClaim, code: str) -> None
```

**Purpose**: Checks a user's submitted verification code against an existing claim. It also enforces the safety rules: a code expires after a short time, and the user only gets a limited number of tries.

**Data flow**: An existing claim and a submitted code go in. The function first checks whether the attempt limit has already been reached, then checks whether the claim has expired. If either is true, it deletes the claim and raises `ClaimError`. Otherwise it records one more attempt, hashes the submitted code, compares it safely with the stored hash, and either raises an incorrect-code error or marks the claim as verified.

**Call relations**: This runs after a user enters the code they received by email. It uses `hash_code` to turn the submitted code into the same kind of fingerprint stored by `ClaimWorkflow.start`, and uses a timing-safe comparison so the check does not leak useful clues. On success it hands the result back to the store by marking the claim verified; on failure it raises `ClaimError` for the onboarding flow to report.

*Call graph*: calls 1 internal fn (hash_code); 3 external calls (__init__, now, compare_digest).


### `control/src/ufo_control/gateway_email.py`

`io_transport` · `request handling`

This file supports the gateway flow where a person proves control of a company email address. First, it rejects addresses that are malformed or belong to common personal and disposable providers, because the workspace is meant to map to a real organization rather than a throwaway inbox. Then it prepares short verification or invite messages containing a code and an expiry time.

For real delivery, the file talks directly to Amazon SES, Amazon’s email service. It does not use a full AWS client library. Instead, it makes async HTTP requests with `httpx`, and signs the SES request itself using AWS SigV4, which is Amazon’s standard request-signing method. Before sending, it exchanges the pod’s web identity token for temporary AWS credentials through STS, Amazon’s token service. Think of STS as the front desk that swaps a badge from Kubernetes for a temporary mailroom pass.

The file also has a console sender for local work. In that mode, the same email text is created, but the code is written to the process log instead of being sent. Configuration comes from environment variables, and missing or unknown settings fail loudly so email delivery problems are noticed early.

#### Function details

##### `normalize_email`  (lines 122–128)

```
def normalize_email(email: str) -> tuple[str, str]
```

**Purpose**: This function cleans up an email address and checks that it has a basic valid shape. It returns both the normalized full address and the domain part, or raises a clear error if the address is unusable.

**Data flow**: It receives a raw email string, trims surrounding spaces, lowercases it, and matches it against a simple email pattern. If the pattern fits, it returns the cleaned address and the domain after the `@`; if not, it stops with a `WorkEmailError`.

**Call relations**: The work-email policy calls this first before judging the domain. It is the front door that keeps malformed input from reaching the domain denylist check.

*Call graph*: called by 1 (validate); 1 external calls (__init__).


##### `WorkEmailPolicy.validate`  (lines 135–139)

```
def validate(self, email: str) -> str
```

**Purpose**: This method checks whether an address belongs to an allowed work domain. It rejects personal or disposable email domains so a signup is tied to an organization rather than a free inbox.

**Data flow**: It takes an email address, asks `normalize_email` to clean it and extract the domain, then compares that domain with the denylist. If the domain is blocked, it raises `WorkEmailError`; otherwise it returns the accepted domain.

**Call relations**: This method builds directly on `normalize_email`. It is the policy decision point: callers use it when they need to know whether an email address is acceptable for a workspace.

*Call graph*: calls 1 internal fn (normalize_email); 1 external calls (__init__).


##### `public_apex_host`  (lines 142–150)

```
def public_apex_host() -> str
```

**Purpose**: This function finds the public host name users should install from, such as `flyingobject.ai`. It is used when composing invite text that includes a public command URL.

**Data flow**: It reads `UFO_PUBLIC_BASE_URL` from the environment, or uses the default public base URL if unset. It parses the URL, strips away the scheme and path, and returns just the host; if no host can be found, it raises an error.

**Call relations**: No in-file caller is shown, but it pairs with `invite_email`: outside code can get the public host here, then place it into the invite message.

*Call graph*: 1 external calls (urlsplit).


##### `verification_email`  (lines 153–159)

```
def verification_email(code: str, expires_at: datetime, ttl: timedelta) -> tuple[str, str]
```

**Purpose**: This function creates the subject and plain-text body for a verification-code email. It keeps the real sender and console sender using identical wording.

**Data flow**: It receives a code, an expiry time, and a time-to-live duration. It formats the expiry as an hour and minute, converts the duration to minutes, and returns a subject/body pair.

**Call relations**: Both `SesEmailSender.send` and `ConsoleEmailSender.send` call this before delivering or logging a code. That shared path prevents local testing from showing a different message than production email.

*Call graph*: called by 2 (send, send); 2 external calls (strftime, total_seconds).


##### `invite_email`  (lines 162–172)

```
def invite_email(object_number: int, code: str, expires_at: datetime, apex_host: str) -> tuple[str, str]
```

**Purpose**: This function creates the subject and body for an invite-code message. The text includes an object number, a code, an expiry time, and an install command.

**Data flow**: It receives the object number, invite code, expiry time, and public host. It converts the expiry to UTC, formats the message template, and returns the subject/body pair.

**Call relations**: No in-file caller is shown. It is meant for invite-related code outside this file, such as an operator command that prints a message to send manually.

*Call graph*: 1 external calls (astimezone).


##### `EmailSender.send`  (lines 176–176)

```
async def send(self, email: str, code: str, expires_at: datetime, ttl: timedelta) -> None
```

**Purpose**: This is the shared shape, or protocol, for anything that can send a verification code. It lets the rest of the system ask for an email to be sent without caring whether it goes to SES or to the console log.

**Data flow**: A caller provides the recipient email, code, expiry time, and time-to-live duration. Concrete senders then decide how to turn that information into delivery; this protocol itself does not produce a result.

**Call relations**: The concrete implementations are `SesEmailSender.send` for real email and `ConsoleEmailSender.send` for local logging. `email_sender_from_env` returns one of those implementations while exposing this common interface.


##### `SesEmailSender.send`  (lines 199–221)

```
async def send(self, email: str, code: str, expires_at: datetime, ttl: timedelta) -> None
```

**Purpose**: This method sends a verification code as a real email through Amazon SES. It is used in production-like settings where the user should receive an actual message.

**Data flow**: It receives the recipient, code, expiry time, and duration. It builds the email text, obtains temporary AWS credentials by calling `_assume_role`, creates the SES JSON request body, signs the request with `_sigv4_headers`, posts it over HTTPS, and raises an error if SES reports failure.

**Call relations**: This is the main real-delivery path selected by `email_sender_from_env` when email mode is `ses`. It hands off message wording to `verification_email`, credential exchange to `_assume_role`, request signing to `_sigv4_headers`, and network delivery to `httpx`.

*Call graph*: calls 3 internal fn (_assume_role, _sigv4_headers, verification_email); 3 external calls (now, AsyncClient, dumps).


##### `SesEmailSender._assume_role`  (lines 223–242)

```
async def _assume_role(self) -> SesCredentials
```

**Purpose**: This method gets temporary AWS credentials that are allowed to send email. It uses the pod’s web identity token instead of relying on long-lived secrets.

**Data flow**: It reads the web identity token from the configured token file, posts it to AWS STS with the role ARN and session details, and checks the response. If STS succeeds, it parses the returned XML into `SesCredentials`; if STS fails, it raises an error with part of the response body.

**Call relations**: `SesEmailSender.send` calls this before every SES request because SES needs signed requests. After the STS network call returns, this method hands the XML response to `_parse_assume_role_credentials`.

*Call graph*: calls 1 internal fn (_parse_assume_role_credentials); called by 1 (send); 1 external calls (AsyncClient).


##### `_parse_assume_role_credentials`  (lines 245–258)

```
def _parse_assume_role_credentials(payload: str) -> SesCredentials
```

**Purpose**: This helper extracts AWS access credentials from an STS XML response. It turns a raw response document into a small credentials object the signer can use.

**Data flow**: It receives the XML payload as text, parses it into an XML tree, and looks for the access key, secret key, and session token fields. If any field is missing, it raises an error; otherwise it returns a `SesCredentials` value.

**Call relations**: `SesEmailSender._assume_role` calls this after STS accepts the web identity token. The credentials it returns then flow back to `SesEmailSender.send` for signing the SES request.

*Call graph*: called by 1 (_assume_role); 2 external calls (__init__, fromstring).


##### `_parse_assume_role_credentials.credential`  (lines 248–252)

```
def credential(name: str) -> str
```

**Purpose**: This small inner helper reads one named credential field from the parsed STS XML. It centralizes the repeated missing-field check.

**Data flow**: It receives a credential field name, searches the already-parsed XML response for that value, and returns the text if present. If the field is absent or empty, it raises an error naming the missing field.

**Call relations**: It is used only inside `_parse_assume_role_credentials`, once for each required credential value. This keeps the outer parser simple and makes malformed STS responses fail clearly.


##### `_sigv4_headers`  (lines 261–294)

```
def _sigv4_headers(host: str, body: bytes, region: str, credentials: SesCredentials, now: datetime) -> dict[str, str]
```

**Purpose**: This function creates the HTTP headers Amazon requires to trust an SES request. It performs AWS SigV4 signing, which proves the request body was made by someone holding the temporary secret key.

**Data flow**: It receives the SES host, request body, AWS region, temporary credentials, and current time. It hashes the body, builds the canonical request string AWS expects, derives a signing key with `_signing_key`, computes the signature, and returns headers including the authorization value.

**Call relations**: `SesEmailSender.send` calls this right before posting to SES. It depends on `_signing_key` for the derived signing secret, then hands the finished headers back to the sender for the network request.

*Call graph*: calls 1 internal fn (_signing_key); called by 1 (send); 3 external calls (strftime, sha256, new).


##### `_signing_key`  (lines 297–301)

```
def _signing_key(secret_key: str, date_stamp: str, region: str) -> bytes
```

**Purpose**: This helper derives the short-lived key used for AWS SigV4 request signing. It transforms the AWS secret key into a key scoped to one date, region, and service.

**Data flow**: It starts with the secret key and repeatedly applies HMAC-SHA256, which is a standard keyed hashing method, using the date, region, SES service name, and final AWS request marker. The result is raw bytes suitable for signing the final request string.

**Call relations**: `_sigv4_headers` calls this while building the authorization header. It does not send anything itself; it only supplies the cryptographic key material needed by the signer.

*Call graph*: called by 1 (_sigv4_headers); 1 external calls (new).


##### `ConsoleEmailSender.send`  (lines 311–313)

```
async def send(self, email: str, code: str, expires_at: datetime, ttl: timedelta) -> None
```

**Purpose**: This method pretends to send a verification email by writing the recipient, subject, and code text to the application log. It is useful for local development where no SES account is available.

**Data flow**: It receives the same inputs as the real sender: email, code, expiry time, and duration. It builds the same verification message with `verification_email`, then logs the result instead of making a network call.

**Call relations**: `email_sender_from_env` returns this sender when `UFO_CONTROL_EMAIL_MODE` is set to `console`. It shares the message-building function with the SES sender so local logs show the same content a real user would receive.

*Call graph*: calls 1 internal fn (verification_email).


##### `email_sender_from_env`  (lines 316–330)

```
def email_sender_from_env() -> EmailSender
```

**Purpose**: This function chooses which email sender the application should use based on environment variables. It gives one clear setup point for production email versus local console mode.

**Data flow**: It reads the email mode from the environment. For console mode, it returns a `ConsoleEmailSender`; for SES mode, it requires the sender address, role ARN, token file path, and optional region, then returns a configured `SesEmailSender`. If the mode is unknown or required settings are missing, it raises an error.

**Call relations**: Startup or setup code can call this once to get an `EmailSender`. It relies on `_require_env` to fail loudly for missing SES settings, then hands back the concrete sender that later receives verification-code send requests.

*Call graph*: calls 1 internal fn (_require_env); 3 external calls (__init__, __init__, Path).


##### `_require_env`  (lines 333–337)

```
def _require_env(name: str) -> str
```

**Purpose**: This helper reads a required environment variable and turns a missing value into a clear runtime error. It prevents the SES sender from being created with half-missing configuration.

**Data flow**: It receives the variable name, looks it up in the process environment, and returns the value if it is present and non-empty. If not, it raises an error explaining which setting is required.

**Call relations**: `email_sender_from_env` calls this while constructing the SES sender. It acts like a checklist item for configuration that must be present before real email delivery can work.

*Call graph*: called by 1 (email_sender_from_env).


### `control/src/ufo_control/gateway_store.py`

`io_transport` · `onboarding request handling`

When a person starts onboarding, the system needs a safe place to remember the email, verification code hash, where the onboarding came from, how many attempts have been made, and whether the process has finished. This file provides that safe place using Postgres, a relational database. Think of it like the front desk logbook for onboarding: each claim is written down, checked later, stamped as verified, and finally marked complete or removed.

The file defines the table layout through `DDL`, including a rule that only one unfinished claim can exist for the same onboarding “surface” and reference. A surface is the place or channel where onboarding began, and the reference identifies the specific session or source within it. That uniqueness rule prevents two active onboarding claims from fighting over the same session.

`OnboardClaim` is the plain data shape used by the rest of the code. `OnboardStore` wraps an asyncpg database connection pool, which is a reusable set of database connections for asynchronous code. Its methods perform the actual database actions: insert a new claim, look up the live claim for a surface, record failed or repeated attempts, mark a claim as verified, mark it as completed with a workspace ID, or delete it. The small `_aware` helper makes sure timestamps read from the database include timezone information, so time comparisons do not accidentally mix timezone-aware and timezone-naive values.

#### Function details

##### `_aware`  (lines 46–49)

```
def _aware(value: datetime | None) -> datetime | None
```

**Purpose**: This helper makes sure a timestamp has timezone information. It protects the rest of the onboarding flow from subtle time bugs caused by mixing timestamps that know their timezone with timestamps that do not.

**Data flow**: It receives either a datetime value or nothing. If there is no value, it returns nothing. If the datetime already has timezone information, it returns it unchanged; otherwise it adds UTC as the timezone and returns the adjusted value.

**Call relations**: When `OnboardStore.live_claim` reads timestamps from Postgres, it passes them through `_aware` before building an `OnboardClaim`. `_aware` may use the datetime object's `replace` method to attach UTC when the database value came back without timezone details.

*Call graph*: called by 1 (live_claim); 1 external calls (replace).


##### `OnboardStore.insert_claim`  (lines 56–70)

```
async def insert_claim(self, claim: OnboardClaim) -> None
```

**Purpose**: This method writes a new onboarding claim into the database. It is used when someone starts the onboarding process and the system needs to remember the verification details for later.

**Data flow**: It receives an `OnboardClaim` object containing the claim ID, email, email domain, hashed code, source surface, source reference, attempt count, and expiry time. It borrows a database connection from the pool, inserts those fields into the onboarding claim table, and returns no value after the database accepts the row.

**Call relations**: This is the entry point into storage for a fresh claim. Later steps in the onboarding flow can find that same record with `OnboardStore.live_claim`, update it after attempts or verification, and eventually complete or delete it.


##### `OnboardStore.live_claim`  (lines 72–93)

```
async def live_claim(self, surface: str, surface_ref: str) -> OnboardClaim | None
```

**Purpose**: This method looks up the currently active onboarding claim for a given source. It only returns a claim that has not yet produced a workspace, so completed claims are ignored.

**Data flow**: It receives a surface and surface reference, asks the database for the matching unfinished row, and returns `None` if there is no active claim. If a row is found, it turns the database fields into an `OnboardClaim`, cleaning up timestamp timezone information with `_aware` before returning it.

**Call relations**: This is used when the system needs to continue an onboarding session, such as checking a submitted code or deciding whether a session already has an open claim. It calls `_aware` for timestamp safety and constructs the `OnboardClaim` object that the rest of the onboarding code can work with.

*Call graph*: calls 1 internal fn (_aware); 1 external calls (__init__).


##### `OnboardStore.record_attempt`  (lines 95–96)

```
async def record_attempt(self, claim_id: UUID, attempts: int) -> None
```

**Purpose**: This method updates how many verification attempts have been made for a claim. It helps enforce limits or track repeated failed code submissions.

**Data flow**: It receives a claim ID and the new attempt count. It passes an update instruction to the shared `_update` helper, which writes the new attempt count into the database row for that claim. It returns no value.

**Call relations**: This is a small, named wrapper around `_update`. Higher-level onboarding code can call `record_attempt` without needing to know the exact database column or SQL used to store the attempt count.

*Call graph*: calls 1 internal fn (_update).


##### `OnboardStore.mark_verified`  (lines 98–99)

```
async def mark_verified(self, claim_id: UUID) -> None
```

**Purpose**: This method records that an onboarding claim has successfully passed verification. It stamps the claim with the current database time.

**Data flow**: It receives a claim ID. It asks `_update` to set the claim's `verified_at` field to the database server's current time, then returns no value once the update is sent.

**Call relations**: This is called after the onboarding flow has accepted the user's verification proof, such as a correct code. It delegates the actual database update to `_update`, keeping the public method focused on the business meaning: this claim is now verified.

*Call graph*: calls 1 internal fn (_update).


##### `OnboardStore.complete`  (lines 101–102)

```
async def complete(self, claim_id: UUID, resulting_workspace_id: str) -> None
```

**Purpose**: This method marks an onboarding claim as finished by attaching the workspace that resulted from it. Once this field is set, the claim is no longer considered live.

**Data flow**: It receives a claim ID and the resulting workspace ID. It sends those to `_update`, which writes the workspace ID into the matching database row. It returns no value.

**Call relations**: This is used near the end of a successful onboarding flow, after a workspace has been created or assigned. Because `live_claim` only searches for claims whose resulting workspace is still empty, completing a claim naturally removes it from future live-claim lookups.

*Call graph*: calls 1 internal fn (_update).


##### `OnboardStore.delete_claim`  (lines 104–106)

```
async def delete_claim(self, claim_id: UUID) -> None
```

**Purpose**: This method removes an onboarding claim from the database. It is useful when a claim should be cancelled, cleaned up, or discarded instead of completed.

**Data flow**: It receives a claim ID, borrows a database connection from the pool, deletes the matching row from the onboarding claim table, and returns no value.

**Call relations**: This provides the direct cleanup path for onboarding claims. Unlike `complete`, which keeps a historical row with a resulting workspace ID, `delete_claim` removes the row entirely.


##### `OnboardStore._update`  (lines 108–112)

```
async def _update(self, assignment: str, claim_id: UUID, *values: object) -> None
```

**Purpose**: This private helper performs the repeated database update pattern used by several public methods. It keeps common update mechanics in one place so the public methods can stay simple and meaningful.

**Data flow**: It receives a SQL assignment fragment, a claim ID, and any extra values needed by that assignment. It borrows a database connection, builds an update statement for the onboarding claim table, applies it to the row with the given ID, and returns no value.

**Call relations**: `record_attempt`, `mark_verified`, and `complete` all call `_update` when they need to change one field on a claim. The public methods decide what the change means, while `_update` performs the shared database write.

*Call graph*: called by 3 (complete, mark_verified, record_attempt).


### Workspace access provisioning
These files enforce invite rules, map domains to shared workspaces, create user bearer tokens, and run first-time workspace setup.

### `control/src/ufo_control/gateway_invite.py`

`domain_logic` · `admin invite creation and workspace signup`

This file is the gatekeeper for workspace creation by invite. The real-world problem is simple: someone on a waitlist may be allowed to create a workspace, but the system must not let the same invite be reused, guessed, or accidentally consumed twice during a race between two requests.

It defines the database table for invite codes, the shape of the possible results, and the main InviteCodes service. A newly minted invite is like a paper ticket: the user sees the ticket code once, but the system stores only a fingerprint of it, not the ticket text itself. That fingerprint is a hash, meaning a one-way scrambled value used for comparison without keeping the original secret.

When minting, the code checks whether the waitlist object already has a live invite or has already been identified. If so, it refuses. Otherwise it deletes stale unused codes for that object, creates a fresh code, stores its hash with an expiry time, and returns the visible code to be sent to the user.

When redeeming, it looks up the hashed code inside a database transaction. It locks the row while checking it, like putting a hand on the ticket while deciding whether it is still valid. That prevents two simultaneous signups from both using the same invite. If valid, it marks the invite as consumed and attaches it to the claim in the same transaction, so the system cannot end up with a used invite that is not tied to the workspace request.

#### Function details

##### `mint_code`  (lines 46–50)

```
def mint_code() -> str
```

**Purpose**: Creates a human-enterable invite code. The code uses a limited alphabet that avoids easily confused characters, then groups the characters with dashes so it is easier to read and type.

**Data flow**: It takes no input. It repeatedly chooses random characters from the invite alphabet, builds three groups of four characters, joins the groups with dashes, and returns the finished code as text.

**Call relations**: InviteCodes.mint calls this when it has decided to try creating a new invite. The generated code is returned to the operator, while only its hash is stored in the database.

*Call graph*: called by 1 (mint); 1 external calls (choice).


##### `hash_invite`  (lines 53–54)

```
def hash_invite(code: str) -> str
```

**Purpose**: Turns an invite code into a stable fingerprint for safe storage and lookup. This lets the database compare codes without keeping the original secret code.

**Data flow**: It receives the code text, trims extra spaces, makes it lowercase, encodes it as bytes, and runs it through SHA-256, a standard one-way hashing method. It returns the hash as a hexadecimal text string.

**Call relations**: InviteCodes.mint uses this before saving a new code, and InviteCodes.redeem uses it before searching for a submitted code. Because both paths normalize the text the same way, users can type the code with different capitalization or surrounding spaces and still match.

*Call graph*: called by 2 (mint, redeem); 1 external calls (sha256).


##### `InviteCodes.mint`  (lines 91–130)

```
async def mint(self, object_number: int) -> MintedInvite
```

**Purpose**: Creates a new invite for a specific waitlist object, but only if that object is allowed to receive one. It prevents duplicate live invites and refuses to mint for an object that has already been identified.

**Data flow**: It receives an object number. It makes a fresh code, calculates the expiry time, opens a database transaction, and checks whether that object already has a consumed invite or an unexpired invite. If the object is already identified or still has a live code, it raises InviteError. Otherwise it removes old expired unused codes for that object, inserts the new invite hash, and returns a MintedInvite containing the visible code, object number, and expiry time.

**Call relations**: This is called by the invite-issuing flow, such as an admin command that prepares an invite email. It relies on mint_code to make the visible secret and hash_invite to store only a fingerprint. The database uniqueness rules back it up if two minting attempts race at the same time.

*Call graph*: calls 2 internal fn (hash_invite, mint_code); 4 external calls (__init__, __init__, now, uuid4).


##### `InviteCodes.redeem`  (lines 132–161)

```
async def redeem(self, code: str, claim_id: UUID) -> InviteUnknown | InviteExpired | InviteConsumed | InviteAccepted
```

**Purpose**: Checks a submitted invite code and, if it is valid, consumes it for a claim. It returns a clear result for each outcome: unknown, expired, already used, or accepted.

**Data flow**: It receives the typed code and the claim ID that is trying to use it. It hashes the code, looks up the matching invite row, and locks that row during the decision. If no row exists, it returns InviteUnknown. If the invite was already consumed, it returns InviteConsumed. If it is past its expiry time, it returns InviteExpired. If it is valid, it records the consumption time, writes the invite ID onto the claim record, and returns InviteAccepted with the invite ID, object number, and consumption time.

**Call relations**: This is used during the workspace creation path when a new claim must prove it has a valid invite. It calls hash_invite so the submitted code can be compared to stored hashes. Its transaction keeps the consume-and-attach steps together, so later workspace creation retries do not need to ask for the code again.

*Call graph*: calls 1 internal fn (hash_invite); 5 external calls (__init__, __init__, __init__, __init__, now).


### `control/src/ufo_control/gateway_shared.py`

`domain_logic` · `hosted onboarding`

This file solves a common onboarding problem: if several people sign up with the same verified organization domain, they should land in the same shared workspace instead of creating scattered, duplicate spaces. It treats the domain like a stable address label. For a brand-new domain, it creates a predictable workspace ID from the domain name, so repeated attempts point to the same place. For an existing domain, it looks up the workspace that already belongs to it.

The main class, SharedWorkspaces, uses a database connection pool to check and create records. Its public methods answer two questions: “does this domain already have a workspace?” and “make sure this email address belongs to the right workspace.” When ensuring a workspace, it creates the workspace row if needed, adds the email address as a member, creates the default agent for that workspace if it is not already there, and then checks whether this member is the first member. That first-member check matters because only the owner should be prompted to do owner-only tasks like billing.

A key safety rule is in _existing: if the same domain appears to point to more than one workspace, the code raises an error instead of guessing. That avoids silently putting users into the wrong shared space.

#### Function details

##### `serve_dsn`  (lines 20–27)

```
def serve_dsn() -> str
```

**Purpose**: This reads the database connection string used by hosted onboarding when it needs to write workspace data as the special serve role. If the setting is missing, it stops immediately with a clear error instead of letting onboarding fail later in a confusing way.

**Data flow**: It reads the UFO_CONTROL_SERVE_DSN environment variable from the process environment. If the value exists, it returns that text. If it is empty or missing, it raises a RuntimeError explaining that hosted onboarding needs this database connection string to write the workspace row correctly.

**Call relations**: This function is a small configuration gate for the onboarding path. Other setup code can call it before creating database access for shared workspaces, so the system fails early if the required connection information has not been provided.


##### `SharedWorkspaces.exists`  (lines 47–48)

```
async def exists(self, domain: str) -> bool
```

**Purpose**: This answers a simple yes-or-no question: does this verified domain already map to a shared workspace? It is useful when onboarding wants to know whether a domain has been seen before without changing anything.

**Data flow**: It receives a domain name as text. It asks _existing to search for a matching workspace. If _existing returns a workspace ID, exists returns true; if _existing returns nothing, it returns false. It does not create or modify any database records.

**Call relations**: This is a read-only wrapper around SharedWorkspaces._existing. Callers use it when they only need to check the state, while _existing does the actual database lookup and consistency check.

*Call graph*: calls 1 internal fn (_existing).


##### `SharedWorkspaces.ensure`  (lines 50–77)

```
async def ensure(self, domain: str, email: str) -> EnsuredWorkspace
```

**Purpose**: This makes sure an email address belongs to the one shared workspace for its verified domain. It either joins the existing workspace or creates the workspace, member, and default agent needed for a new domain.

**Data flow**: It receives a domain and an email address. First it looks for an existing workspace for the domain. If none exists, it creates a deterministic UUID from the lowercased domain, meaning the same domain always produces the same workspace ID. It normalizes the email address by trimming spaces and lowercasing it. Inside the workspace context and a database transaction, it inserts the workspace if it is missing, creates or finds the member, inserts the default agent if needed, and asks which member owns the workspace. It returns an EnsuredWorkspace containing the workspace ID as text and a true-or-false owner flag.

**Call relations**: This is the main write path for hosted onboarding. It first depends on SharedWorkspaces._existing to avoid duplicates. It then uses the workspace transaction helper to group database changes together, create_member to add the person, and owner_member_id to decide whether this person is the workspace owner. At the end it packages that result into EnsuredWorkspace so the onboarding flow can decide what to show next.

*Call graph*: calls 1 internal fn (_existing); 8 external calls (__init__, insert, workspace_tx, create_member, owner_member_id, ws, uuid4, uuid5).


##### `SharedWorkspaces._existing`  (lines 79–97)

```
async def _existing(self, domain: str) -> UUID | None
```

**Purpose**: This finds the workspace that already belongs to a domain, if one exists. It also protects the system from a dangerous ambiguous case where one domain appears tied to multiple workspaces.

**Data flow**: It receives a domain, lowercases it, and builds the deterministic UUID that would be used for that domain’s workspace. Then it queries the database in two ways: it checks whether a workspace with that deterministic ID exists, and it checks whether the first member of any workspace has an email address ending in that domain. If the query finds one matching workspace, it returns that workspace ID. If it finds none, it returns nothing. If it finds more than one, it raises an error because the domain mapping is no longer safe to assume.

**Call relations**: This is the shared lookup helper behind both public operations. SharedWorkspaces.exists calls it for a read-only check, and SharedWorkspaces.ensure calls it before deciding whether to create a new workspace or reuse an old one.

*Call graph*: called by 2 (ensure, exists); 1 external calls (uuid5).


### `control/src/ufo_control/gateway_token.py`

`domain_logic` · `authentication token creation`

This file is a small bridge between the control service and the shared token-making code. The real problem it solves is consistency: the gateway needs to mint a token that other parts of the system can later verify, and both sides must agree on the exact signed shape. If each part built tokens in its own way, small differences could make valid users look unauthorized.

The token here is a bearer token, meaning whoever presents it is treated as the authenticated user, much like a ticket at an event. It contains claims such as the workspace, the member email, and an expiry time. The expiry is fixed here as 30 days, so hosted member credentials do not last forever.

The file also names the expected environment variable for the signing secret: `UFO_TOKEN_SECRET`. A signing secret is a private value used to create an HMAC, which is a cryptographic seal proving the token was made by someone who knows the secret and was not changed afterward.

Instead of building the token itself, this file calls the shared `ufo.bearer.mint_token` function. That keeps token creation and token verification tied to the same codec, reducing the chance that the “printer” and the “scanner” disagree about what a valid ticket looks like.

#### Function details

##### `mint_token`  (lines 13–14)

```
def mint_token(secret: str, workspace_id: str, email: str, now: datetime | None=None) -> str
```

**Purpose**: Creates a signed gateway token for a hosted member. A caller gives it the secret, workspace ID, member email, and optionally the current time, and it returns a token string that can be stored and later presented for verification.

**Data flow**: The function receives a signing secret, a workspace identifier, an email address, and optionally a timestamp to treat as “now.” It adds this file’s fixed 30-day lifetime, then passes everything to the shared bearer-token maker. The result is a signed text token; the function does not store it or change any outside state.

**Call relations**: When the control side needs to issue credentials for a member, it calls this wrapper rather than constructing the token directly. This function immediately hands the work to `ufo.bearer.mint_token`, which performs the actual encoding and signing, so the produced token matches the format expected by the verification side.

*Call graph*: 1 external calls (mint_token).


### `core/src/ufo/onboarding.py`

`orchestration` · `startup / first-run initialization`

This file is the “move-in checklist” for a brand-new UFO installation. Without it, the system would not have a workspace, an owner account, or a default assistant agent to start from. It also prevents a dangerous mistake: running setup twice against an already-initialized database and accidentally creating duplicate core records.

The main flow is wrapped in the Onboarding class. It first checks that the chosen AI model has the needed environment variable set, if that model requires one. An environment variable is a value supplied by the operating system, often used for secrets such as API keys. It also checks whether installed extensions need a credential store before any database changes happen, so a failed setup does not leave behind a half-created workspace.

After those checks, it opens a workspace database transaction, which means the database changes are grouped together like one all-or-nothing operation. It creates a workspace, creates the first member as the owner, and creates a default agent with a simple helpful-assistant prompt.

Only after the core workspace exists does it run onboarding steps from extensions. Each extension gets its own scoped context, like giving each add-on its own labeled toolbox. If an extension step fails, the failure is logged and the rest of setup continues; one add-on should not ruin the whole workspace.

#### Function details

##### `run_onboarding_steps`  (lines 47–75)

```
async def run_onboarding_steps(manifests: tuple[Manifest, ...], workspace_id: UUID, credentials: CredentialStore | None) -> None
```

**Purpose**: Runs setup steps contributed by installed extensions after the core workspace has been created. It keeps extension failures contained so the main workspace setup is not undone by one broken add-on.

**Data flow**: It receives the installed extension manifests, the new workspace ID, and an optional credential store. It enters the workspace context, checks each manifest for onboarding steps, builds an extension-specific context when possible, and calls each step. It returns nothing, but it may log skipped or failed extension setup work.

**Call relations**: Onboarding.run_steps calls this after the workspace and owner already exist. Inside, it uses ws to mark which workspace is active, context_for to build the extension’s scoped working context, and log to record skipped steps or failures without stopping the whole flow.

*Call graph*: called by 1 (run_steps); 3 external calls (context_for, log, ws).


##### `Onboarding.run`  (lines 89–92)

```
async def run(self) -> Onboarded
```

**Purpose**: Runs the full first-run onboarding sequence from start to finish. This is the high-level method someone uses when they want to initialize a new workspace completely.

**Data flow**: It starts with the Onboarding object’s stored configuration, email, model name, credentials, and extension manifests. It first creates the core workspace records, then runs extension onboarding steps for that new workspace. It returns an Onboarded value containing the new workspace ID and member ID.

**Call relations**: This is the top-level method for this file’s flow. It calls Onboarding.create to build the durable core setup, then calls Onboarding.run_steps so extensions can add their own first-run behavior.

*Call graph*: calls 2 internal fn (create, run_steps).


##### `Onboarding.create`  (lines 94–100)

```
async def create(self) -> Onboarded
```

**Purpose**: Creates the essential built-in setup: required model checks, credential checks, workspace, owner, and default agent. It deliberately does this before extension setup so the core system can exist even if an extension later fails.

**Data flow**: It reads the Onboarding object’s model choice, config, credentials, manifests, and owner email. It checks for required environment secrets, checks whether extension steps require credentials, and then writes the core workspace records to the database. It returns an Onboarded object with the created workspace and member identifiers.

**Call relations**: Onboarding.run calls this as the first phase. It delegates the safety checks to Onboarding._require_model_key and Onboarding._require_credentials_for_steps, then delegates the database creation to Onboarding._create_workspace.

*Call graph*: calls 3 internal fn (_create_workspace, _require_credentials_for_steps, _require_model_key); called by 1 (run).


##### `Onboarding.run_steps`  (lines 102–103)

```
async def run_steps(self, onboarded: Onboarded) -> None
```

**Purpose**: Runs the extension-specific part of onboarding for an already-created workspace. It is a small bridge between the Onboarding object and the shared helper that actually loops through extension steps.

**Data flow**: It receives an Onboarded result, reads the workspace ID from it, and combines that with the Onboarding object’s manifests and credential store. It passes those values into run_onboarding_steps. It returns nothing and does not directly change data itself.

**Call relations**: Onboarding.run calls this after Onboarding.create succeeds. This method then hands off to run_onboarding_steps, which does the detailed extension-by-extension work.

*Call graph*: calls 1 internal fn (run_onboarding_steps); called by 1 (run).


##### `Onboarding._require_credentials_for_steps`  (lines 105–116)

```
def _require_credentials_for_steps(self) -> None
```

**Purpose**: Stops setup early if installed extensions have onboarding steps but no credential store is available. This avoids creating a workspace that immediately cannot finish required extension setup.

**Data flow**: It reads the Onboarding object’s credential store, extension manifests, and configured credential-key environment variable name. If credentials are present, it does nothing. If credentials are missing and any extension has onboarding steps, it raises an error explaining which environment setting is needed.

**Call relations**: Onboarding.create calls this before any database records are created. It acts as a gatekeeper so Onboarding._create_workspace is only reached when the setup has enough credential support for extension steps.

*Call graph*: called by 1 (create).


##### `Onboarding._require_model_key`  (lines 118–125)

```
def _require_model_key(self) -> None
```

**Purpose**: Checks whether the selected AI model needs an API key or similar secret before the first conversation can work. If the model needs a named environment variable and it is missing, setup stops with a clear error.

**Data flow**: It asks Onboarding._model_key_env for the environment variable name required by the chosen model. If there is no required name, it allows setup to continue. If there is a required name but the operating system environment does not contain a value for it, it raises an error.

**Call relations**: Onboarding.create calls this near the start of setup. It relies on Onboarding._model_key_env to discover what to check before the flow proceeds to credential checks and database creation.

*Call graph*: calls 1 internal fn (_model_key_env); called by 1 (create).


##### `Onboarding._model_key_env`  (lines 127–130)

```
def _model_key_env(self) -> str | None
```

**Purpose**: Finds the environment variable name that should hold the key for the selected model, if the core system knows one. Some extension-provided models may resolve their own keys later, so this can return no name.

**Data flow**: It reads the Onboarding object’s config, installed manifests, and selected model name. It builds or consults the model registry, which is the lookup table for available model providers, and asks it which environment variable belongs to this model. It returns that variable name or None.

**Call relations**: Onboarding._require_model_key calls this when deciding whether setup must check for a model secret. This function hands the model lookup work to model_registry rather than hard-coding provider details here.

*Call graph*: called by 1 (_require_model_key); 1 external calls (model_registry).


##### `Onboarding._create_workspace`  (lines 132–155)

```
async def _create_workspace(self) -> Onboarded
```

**Purpose**: Writes the core first-run records into the database: the workspace, the first member, and the default agent. It also protects against running initialization twice by refusing to continue if a member already exists.

**Data flow**: It opens a workspace database transaction, looks for any existing member email, and raises AlreadyInitialized if one is found. If the database is still empty, it creates new unique IDs, inserts a workspace row, creates the owner member using the supplied email, inserts the default agent row, and returns an Onboarded object with the new workspace and member IDs.

**Call relations**: Onboarding.create calls this after all early checks pass. It uses workspace_tx for the database transaction, SQLAlchemy insert and select helpers to read and write rows, create_member to add the owner, uuid4 to make new identifiers, and Onboarded to package the result for later steps.

*Call graph*: called by 1 (create); 7 external calls (__init__, __init__, insert, select, workspace_tx, create_member, uuid4).


### Slack Connect onboarding jobs
This file coordinates retryable Slack Connect channel and invitation provisioning for newly created customer workspaces.

### `control/src/ufo_control/gateway_slack_connect.py`

`orchestration` · `startup and background polling`

When a new customer finishes signup, the main signup path should not have to wait for Slack. This file turns completed signup records in the database into Slack Connect delivery records, then slowly works through them in the background. Think of it like a mailroom: signup drops a durable note in a ledger, and this worker later creates the right envelope, sends it, and marks the ledger done.

The workflow is careful because Slack calls can fail in awkward ways. A network timeout might happen after Slack actually created a channel. An invite call might succeed but the response might be lost. To avoid duplicate channels or duplicate invitations, the file uses deterministic channel names, stores progress after each step, and checks Slack’s existing state before retrying sensitive work.

It also supports multiple gateway processes running at once. Each process leases one delivery row before touching it, renews that lease during slow Slack calls, and only writes results if it still owns the lease. Temporary Slack problems are scheduled for retry with backoff. Problems that retries cannot fix, such as a bad token, wrong Slack team, impossible recipient, or unclear Slack state, mark the row as failed for an operator to review. A helper can later re-arm a failed row after the cause has been fixed.

#### Function details

##### `SlackTransientError.__init__`  (lines 167–169)

```
def __init__(self, message: str, retry_after: float | None=None) -> None
```

**Purpose**: This creates an error object for Slack problems that may clear up later, such as a timeout, rate limit, or server outage. It can also carry Slack’s requested wait time before trying again.

**Data flow**: It receives a human-readable message and, optionally, a retry-after delay. It stores the message as the exception text and saves the delay on the error object so the retry scheduler can use it later.

**Call relations**: SlackConnectClient._call raises this when an HTTP or Slack response looks temporary. Later, SlackConnectInviter._advance catches this kind of error and sends the delivery row to _reschedule instead of marking it permanently failed.

*Call graph*: called by 1 (_call).


##### `rearm_failed_delivery`  (lines 188–203)

```
async def rearm_failed_delivery(pool: asyncpg.Pool, onboard_claim_id: UUID) -> datetime | None
```

**Purpose**: This is the operator recovery helper for one failed Slack Connect delivery. After someone fixes the root cause, such as permissions or configuration, this function puts that one row back into the retry queue.

**Data flow**: It receives a database pool and the signup claim ID. It looks for a matching row whose state is failed, resets its worker, retry time, attempt count, and error text, and returns the time when the row had last changed. If the row is not failed, it changes nothing and returns nothing.

**Call relations**: This function talks only to the database through asyncpg.Pool.fetchval. It is intentionally separate from Slack calls, so an operator command can safely re-arm work without accidentally sending an invitation immediately.

*Call graph*: 1 external calls (fetchval).


##### `SlackConnectClient.team_id`  (lines 215–216)

```
async def team_id(self) -> str
```

**Purpose**: This asks Slack which workspace the configured bot token belongs to. The inviter uses it to prove it is about to work in UFO’s operator Slack workspace, not some other workspace.

**Data flow**: It sends an auth.test request through _call, then extracts the team_id field from Slack’s response with _text. The result is the Slack team ID as a string.

**Call relations**: SlackConnectInviter._verify_team calls this before any channel is created or invited. It relies on _call for the HTTP request and _text for safe response reading.

*Call graph*: calls 2 internal fn (_call, _text).


##### `SlackConnectClient.create_channel`  (lines 218–220)

```
async def create_channel(self, name: str) -> str
```

**Purpose**: This creates a public Slack channel with the deterministic customer channel name. It returns the new channel’s Slack ID, which is needed for later invitation calls.

**Data flow**: It receives a channel name, sends it to Slack’s conversations.create method, and reads channel.id from the response. On success it outputs that channel ID; on Slack errors it raises the appropriate error.

**Call relations**: SlackConnectInviter._open_channel uses this when a delivery row does not yet have a channel ID. The function delegates the raw Slack request to _call and response extraction to _text.

*Call graph*: calls 2 internal fn (_call, _text).


##### `SlackConnectClient.channel_id_by_name`  (lines 222–243)

```
async def channel_id_by_name(self, name: str) -> str
```

**Purpose**: This finds an existing Slack channel by its exact name, including archived channels. It is used to recover when Slack says the deterministic channel name is already taken, which may mean a previous create call succeeded but its response was lost.

**Data flow**: It receives a channel name, pages through Slack’s public channel list, and compares each channel’s name to the requested one. If it finds a match, it returns that channel’s ID; if it reaches the search limit or finds no match, it raises a terminal error because the state is inconsistent.

**Call relations**: SlackConnectInviter._open_channel calls this after create_channel raises SlackNameTakenError. It uses _call for each conversations.list page and _text to safely read the matching channel ID.

*Call graph*: calls 2 internal fn (_call, _text); 1 external calls (__init__).


##### `SlackConnectClient.outgoing_invite_id`  (lines 245–276)

```
async def outgoing_invite_id(self, channel_id: str) -> str | None
```

**Purpose**: This checks whether Slack already has a live Slack Connect invitation for a channel. It prevents the system from blindly sending a second invite after a previous invite attempt may have succeeded.

**Data flow**: It receives a channel ID, pages through Slack’s outgoing Connect invitations, and looks for entries tied to that channel. If it sees a live invite status, it returns the invite ID. If it sees only known dead statuses, it returns nothing. If Slack shows an unknown status or the list is too long to inspect safely, it raises a terminal error for human review.

**Call relations**: SlackConnectInviter._invite uses this during reconciliation, but only when the database shows an earlier invite was already attempted. It calls _call to read Slack’s invitation list and _text to pull out the invite ID.

*Call graph*: calls 2 internal fn (_call, _text); 1 external calls (__init__).


##### `SlackConnectClient.is_externally_shared`  (lines 278–281)

```
async def is_externally_shared(self, channel_id: str) -> bool
```

**Purpose**: This checks whether a Slack channel is already shared or pending share with another workspace. That can prove an invite landed even if Slack no longer lists the original invitation in a useful way.

**Data flow**: It receives a Slack channel ID, asks Slack for channel details, and reads the sharing flags from the response. It returns true if Slack says the channel is externally shared or pending external sharing, otherwise false.

**Call relations**: SlackConnectInviter._invite calls this after it cannot find a live outgoing invite during reconciliation. The raw Slack request goes through _call.

*Call graph*: calls 1 internal fn (_call).


##### `SlackConnectClient.invite_shared`  (lines 283–290)

```
async def invite_shared(self, channel_id: str, email: str) -> str
```

**Purpose**: This sends the actual Slack Connect invitation email for a channel. It also blocks impossible recipient addresses that are longer than the normal email length limit.

**Data flow**: It receives a channel ID and email address. If the email is too long, it raises a terminal error. Otherwise it calls Slack’s conversations.inviteShared method and returns the invitation ID from the response.

**Call relations**: SlackConnectInviter._invite calls this only after recording that an invite attempt is beginning. It uses _call for the Slack request and _text to read invite_id.

*Call graph*: calls 2 internal fn (_call, _text); 1 external calls (__init__).


##### `SlackConnectClient.redact`  (lines 292–293)

```
def redact(self, message: str) -> str
```

**Purpose**: This removes the bot token from error text before that text is stored or logged. It also trims the message so an unexpectedly large error does not flood logs or the database.

**Data flow**: It receives a message string, replaces any occurrence of the secret token with a safe placeholder, and cuts the result to a fixed maximum length. The returned string is safe to put in logs or last_error fields.

**Call relations**: SlackConnectInviter._reschedule and _fail use this before saving or logging errors. It is a small safety step around all failure reporting from Slack delivery.


##### `SlackConnectClient._call`  (lines 295–323)

```
async def _call(self, method: str, params: dict[str, str | int]) -> dict[str, Any]
```

**Purpose**: This is the low-level Slack Web API requester. It turns HTTP responses, Slack error codes, timeouts, and rate limits into the project’s clearer temporary or permanent error types.

**Data flow**: It receives a Slack method name and form parameters, sends a POST request with the bot token, and reads the JSON response. Successful Slack responses come out as dictionaries. Network failures, rate limits, server errors, Slack name conflicts, known temporary Slack errors, and permanent Slack errors become specific exceptions.

**Call relations**: All public SlackConnectClient methods use this instead of making HTTP calls directly. It creates SlackTransientError for retryable trouble, uses _retry_after for rate-limit timing, and raises SlackNameTakenError or SlackTerminalError when retrying would not be safe or useful.

*Call graph*: calls 2 internal fn (__init__, _retry_after); called by 6 (channel_id_by_name, create_channel, invite_shared, is_externally_shared, outgoing_invite_id, team_id); 3 external calls (__init__, __init__, AsyncClient).


##### `SlackConnectClient._text`  (lines 325–331)

```
def _text(self, payload: dict[str, Any], *path: str) -> str
```

**Purpose**: This safely pulls a required text value out of a nested Slack response. It turns missing or oddly shaped Slack data into a clear permanent error instead of letting the rest of the code guess.

**Data flow**: It receives a response dictionary and a path of keys, walks through the dictionary one key at a time, and returns the final value as text. If any key is missing or the response shape is not a dictionary where expected, it raises a terminal error.

**Call relations**: SlackConnectClient.team_id, create_channel, channel_id_by_name, outgoing_invite_id, and invite_shared all use this after _call returns. It is the shared response-checking helper for required Slack fields.

*Call graph*: called by 5 (channel_id_by_name, create_channel, invite_shared, outgoing_invite_id, team_id); 1 external calls (__init__).


##### `_retry_after`  (lines 334–338)

```
def _retry_after(response: httpx.Response) -> float | None
```

**Purpose**: This reads Slack’s rate-limit wait time from an HTTP response. It caps the value so Slack cannot accidentally make the worker sleep for an unreasonably long time.

**Data flow**: It receives an HTTP response, looks for a numeric retry-after header, and returns that number as seconds up to a configured maximum. If the header is missing or not a clean number, it returns nothing.

**Call relations**: SlackConnectClient._call uses this only when Slack returns HTTP 429, meaning too many requests. The returned delay is stored inside SlackTransientError so _reschedule can honor it.

*Call graph*: called by 1 (_call).


##### `SlackConnectInviter.run`  (lines 364–388)

```
async def run(self) -> None
```

**Purpose**: This is the endless background loop for Slack Connect delivery. It keeps sweeping for due work and deliberately stays alive even when one sweep fails.

**Data flow**: It starts with a failure counter, repeatedly calls poll, resets the counter on success, and logs exceptions without exiting. If poll did not claim a row, it sleeps for the configured interval before checking again. Cancellation is the only normal way out.

**Call relations**: The gateway lifespan is expected to run this task. It calls poll for the real work and asyncio.sleep to avoid busy-waiting when there is nothing due.

*Call graph*: calls 1 internal fn (poll); 1 external calls (sleep).


##### `SlackConnectInviter.poll`  (lines 390–405)

```
async def poll(self) -> bool
```

**Purpose**: This performs one unit of background work: discover any new eligible signups, claim one due delivery row, and move that row forward. It returns whether it claimed work so the outer loop can drain a busy queue quickly.

**Data flow**: It first materializes database rows for completed signups, then tries to claim one due row. If none is available, it returns false. If one is claimed, it starts a lease-renewal task, advances the delivery through Slack steps, cancels lease renewal afterward, and returns true.

**Call relations**: SlackConnectInviter.run calls this on every sweep. Inside, it coordinates _materialize, _claim, _renew_lease, and _advance, using asyncio.create_task and asyncio.gather to keep the lease alive during Slack calls.

*Call graph*: calls 4 internal fn (_advance, _claim, _materialize, _renew_lease); called by 1 (run); 2 external calls (create_task, gather).


##### `SlackConnectInviter._materialize`  (lines 407–421)

```
async def _materialize(self) -> None
```

**Purpose**: This turns completed signup claims into Slack Connect delivery rows in the database. It is the bridge from signup history to background Slack work.

**Data flow**: It opens a database transaction, takes a PostgreSQL advisory lock, and runs an insert-from-select statement. The statement chooses the earliest completed claim for each new workspace and inserts a pending delivery row with the deterministic channel name, skipping conflicts.

**Call relations**: SlackConnectInviter.poll calls this before trying to claim work. The advisory lock and conflict skipping make it safe when more than one gateway replica is polling at the same time.

*Call graph*: called by 1 (poll).


##### `SlackConnectInviter._claim`  (lines 423–435)

```
async def _claim(self) -> _Delivery | None
```

**Purpose**: This reserves one pending or expired delivery row for the current worker. Reserving the row stops another gateway replica from doing the same Slack work at the same time.

**Data flow**: It asks the database for the oldest due row, marks it claimed by this worker, extends its lease expiry, increments its attempt count, and joins in the signup email. If a row is found, it packages the fields into a _Delivery object; if not, it returns nothing.

**Call relations**: SlackConnectInviter.poll calls this after materialization. The returned _Delivery is the work ticket that _advance uses for channel creation and invitation.

*Call graph*: called by 1 (poll); 1 external calls (__init__).


##### `SlackConnectInviter._renew_lease`  (lines 437–459)

```
async def _renew_lease(self, onboard_claim_id: UUID) -> None
```

**Purpose**: This keeps the worker’s claim alive while slow Slack calls are in flight. Without it, another replica might think the claim expired and send a duplicate invitation.

**Data flow**: It receives the signup claim ID, sleeps for a fixed renewal interval, then updates that row’s lease expiry if it is still owned by this worker. It repeats forever until cancelled, logging database renewal failures but continuing to try.

**Call relations**: SlackConnectInviter.poll starts this as a background task right after claiming a row and cancels it when _advance finishes. The delivery writes themselves still check ownership through _write.

*Call graph*: called by 1 (poll); 1 external calls (sleep).


##### `SlackConnectInviter._advance`  (lines 461–488)

```
async def _advance(self, delivery: _Delivery) -> None
```

**Purpose**: This is the main state machine for one delivery row. It verifies the Slack token, opens or recovers the channel, sends or reconciles the invitation, and finally marks the row delivered.

**Data flow**: It receives a _Delivery record. It checks the configured Slack team, obtains a channel ID if one is not already stored, obtains an invitation ID or proof of sharing if one is not already stored, and writes the delivered state. Temporary errors become scheduled retries, permanent errors become failed rows, and unexpected errors are logged then failed.

**Call relations**: SlackConnectInviter.poll calls this after claiming work. It delegates each step to _verify_team, _open_channel, _invite, _reschedule, _fail, and _write, and lets _LeaseLost bubble up so the caller can abandon work owned by another replica.

*Call graph*: calls 6 internal fn (_fail, _invite, _open_channel, _reschedule, _verify_team, _write); called by 1 (poll).


##### `SlackConnectInviter._verify_team`  (lines 490–498)

```
async def _verify_team(self) -> None
```

**Purpose**: This confirms the Slack bot token belongs to the expected operator workspace before making any channel changes. It protects against a misconfigured or rotated token sending customer invites from the wrong Slack workspace.

**Data flow**: It asks Slack for the token’s team ID and compares it with the configured expected team ID. If they match, nothing is returned and delivery continues. If they differ, it raises a configuration error.

**Call relations**: SlackConnectInviter._advance calls this first for every delivery. It uses SlackConnectClient.team_id, and a mismatch is later caught by _advance and written as a failed row.

*Call graph*: called by 1 (_advance); 1 external calls (__init__).


##### `SlackConnectInviter._open_channel`  (lines 500–506)

```
async def _open_channel(self, delivery: _Delivery) -> str
```

**Purpose**: This creates the customer’s operator-side Slack channel, or recovers the existing channel if Slack says the name is already taken. It then saves the channel ID so later retries do not need to create it again.

**Data flow**: It receives a _Delivery with a deterministic channel name. It tries to create the channel; if the name is taken, it looks up the channel by that exact name. Once it has a channel ID, it writes that ID into the delivery row and returns it.

**Call relations**: SlackConnectInviter._advance calls this when the claimed row has no stored channel ID. It calls Slack through the SlackConnectClient methods and records progress using _write.

*Call graph*: calls 1 internal fn (_write); called by 1 (_advance).


##### `SlackConnectInviter._invite`  (lines 508–525)

```
async def _invite(self, delivery: _Delivery, channel_id: str) -> str | None
```

**Purpose**: This sends the Slack Connect invitation, but first reconciles any previous attempt that may already have reached Slack. That is the key safeguard against duplicate invitations.

**Data flow**: It receives a delivery record and channel ID. If the row shows an earlier invite attempt, it asks Slack whether a live invite exists, then whether the channel is already externally shared. If either proves success, it records or accepts that result. Otherwise it writes invite_attempted_at before calling Slack to send a new invitation, then stores the returned invitation ID.

**Call relations**: SlackConnectInviter._advance calls this after a channel ID is known. It uses _write to mark the risky point before the Slack invite call, and _persist_invitation to save an invite ID when one is known.

*Call graph*: calls 2 internal fn (_persist_invitation, _write); called by 1 (_advance).


##### `SlackConnectInviter._persist_invitation`  (lines 527–529)

```
async def _persist_invitation(self, delivery: _Delivery, invitation_id: str) -> str
```

**Purpose**: This saves Slack’s invitation ID on the delivery row. It is a small helper that makes successful invitation reconciliation and fresh invitation sending use the same database update.

**Data flow**: It receives a delivery record and invitation ID, writes that ID to the row, and returns the same ID to the caller. If the worker no longer owns the row, the write fails through _write.

**Call relations**: SlackConnectInviter._invite calls this both when it finds an existing live invite and when it receives a new invite ID from Slack. It delegates the guarded database update to _write.

*Call graph*: calls 1 internal fn (_write); called by 1 (_invite).


##### `SlackConnectInviter._reschedule`  (lines 531–550)

```
async def _reschedule(self, delivery: _Delivery, error: SlackTransientError) -> None
```

**Purpose**: This handles temporary Slack failures by putting the row back into pending state for a later try. It uses increasing delays so repeated problems do not hammer Slack.

**Data flow**: It receives a delivery record and a temporary error. If the attempt count has reached the maximum, it marks the row failed instead. Otherwise it chooses a delay from Slack’s retry-after value or exponential backoff, clears the worker lease, stores the next attempt time and redacted error, and logs the retry.

**Call relations**: SlackConnectInviter._advance calls this when it catches SlackTransientError. It may hand off to _fail after too many attempts, or use _write to schedule the next attempt.

*Call graph*: calls 2 internal fn (_fail, _write); called by 1 (_advance); 1 external calls (timedelta).


##### `SlackConnectInviter._fail`  (lines 552–563)

```
async def _fail(self, delivery: _Delivery, error: Exception) -> None
```

**Purpose**: This marks a delivery row as needing operator review. It is used when retrying is not safe, not useful, or the system hit an unexpected exception.

**Data flow**: It receives a delivery record and an error, redacts any secret token from the error text, clears the worker lease and next attempt time, stores the failed state and last_error, and logs the failure.

**Call relations**: SlackConnectInviter._advance calls this for terminal and unexpected errors. _reschedule also calls it when a temporary problem has been retried too many times. The actual guarded database update goes through _write.

*Call graph*: calls 1 internal fn (_write); called by 2 (_advance, _reschedule).


##### `SlackConnectInviter._write`  (lines 565–574)

```
async def _write(self, onboard_claim_id: UUID, assignment: str, *values: object) -> None
```

**Purpose**: This updates a delivery row only if the current worker still owns its lease. It is the safety latch that stops an old worker from overwriting another replica’s work.

**Data flow**: It receives a claim ID, a SQL assignment fragment, and optional values. It runs an update where both the claim ID and worker ID match, then returns if the row was updated. If no row was updated, it raises _LeaseLost.

**Call relations**: Nearly every delivery step uses this to store progress: _advance, _open_channel, _invite, _persist_invitation, _reschedule, and _fail. When it raises _LeaseLost, poll logs that this worker lost ownership and leaves the other worker’s row untouched.

*Call graph*: called by 6 (_advance, _fail, _invite, _open_channel, _persist_invitation, _reschedule); 1 external calls (__init__).


##### `slack_connect_from_env`  (lines 577–592)

```
def slack_connect_from_env(pool: asyncpg.Pool) -> SlackConnectInviter | None
```

**Purpose**: This builds the Slack Connect background inviter from environment variables, or disables it cleanly. It makes startup fail loudly if Slack Connect is enabled but required settings are missing or malformed.

**Data flow**: It receives a database pool and reads the enable switch from the process environment. If disabled, it returns nothing. If enabled, it requires the bot token and expected Slack team ID, creates a SlackConnectClient and SlackConnectInviter, and gives the worker a hostname-and-process-ID name. If the enable switch is not a boolean-like value, it raises an error.

**Call relations**: Gateway startup code can call this to decide whether to start SlackConnectInviter.run. It uses _require_env for mandatory settings, plus socket.gethostname and os.getpid to create a distinct worker ID.

*Call graph*: calls 1 internal fn (_require_env); 4 external calls (__init__, __init__, getpid, gethostname).


##### `_require_env`  (lines 595–599)

```
def _require_env(name: str) -> str
```

**Purpose**: This reads a required environment variable and gives a clear error if it is missing. It is used only when Slack Connect delivery has been explicitly enabled.

**Data flow**: It receives an environment variable name, looks it up, and returns its value if present and non-empty. If the value is absent or empty, it raises a runtime error explaining that the setting is required.

**Call relations**: slack_connect_from_env calls this for the Slack bot token and expected team ID. This keeps half-configured deployments from reaching Slack at all.

*Call graph*: called by 1 (slack_connect_from_env).

## 📊 State Registers Touched

- `reg-effective-config` — The running service’s merged settings, such as required keys, enabled backends, safety options, and service behavior.
- `reg-workspace-tenant-record` — The customer workspace record that all users, conversations, data, tools, and billing are kept under.
- `reg-member-session-auth` — The signed-in person’s identity and session proof used to decide who is making a request.
- `reg-surface-installation-binding` — The stored connection between outside channels like Slack, web chat, or terminal clients and an internal workspace conversation.
- `reg-seat-entitlement` — The workspace membership and seat-limit state that decides which people the agent may serve.
- `reg-row-level-security-context` — The database safety context that keeps each workspace’s rows separated even when code uses shared tables.
- `reg-onboarding-verification-invites` — Short-lived hashed email verification proofs and one-time invite codes used to admit new users and create or join workspaces.
- `reg-client-update-check-cache` — Hosted gateway state or cache for known client/install versions and whether a terminal user should be offered an updated curl-based install.
- `reg-slack-connect-provisioning-state` — The hosted-control-plane state for creating, retrying, and inspecting customer Slack Connect channels during workspace onboarding.
- `reg-workspace-domain-claim-map` — The hosted onboarding mapping from verified company email domains to existing or newly-created workspaces, reused for domain-based workspace lookup and operator authorization.
- `reg-crypto-signing-encryption-keyring` — Stable secret key material used to sign/verify session, OAuth/state, artifact, and filesystem tokens and to encrypt/decrypt stored credentials.
