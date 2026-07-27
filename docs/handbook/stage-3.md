# Hosted gateway startup and workspace onboarding  `stage-3`

This stage is the front door for hosted UFO deployments. It runs during startup and before normal workspace use, bringing up a public gateway that helps a new user prove who they are and land in the right workspace. The gateway server is the main entry point. It guides users through installing the client, entering a work email, passing an invite gate if required, and receiving a signed-in token.

Several helper pieces act like the desks in a reception area. The claim code creates short-lived email codes, stores only safe hashed copies, and checks attempts and expiry. The email code decides whether an address is a real work email and sends codes through Amazon SES or local logs. The store keeps onboarding records in Postgres so the process survives restarts. Invite code logic enforces one-time workspace creation codes. Shared-domain logic finds or safely creates the workspace for a company domain.

The terminal and web helpers present the same flow to different users: byte directives for the UFO client, or browser pages and JSON. Finally, the onboarding module creates the first workspace, owner, default assistant, keys, and extension setup.

## Files in this stage

### Gateway entrypoint
The hosted control gateway package and public server entrypoint coordinate onboarding requests and route them into the rest of the flow.

### `control/src/ufo_control/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can write imports that start with `ufo_control`, and Python knows this directory is the home for that package. Think of it like a label on a drawer: the drawer may contain many useful tools, but this label simply tells you what collection they belong to. Because the file is empty, it does not run setup code, define shared names, or change how the package works. Its value is structural: without it, some Python environments or tools may not recognize `control/src/ufo_control` as a normal package, which could make imports fail or behave inconsistently.


### `control/src/ufo_control/gateway.py`

`entrypoint` · `startup, request handling, shutdown`

This is the front door for hosted UFO onboarding. Without it, a new person could not download the install script, prove who they are, join their company workspace, or get the token that lets the client talk to the workspace service.

The file creates a FastAPI app, which is a web server framework for defining HTTP routes. At startup it checks required settings, verifies the control database schema exists, opens a small database connection pool, builds the onboarding services, and optionally starts a Slack Connect invite delivery task. On shutdown it cancels background work and closes database resources cleanly.

The main onboarding flow is like a receptionist desk. First, the server asks for a work email. Then it sends and checks a verification code. Once the email is verified, it decides whether the person can use an existing workspace for their email domain, or whether a new workspace needs an invite code. Finally it creates or finds the workspace, records that onboarding is complete, mints a signed token, and sends the client simple “directives” such as say this, ask that, save this token, or use this workspace URL.

The file supports both terminal onboarding and web onboarding. Terminal responses are plain text directives. Web responses are parsed into JSON so the browser can render them. It also exposes small operational routes such as health checks, the install script, a login page, and a workspace count.

#### Function details

##### `_stamp_script`  (lines 66–70)

```
def _stamp_script(text: str) -> str
```

**Purpose**: Adds deployment-specific information to the client install script before it is served. It gives the script a short version based on its contents and points it at the configured public UFO URL.

**Data flow**: It takes the raw script text in → calculates a short SHA-1 hash, which is a fingerprint of the text → replaces the development version marker and default URL inside the script → returns the modified script text ready to serve.

**Call relations**: This runs when the module is loaded to create the shared STAMPED_SCRIPT value. Later, the /ufo route serves that already-prepared script instead of recalculating it for every request.

*Call graph*: 1 external calls (sha1).


##### `Onboarding.advance`  (lines 86–92)

```
async def advance(self, channel: str, session: str, body: str, install: bytes) -> bytes
```

**Purpose**: Moves one onboarding session to its next step. It decides whether the user should be asked for an email, asked for a verification code, or signed into a workspace.

**Data flow**: It receives a channel, session ID, user message body, and optional install instructions → looks up any live onboarding claim for that channel and session → routes the message to the correct next step → returns bytes containing directives for the client to display or act on.

**Call relations**: The web and terminal onboarding routes call this after reading a request. It is the traffic director for the onboarding conversation, handing off to _collect_email, _verify_code, or _resolve depending on what is already known.

*Call graph*: calls 3 internal fn (_collect_email, _resolve, _verify_code).


##### `Onboarding._collect_email`  (lines 94–112)

```
async def _collect_email(self, channel: str, session: str, body: str, install: bytes) -> bytes
```

**Purpose**: Starts onboarding by asking for and validating the user’s work email. If the email is acceptable, it begins a claim and tells the user to check their email for a code.

**Data flow**: It receives the current channel, session, typed body, and install directives → if the body is empty, it returns prompts asking for a work email → otherwise it tries to start an email claim → on failure it returns the error and asks again; on success it returns a message saying a code was emailed and asks for that code.

**Call relations**: Onboarding.advance calls this when there is no live claim yet. It uses the directive renderer to turn the next instructions into the simple command format consumed by the terminal or web layer.

*Call graph*: called by 1 (advance); 2 external calls (directive, render).


##### `Onboarding._verify_code`  (lines 114–121)

```
async def _verify_code(self, claim: OnboardClaim, body: str, install: bytes) -> bytes
```

**Purpose**: Checks the verification code that the user typed. If the code is right, it continues toward workspace resolution; if not, it asks for the code again.

**Data flow**: It receives the stored claim, the typed code, and install directives → asks the claim workflow to verify the code → if verification fails, returns an error plus another code prompt → if verification succeeds, continues into workspace resolution and returns that result.

**Call relations**: Onboarding.advance calls this when a claim exists but has not been verified yet. On success it immediately hands off to _resolve, so the user can be signed in or asked for an invite without making another round trip.

*Call graph*: calls 1 internal fn (_resolve); called by 1 (advance); 2 external calls (directive, render).


##### `Onboarding._resolve`  (lines 123–138)

```
async def _resolve(self, claim: OnboardClaim, answer: str | None, install: bytes) -> bytes
```

**Purpose**: Turns a verified email claim into an actual workspace sign-in. It enforces invite rules for new workspaces, creates or finds the workspace, marks onboarding complete, and produces the signed-in response.

**Data flow**: It receives a verified claim, the user’s latest answer, and install directives → if new workspaces require invites and this email domain has no workspace, it runs the invite gate → once allowed, it ensures a workspace exists for the email domain → records the completed claim with the workspace ID → returns the final signed-in directives.

**Call relations**: This is reached from advance for already verified claims and from _verify_code after a successful code check. It may call _invite_gate before calling _signed_in, so invite enforcement happens before any token is issued.

*Call graph*: calls 2 internal fn (_invite_gate, _signed_in); called by 2 (_verify_code, advance); 1 external calls (directive).


##### `Onboarding._invite_gate`  (lines 140–181)

```
async def _invite_gate(self, claim: OnboardClaim, answer: str | None, install: bytes) -> bytes | InviteAccepted | None
```

**Purpose**: Checks whether a verified user may create a brand-new workspace when invites are required. It asks for an invite code and explains clearly when a code is expired, already used, unknown, or accepted.

**Data flow**: It receives the claim, the latest answer, and install directives → if an invite is already attached to the claim, it lets the flow continue → if no answer was given, it asks for an invite code → otherwise it redeems the code → returns either an accepted invite object, a prompt explaining the problem, or permission to continue.

**Call relations**: _resolve calls this only when invite gating matters: invite-required mode is on and the user’s email domain does not already have a workspace. Its answer decides whether _resolve can continue to workspace creation or must return another prompt.

*Call graph*: called by 1 (_resolve); 2 external calls (directive, render).


##### `Onboarding._signed_in`  (lines 183–211)

```
def _signed_in(self, claim: OnboardClaim, ensured: EnsuredWorkspace, install: bytes, accepted: bytes) -> bytes
```

**Purpose**: Builds the final successful onboarding response. It gives the client a signed token, workspace URL, and next prompt or menu.

**Data flow**: It receives the verified claim, the ensured workspace, install directives, and any invite-accepted message → creates a signed token for the user and workspace → adds the workspace URL and, for operator-domain users only, a debugger URL → adds a signed-in message → returns rendered directives as bytes.

**Call relations**: _resolve calls this after the workspace has been ensured and the claim has been completed. It calls mint_token to create the credential that later requests will use, then packages everything with directive and render.

*Call graph*: called by 1 (_resolve); 3 external calls (directive, render, mint_token).


##### `GatewayState.healthy`  (lines 221–229)

```
async def healthy(self) -> bool
```

**Purpose**: Checks whether the gateway is connected to the database using the expected roles. This helps the health endpoint report whether the service is actually usable, not just running.

**Data flow**: It reads from the async database pool and from the shared workspace database connection → asks each connection who the current database user is → compares those users with the roles recorded at startup → returns true if both match, otherwise logs the failure and returns false.

**Call relations**: The /healthz route calls this during health checks. It uses workspace_tx for the workspace-side database check and a direct pool query for the control-side check.

*Call graph*: 2 external calls (text, workspace_tx).


##### `_require_env`  (lines 232–236)

```
def _require_env(name: str) -> str
```

**Purpose**: Reads a required environment variable and fails fast if it is missing. This prevents the gateway from starting in a half-configured state.

**Data flow**: It receives the environment variable name → looks it up in the process environment → returns the value if present → raises a clear runtime error if it is empty or missing.

**Call relations**: The startup lifespan calls this for settings that must exist, such as the workspace base URL and token secret. That means bad deployment configuration is caught before the server starts accepting requests.

*Call graph*: called by 1 (lifespan).


##### `_invite_required`  (lines 239–248)

```
def _invite_required() -> bool
```

**Purpose**: Decides whether creating a new workspace requires an invite code. The safe default is yes, so forgetting the setting does not accidentally open public signup.

**Data flow**: It reads the invite-required environment variable → treats true/1 as enabled and false/0 as disabled → returns a boolean → raises an error for any other value so mistakes are visible.

**Call relations**: The startup lifespan calls this while building the Onboarding object. Its result later controls whether _resolve uses the invite gate for new workspaces.

*Call graph*: called by 1 (lifespan).


##### `_dsn_role`  (lines 251–255)

```
def _dsn_role(dsn: str) -> str
```

**Purpose**: Extracts the database username, also called the role, from a database connection string. The gateway uses this to remember which roles it expects to see during health checks.

**Data flow**: It receives a database DSN, meaning a database address string → parses it with SQLAlchemy’s URL parser → returns the username part → raises an error if no username is present.

**Call relations**: The startup lifespan calls this for both owner and serving database URLs. GatewayState.healthy later compares live database users against these saved role names.

*Call graph*: called by 1 (lifespan).


##### `_request_body`  (lines 258–264)

```
async def _request_body(request: Request) -> str
```

**Purpose**: Safely reads a request body as text while enforcing a small size limit. This protects onboarding endpoints from overly large input.

**Data flow**: It receives a FastAPI request → streams the body in chunks instead of assuming it is tiny → keeps only up to the allowed limit plus one byte → raises an input error if the body is too large → decodes the bytes as UTF-8 text, replaces invalid characters, trims whitespace, and returns the string.

**Call relations**: Both onboarding routes call this before passing the user’s answer into Onboarding.advance. If it raises _RequestInputError, the route turns that into a friendly client directive instead of crashing.

*Call graph*: called by 2 (onboard, onboard_web); 2 external calls (__init__, stream).


##### `gateway_app`  (lines 267–393)

```
def gateway_app() -> FastAPI
```

**Purpose**: Creates and returns the FastAPI application for the gateway server. It defines startup and shutdown behavior plus all HTTP routes served by this file.

**Data flow**: It starts with no active state → defines a lifespan function that will fill in database pools and onboarding services at startup → creates the FastAPI app with that lifespan → registers route functions for health, script serving, login, fleet count, and onboarding → returns the ready app object.

**Call relations**: The module calls this at the bottom to create the exported app. A web server process imports that app and then FastAPI calls the nested route functions when matching requests arrive.

*Call graph*: 1 external calls (FastAPI).


##### `gateway_app.lifespan`  (lines 271–323)

```
async def lifespan(app: FastAPI)
```

**Purpose**: Sets up everything the gateway needs before serving requests and cleans it up afterward. It is the service’s startup and shutdown checklist.

**Data flow**: On startup, it reads database URLs and environment settings → checks the control schema → opens a database pool → builds the store, invite system, claim workflow, workspace resolver, and GatewayState → initializes the workspace database engine → optionally starts a Slack Connect background task. On shutdown, it cancels that task if present, clears state, disposes database engines, and closes the pool.

**Call relations**: FastAPI runs this around the lifetime of the app. It calls helper functions such as _require_env, _invite_required, and _dsn_role, and it constructs the Onboarding object used later by the request routes.

*Call graph*: calls 3 internal fn (_dsn_role, _invite_required, _require_env); 18 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, create_task, gather, create_pool (+8 more)).


##### `gateway_app.healthz`  (lines 328–331)

```
async def healthz() -> Response
```

**Purpose**: Answers health-check requests. It tells load balancers or operators whether the gateway is ready and connected correctly.

**Data flow**: It reads the shared gateway state → if state is missing or the health check fails, it returns JSON saying unavailable with HTTP status 503 → otherwise it returns JSON saying ok.

**Call relations**: This route is called by HTTP GET /healthz. It relies on GatewayState.healthy to do the real database role checks before returning a response.

*Call graph*: 1 external calls (JSONResponse).


##### `gateway_app.serve_script`  (lines 334–335)

```
async def serve_script() -> Response
```

**Purpose**: Serves the UFO client install script. This is how a terminal user can fetch the shell script needed to start onboarding.

**Data flow**: It reads the already-stamped script text from memory → returns it as a plain text response with a shell-script media type.

**Call relations**: This route is called by HTTP GET /ufo. It depends on _stamp_script having prepared STAMPED_SCRIPT when the module loaded.

*Call graph*: 1 external calls (PlainTextResponse).


##### `gateway_app.fleet`  (lines 338–341)

```
async def fleet() -> Response
```

**Purpose**: Reports how many workspaces exist. It exposes a small operational count under the name “craft.”

**Data flow**: It reads the active gateway state → queries the control database for the number of rows in the workspace table → returns that count as JSON.

**Call relations**: This route is called by HTTP GET /fleet. It uses the async database pool created during startup.

*Call graph*: 1 external calls (JSONResponse).


##### `gateway_app.login`  (lines 344–345)

```
async def login() -> Response
```

**Purpose**: Serves the browser login page. It gives web users the HTML page that starts or continues web onboarding.

**Data flow**: It reads the LOGIN_PAGE HTML constant → wraps it in an HTML response → sends it to the browser.

**Call relations**: This route is called by HTTP GET /login. The actual onboarding actions from that page go to the web onboarding route.

*Call graph*: 1 external calls (HTMLResponse).


##### `gateway_app.onboard_web`  (lines 348–363)

```
async def onboard_web(request: Request) -> Response
```

**Purpose**: Runs one step of onboarding for the browser version of the client. It returns structured JSON directives so the web page can display the next message or action.

**Data flow**: It reads the x-ufo-session header and rejects the request if it is missing → checks the session length → reads the body with _request_body → calls Onboarding.advance using the web channel and no install script → parses the returned directive bytes into JSON-friendly objects → returns them. If input is invalid or something unexpected fails, it returns directives explaining the failure.

**Call relations**: This route is called by HTTP POST /v1/onboard/web. It is the web-specific wrapper around the shared onboarding state machine, converting the shared directive format into JSON for the browser.

*Call graph*: calls 1 internal fn (_request_body); 5 external calls (__init__, JSONResponse, directive, render, parse_directives).


##### `gateway_app.onboard`  (lines 366–391)

```
async def onboard(channel: str, request: Request) -> Response
```

**Purpose**: Runs one step of onboarding for non-web clients, especially the terminal client. It returns plain text directives that the client can interpret directly.

**Data flow**: It receives a channel in the URL, reads the x-ufo-session header, and prepares any first-run install directives from headers → if the session is missing, it returns an error directive → otherwise it checks channel and session sizes, reads the request body, and calls Onboarding.advance → returns the resulting directives as plain text. Invalid input and unexpected failures are turned into plain text error directives.

**Call relations**: This route is called by HTTP POST /v1/onboard/{channel}. It is the terminal-oriented wrapper around Onboarding.advance and includes first_run_install output so a fresh client can be guided through setup.

*Call graph*: calls 1 internal fn (_request_body); 5 external calls (__init__, PlainTextResponse, directive, first_run_install, render).


### Client-facing onboarding surfaces
Terminal directives and browser rendering present the onboarding flow to users across supported clients.

### `control/src/ufo_control/gateway_directives.py`

`io_transport` · `request handling`

This file defines the tiny “wire format” used for server-to-terminal instructions. A wire format is just an agreed way to write messages so both sides understand them. Here, each instruction is one line of bytes: a command word, followed by optional fields, separated by tab characters, and ending with a newline.

The main job is to make sure these messages are safe and unambiguous. If a field contains a backslash, tab, or newline, `directive` escapes it so the client does not mistake ordinary text for message structure. This is like putting fragile items in labeled boxes before shipping them, so they arrive without being confused with the packaging.

The file also includes a small helper for reading HTTP-style headers without caring about letter case, because header names like `X-UFO-Installed` and `x-ufo-installed` should mean the same thing.

The most important behavior is the first-run install trigger. When someone starts with a fresh `curl | sh` flow, the server checks whether the client reports that UFO is already installed. If not, it sends an `install` directive before the normal screen. Once the client has installed itself and reports `x-ufo-installed: 1`, that extra instruction stops appearing.

#### Function details

##### `directive`  (lines 8–13)

```
def directive(verb: str, *fields: str) -> bytes
```

**Purpose**: This function creates one server directive as bytes that can be sent to the terminal client. It turns a command word and optional text fields into a single safely formatted line.

**Data flow**: It receives a directive name, called the verb, plus any number of text fields. It escapes characters that could confuse the line format, joins the pieces with tabs, adds a newline, and returns the result as bytes ready to send over the connection.

**Call relations**: When `first_run_install` decides a new client needs installation instructions, it calls `directive` to build the actual `install` message. Other code can also use this as the standard way to create client-readable directive lines.

*Call graph*: called by 1 (first_run_install).


##### `render`  (lines 16–17)

```
def render(*lines: bytes) -> bytes
```

**Purpose**: This function combines several already-built byte messages into one byte stream. It is useful when the server wants to send multiple directives or screen fragments together.

**Data flow**: It receives any number of byte strings. It joins them in the same order without adding anything extra, and returns one combined byte string.

**Call relations**: This function is a simple packaging step. The call graph provided does not show another function in this file calling it, but its role is to let higher-level response code assemble several directive lines into one response.


##### `header_value`  (lines 20–25)

```
def header_value(headers: Mapping[str, str], name: str) -> str | None
```

**Purpose**: This function looks up a header value by name without caring about capitalization. That matters because HTTP-style header names are meant to be case-insensitive.

**Data flow**: It receives a mapping of header names to values and the header name to search for. It compares names in lowercase form, returns the matching value if it finds one, and returns `None` if the header is absent.

**Call relations**: `first_run_install` calls this function to check whether the incoming request says UFO is already installed. By isolating the case-insensitive lookup here, the install decision can stay simple and readable.

*Call graph*: called by 1 (first_run_install).


##### `first_run_install`  (lines 28–35)

```
def first_run_install(headers: Mapping[str, str]) -> bytes
```

**Purpose**: This function decides whether to prepend a first-time `install` directive for a client. It prevents already-installed clients from being told to install again.

**Data flow**: It receives request headers. It reads the `x-ufo-installed` header using `header_value`; if the value is exactly `1`, it returns empty bytes, meaning no install instruction is needed. Otherwise, it uses `directive` to return an `install` message as bytes.

**Call relations**: This is the decision point for the first-run flow. During request handling, higher-level code can call it before rendering the rest of the terminal session. It delegates header lookup to `header_value` and message construction to `directive`, then hands back either an install command or nothing.

*Call graph*: calls 2 internal fn (directive, header_value).


### `control/src/ufo_control/gateway_web.py`

`io_transport` · `request handling`

The project has an onboarding machine that speaks in simple directive lines such as “say this”, “ask this”, “here is a token”, and “here is the workspace”. This file is the web-facing presentation layer for that machine. It does not create a separate web-only sign-in process; it lets the browser drive the same underlying onboarding flow as the terminal client.

The large `LOGIN_PAGE` string is a complete HTML page with its own styling and JavaScript. When opened, it creates a random browser session id, calls the onboarding endpoint, shows transcript messages, asks the user for answers like email or code, and finally shows a signed-in card with the workspace URL and terminal install command. If a special debugger directive is present, the page shows a form that sends the token by POST instead of putting it in the URL, which avoids leaking the bearer token through browser history or logs.

The Python helper `parse_directives` translates the onboarding machine’s line-based byte output into ordinary dictionaries that can be returned as JSON. Its partner `_unescape` reverses the escaping used inside directive fields, so tabs and newlines inside user-visible text are preserved instead of being confused with the line format itself. In short, this file is the adapter between a plain text onboarding protocol and a friendly browser experience.

#### Function details

##### `parse_directives`  (lines 15–24)

```
def parse_directives(payload: bytes) -> list[dict[str, object]]
```

**Purpose**: This function turns raw directive text from the onboarding machine into JSON-shaped data the browser can understand. It preserves the command word, such as `say` or `ask`, and separates out the attached fields.

**Data flow**: It receives bytes containing one or more directive lines. It decodes those bytes into text, skips blank lines, splits each line into a verb and tab-separated fields, and asks `_unescape` to restore any tabs, newlines, or backslashes that were protected for transport. It returns a list of dictionaries, each with a `verb` and a `fields` list.

**Call relations**: In the web onboarding flow, this is the bridge from the onboarding machine’s compact line format to the browser’s JSON format. While doing that conversion, it calls `_unescape` for each field so the browser receives the original human-readable text rather than the escaped wire version.

*Call graph*: calls 1 internal fn (_unescape).


##### `_unescape`  (lines 27–40)

```
def _unescape(field: str) -> str
```

**Purpose**: This function restores special characters inside one directive field. It is needed because tabs and newlines are used to frame the directive format, so real tabs and newlines inside field text must be temporarily written in escaped form.

**Data flow**: It receives one text field that may contain escape sequences such as `\t`, `\n`, or `\\`. It walks through the characters from left to right, replacing recognized escape pairs with the real character and leaving anything else alone. It returns the cleaned-up string.

**Call relations**: This is a small helper used by `parse_directives`. `parse_directives` separates the directive into fields, then hands each field here so the final JSON data contains the same text the onboarding machine originally meant to send.

*Call graph*: called by 1 (parse_directives).


### Email claim verification
Work-email validation, code delivery, and persisted claim state let the gateway verify a user before workspace access is granted.

### `control/src/ufo_control/gateway_claim.py`

`domain_logic` · `onboarding request handling`

This file protects the onboarding flow from people claiming a work email they do not control. It is like giving someone a numbered ticket by email and asking them to read it back before they can continue. The code is only valid for a short time, and the user only gets a limited number of tries.

The main piece is ClaimWorkflow. When onboarding starts, it first asks the WorkEmailPolicy whether the email address is allowed and what domain it belongs to. Then it creates a random six-digit code. The plain code is emailed to the user, but the system stores only a hash, which is a one-way fingerprint of the code. That matters because if the database is read by mistake or by an attacker, the usable code is not sitting there in plain text.

The workflow saves a claim record with the email, domain, expiry time, attempt count, and other onboarding details. If sending the email fails, it deletes the claim again so the system is not left with a half-started verification.

When the user submits a code, the workflow checks three things: too many tries, expired code, and whether the code matches. Failed or expired claims are cleaned up when appropriate. A successful match marks the claim as verified.

#### Function details

##### `hash_code`  (lines 27–28)

```
def hash_code(code: str) -> str
```

**Purpose**: This turns a verification code into a secure fingerprint using SHA-256, a common one-way hashing method. The system uses this so it can check a code later without saving the real code.

**Data flow**: It receives the code as text, converts it into bytes, and runs it through the SHA-256 hashing function. It returns the resulting hexadecimal text fingerprint. It does not change any outside state.

**Call relations**: ClaimWorkflow.start calls this before saving a new claim, so only the fingerprint goes into storage. ClaimWorkflow.verify calls it again on the code the user typed, then compares that new fingerprint with the saved one.

*Call graph*: called by 2 (start, verify); 1 external calls (sha256).


##### `ClaimWorkflow.start`  (lines 39–61)

```
async def start(self, email: str, surface: str, surface_ref: str) -> str
```

**Purpose**: This begins an email claim for onboarding. It checks that the email is acceptable, creates a temporary verification code, stores the claim, and sends the code to the user.

**Data flow**: It receives an email address plus information about where the onboarding is happening, such as the surface and its reference. It validates the email, makes a random six-digit code, builds an OnboardClaim with a unique ID, expiry time, hashed code, and zero attempts, then saves it through the store. It asks the email sender to deliver the real code. If sending fails, it deletes the claim and raises a ClaimError. If everything works, it returns the validated email domain.

**Call relations**: This is used at the start of the onboarding verification story. It relies on hash_code to avoid storing the real code, uses uuid4 to give the claim its own identity, uses the current time to set the expiry, and uses random number generation for the code. It hands the claim to the onboarding store for persistence and hands the plain code to the email sender so the user can receive it.

*Call graph*: calls 1 internal fn (hash_code); 5 external calls (__init__, __init__, now, randbelow, uuid4).


##### `ClaimWorkflow.verify`  (lines 63–73)

```
async def verify(self, claim: OnboardClaim, code: str) -> None
```

**Purpose**: This checks whether a submitted verification code proves that the user controls the work email. It also enforces the safety rules: no expired codes and no unlimited guessing.

**Data flow**: It receives an existing claim and the code the user entered. First it checks whether the claim has already used too many attempts; if so, it deletes the claim and raises a ClaimError. Next it checks whether the expiry time has passed; if so, it deletes the claim and raises a ClaimError. Otherwise it records one more attempt, hashes the submitted code, and compares that hash with the stored hash. A mismatch raises a ClaimError. A match marks the claim as verified in the store.

**Call relations**: This is called after a claim has already been started and the user has replied with a code. It uses hash_code in the same way start did, so the comparison is fingerprint-to-fingerprint rather than plain-code-to-plain-code. It uses hmac.compare_digest for the comparison, which is a safer equality check designed not to leak small timing clues about the correct value.

*Call graph*: calls 1 internal fn (hash_code); 3 external calls (__init__, now, compare_digest).


### `control/src/ufo_control/gateway_email.py`

`io_transport` · `request handling and onboarding`

This file solves two connected onboarding problems. First, it filters out personal or throwaway email domains, so a workspace is more likely to represent a real organization rather than a random mailbox. Second, it delivers short verification codes that prove the user can receive mail at that address.

The file starts with a work-email policy. It cleans and checks an address, rejects malformed addresses, and blocks known free or disposable domains such as Gmail or Mailinator. That is the “front desk” check before the system trusts an email domain.

It then builds the text for two kinds of messages: ordinary verification-code emails and invite emails that include a command the operator can share by hand.

For delivery, the file offers two senders. The production sender, `SesEmailSender`, talks directly to Amazon SES, Amazon’s email-sending service. It first exchanges the pod’s web identity token for temporary AWS credentials through STS, Amazon’s token service. Then it signs the SES request using SigV4, Amazon’s request-signing method, and sends it with asynchronous HTTP so the event loop is not blocked. The local sender, `ConsoleEmailSender`, logs the code instead of emailing it, which is useful for development.

Configuration is intentionally strict. Missing SES settings or an unknown email mode fail immediately, so a deployment does not silently lose verification emails.

#### Function details

##### `normalize_email`  (lines 122–128)

```
def normalize_email(email: str) -> tuple[str, str]
```

**Purpose**: This function turns an email address into a clean, lowercase form and pulls out its domain. It is the basic sanity check before the system decides whether the address is acceptable.

**Data flow**: It receives a raw email string. It trims surrounding spaces, lowercases it, and checks that it looks like one address with a domain after `@`. If the shape is bad, it raises `WorkEmailError`; otherwise it returns the cleaned full address and the domain.

**Call relations**: The work-email policy calls this first. `WorkEmailPolicy.validate` depends on it to separate “badly written address” from “well-formed address on a blocked domain.”

*Call graph*: called by 1 (validate); 1 external calls (__init__).


##### `WorkEmailPolicy.validate`  (lines 135–139)

```
def validate(self, email: str) -> str
```

**Purpose**: This method checks whether an email belongs to an allowed work domain. It rejects personal and disposable email providers so signups map to organizations rather than throwaway inboxes.

**Data flow**: It receives an email address. It asks `normalize_email` to clean it and extract the domain, then compares that domain with the denylist. If the domain is blocked, it raises `WorkEmailError`; otherwise it returns the accepted domain.

**Call relations**: This is the policy gate that other onboarding code can call before sending or trusting a verification code. Inside the file, it builds directly on `normalize_email` and raises the same kind of error when the domain is not allowed.

*Call graph*: calls 1 internal fn (normalize_email); 1 external calls (__init__).


##### `public_apex_host`  (lines 142–150)

```
def public_apex_host() -> str
```

**Purpose**: This function finds the public host name users should reach, without the URL scheme or path. It is used when generated text needs a clean host such as `flyingobject.ai`.

**Data flow**: It reads `UFO_PUBLIC_BASE_URL` from the environment, or uses the default public URL if it is not set. It parses that URL and extracts the host. If no host can be found, it raises an error; otherwise it returns the host string.

**Call relations**: No internal caller is shown in this file’s call facts, but it supports flows that need to print or send a public-facing install or invite URL. It relies on URL parsing from the standard library to avoid hand-splitting strings.

*Call graph*: 1 external calls (urlsplit).


##### `verification_email`  (lines 153–159)

```
def verification_email(code: str, expires_at: datetime, ttl: timedelta) -> tuple[str, str]
```

**Purpose**: This function creates the subject and body for a normal verification-code email. It keeps the wording consistent whether the code is really emailed or only logged locally.

**Data flow**: It receives a code, an expiration time, and a time-to-live duration. It formats the expiration as an hour and minute, converts the duration into minutes, and returns a subject/body pair ready to send.

**Call relations**: `SesEmailSender.send` uses this before sending real mail through SES. `ConsoleEmailSender.send` uses the same formatter before writing the message to the log, so local and production modes show the same code text.

*Call graph*: called by 2 (send, send); 2 external calls (strftime, total_seconds).


##### `invite_email`  (lines 162–172)

```
def invite_email(object_number: int, code: str, expires_at: datetime, apex_host: str) -> tuple[str, str]
```

**Purpose**: This function creates the subject and body for an invite message that an operator can send manually. The message includes an object number, a code, an expiration time, and an install command.

**Data flow**: It receives the object number, invite code, expiration time, and public host. It converts the expiration time to UTC, fills those values into a fixed text template, and returns the subject and body.

**Call relations**: No internal caller is shown in the provided call facts. It is designed for the invite workflow, where another command can ask this file to produce the exact text that should be shared.

*Call graph*: 1 external calls (astimezone).


##### `EmailSender.send`  (lines 176–176)

```
async def send(self, email: str, code: str, expires_at: datetime, ttl: timedelta) -> None
```

**Purpose**: This is the shared promise that all email senders must keep: given an address and verification-code details, send or otherwise deliver the code. It lets the rest of the gateway use “an email sender” without caring whether it is SES or console mode.

**Data flow**: It defines the expected inputs: destination email, code, expiration time, and time-to-live. As a protocol method, it does not perform work itself; concrete senders provide the actual before-to-after behavior.

**Call relations**: The production implementation is `SesEmailSender.send`, and the local-development implementation is `ConsoleEmailSender.send`. Code outside this file can depend on the protocol and receive whichever sender `email_sender_from_env` chooses.


##### `SesEmailSender.send`  (lines 199–221)

```
async def send(self, email: str, code: str, expires_at: datetime, ttl: timedelta) -> None
```

**Purpose**: This method sends a verification code through Amazon SES, the production email service. It does the whole delivery path: make the message, get temporary AWS credentials, sign the request, and post it to SES.

**Data flow**: It receives the destination email, code, expiration time, and time-to-live. It builds the email text with `verification_email`, gets temporary credentials from `_assume_role`, creates a JSON request body for SES, signs that body with `_sigv4_headers`, and sends it over asynchronous HTTP. If SES returns an error, it raises a runtime error with the status and a shortened response body; otherwise it finishes with no returned value.

**Call relations**: This is the real-mail implementation of `EmailSender.send`. It calls `_assume_role` before SES because the pod starts with a web identity token, not ordinary AWS keys. It then hands the signed request to `httpx.AsyncClient` for network delivery.

*Call graph*: calls 3 internal fn (_assume_role, _sigv4_headers, verification_email); 3 external calls (now, AsyncClient, dumps).


##### `SesEmailSender._assume_role`  (lines 223–242)

```
async def _assume_role(self) -> SesCredentials
```

**Purpose**: This method turns the pod’s web identity token into temporary AWS credentials that can send email. It is needed because SES requests must be signed with AWS credentials, but the running pod does not store long-lived keys.

**Data flow**: It reads the token file configured on the sender. It posts that token, the role ARN, and session details to AWS STS. If STS rejects the request, it raises an error; otherwise it passes the XML response to `_parse_assume_role_credentials` and returns the extracted access key, secret key, and session token.

**Call relations**: `SesEmailSender.send` calls this immediately before sending each email. After STS responds, this method hands off parsing to `_parse_assume_role_credentials` so the send method receives a simple credentials object instead of raw XML.

*Call graph*: calls 1 internal fn (_parse_assume_role_credentials); called by 1 (send); 1 external calls (AsyncClient).


##### `_parse_assume_role_credentials`  (lines 245–258)

```
def _parse_assume_role_credentials(payload: str) -> SesCredentials
```

**Purpose**: This helper extracts the three useful credential values from the XML response returned by AWS STS. It turns a service-specific document into a simple `SesCredentials` object the rest of the file can use.

**Data flow**: It receives the raw XML response text from STS. It parses the XML, looks under the `Credentials` section for the access key ID, secret access key, and session token, and returns them together. If any required value is missing, the nested `credential` helper raises an error.

**Call relations**: `SesEmailSender._assume_role` calls this after a successful STS HTTP response. This helper hides the XML details so the rest of the email flow can work with plain credential fields.

*Call graph*: called by 1 (_assume_role); 2 external calls (__init__, fromstring).


##### `_parse_assume_role_credentials.credential`  (lines 248–252)

```
def credential(name: str) -> str
```

**Purpose**: This nested helper fetches one named credential field from the parsed STS XML. It gives a clear error if AWS returned a response that does not contain the expected value.

**Data flow**: It receives the name of a credential field, such as `AccessKeyId`. It searches the already-parsed XML document from the enclosing function. If it finds a non-empty value, it returns that text; if not, it raises a runtime error naming the missing field.

**Call relations**: It is used only inside `_parse_assume_role_credentials`. The outer function calls it once for each required credential value before building the `SesCredentials` result.


##### `_sigv4_headers`  (lines 261–294)

```
def _sigv4_headers(host: str, body: bytes, region: str, credentials: SesCredentials, now: datetime) -> dict[str, str]
```

**Purpose**: This helper creates the special HTTP headers Amazon requires to prove a SES request is authentic. SigV4 is AWS’s signing system: it combines the request details, time, region, and secret key into a signature Amazon can verify.

**Data flow**: It receives the SES host, request body bytes, AWS region, temporary credentials, and current time. It hashes the body, builds the canonical request text AWS expects, creates a string to sign, asks `_signing_key` for the derived signing key, and computes the final HMAC signature. It returns a headers dictionary containing content information, the session token, date, and authorization signature.

**Call relations**: `SesEmailSender.send` calls this after building the SES JSON body and before making the HTTP request. This function calls `_signing_key` for the low-level key derivation, then hands signed headers back to the sender for the final network call.

*Call graph*: calls 1 internal fn (_signing_key); called by 1 (send); 3 external calls (strftime, sha256, new).


##### `_signing_key`  (lines 297–301)

```
def _signing_key(secret_key: str, date_stamp: str, region: str) -> bytes
```

**Purpose**: This helper derives the short-lived signing key used by AWS SigV4. It is like making a purpose-specific stamp from the secret key, date, region, and service name.

**Data flow**: It receives the AWS secret key, date stamp, and region. It repeatedly applies HMAC hashing with the date, region, SES service name, and final AWS marker. It returns the derived bytes that can sign one AWS request scope.

**Call relations**: _sigv4_headers calls this while preparing the SES authorization header. It does not send anything itself; it only supplies the cryptographic key material needed for the signature.

*Call graph*: called by 1 (_sigv4_headers); 1 external calls (new).


##### `ConsoleEmailSender.send`  (lines 311–313)

```
async def send(self, email: str, code: str, expires_at: datetime, ttl: timedelta) -> None
```

**Purpose**: This method delivers a verification code by writing it to the application log instead of sending email. It is meant for local development, where developers need the code but do not want to configure an SES account.

**Data flow**: It receives the destination email, code, expiration time, and time-to-live. It formats the same subject and body that a real email would use by calling `verification_email`, then logs the email address, subject, and text. It returns nothing and does not contact any outside service.

**Call relations**: This is the local-mode implementation of `EmailSender.send`. `email_sender_from_env` creates this sender when `UFO_CONTROL_EMAIL_MODE` is set to console, and the method reuses `verification_email` so console output matches production message content.

*Call graph*: calls 1 internal fn (verification_email).


##### `email_sender_from_env`  (lines 316–330)

```
def email_sender_from_env() -> EmailSender
```

**Purpose**: This function chooses which email sender the gateway should use based on environment variables. It is the switch between production SES delivery and local console logging.

**Data flow**: It reads `UFO_CONTROL_EMAIL_MODE`, defaulting to SES mode. In console mode, it returns a `ConsoleEmailSender`. In SES mode, it requires the sender address, role ARN, and token file path, reads the optional region, and returns a configured `SesEmailSender`. If the mode is unknown, it raises an error.

**Call relations**: Other startup or wiring code can call this to get an `EmailSender` without knowing the details. It delegates required environment checks to `_require_env` and constructs either the console sender or the SES sender.

*Call graph*: calls 1 internal fn (_require_env); 3 external calls (__init__, __init__, Path).


##### `_require_env`  (lines 333–337)

```
def _require_env(name: str) -> str
```

**Purpose**: This helper reads an environment variable that must be present. It makes missing email configuration fail loudly instead of causing a confusing error later.

**Data flow**: It receives the name of an environment variable. It reads that value from the process environment. If the value is missing or empty, it raises a runtime error; otherwise it returns the string value.

**Call relations**: `email_sender_from_env` calls this for the SES settings that cannot be guessed safely, such as the sender address, AWS role ARN, and web identity token file path.

*Call graph*: called by 1 (email_sender_from_env).


### `control/src/ufo_control/gateway_store.py`

`io_transport` · `request handling during onboarding`

This file gives the control service a durable place to store onboarding claims. An onboarding claim is a temporary promise like: “this email address is trying to claim access through this surface, using this verification code, before this expiry time.” Without this file, the system would have no reliable memory of pending onboarding attempts, verified claims, or completed claims once the server restarted.

The file defines the shape of the Postgres table and an `OnboardClaim` data object, which is a simple typed bundle of claim fields. The `OnboardStore` class is the working part. It receives an `asyncpg` connection pool, which is a reusable set of database connections for asynchronous code, and uses it to insert, read, update, and delete claim rows.

One important database rule appears in the table setup: for a given `surface` and `surface_ref`, there can be only one active claim that has not yet produced a workspace. In plain terms, the same doorway cannot have two unfinished onboarding tickets at the same time.

The methods are deliberately small. Some create or fetch full claim records. Others update one piece of state, such as the attempt count, the verification timestamp, or the final workspace ID. A tiny helper, `_aware`, makes sure timestamps read from the database include timezone information, so later time comparisons do not accidentally mix timezone-aware and timezone-naive dates.

#### Function details

##### `_aware`  (lines 46–49)

```
def _aware(value: datetime | None) -> datetime | None
```

**Purpose**: This helper makes sure a timestamp either stays missing or has timezone information attached. It prevents later code from comparing dates that mean different things because one knows its timezone and the other does not.

**Data flow**: It receives either a date-and-time value or nothing. If it receives nothing, it returns nothing. If the date already has timezone information, it returns it unchanged; otherwise it labels it as UTC, meaning Coordinated Universal Time, the common reference clock used by servers.

**Call relations**: When `OnboardStore.live_claim` rebuilds an `OnboardClaim` from a database row, it calls `_aware` for timestamp fields. `_aware` may use the datetime object's `replace` operation to attach UTC when the database value came back without timezone information.

*Call graph*: called by 1 (live_claim); 1 external calls (replace).


##### `OnboardStore.insert_claim`  (lines 56–70)

```
async def insert_claim(self, claim: OnboardClaim) -> None
```

**Purpose**: This saves a new onboarding claim into Postgres. It is used when someone starts an onboarding flow and the service needs a durable record of their email, verification code hash, entry point, and expiry time.

**Data flow**: It receives an `OnboardClaim` object. It opens a database connection from the pool and writes the claim's main fields into the onboarding table. It does not return a value; after it finishes, the claim exists in the database unless the database rejects it, for example because an active claim already exists for the same surface and reference.

**Call relations**: This method is an entry point into the store for the code that creates onboarding claims. It does the database write directly and does not delegate to the shared update helper because it is creating a whole new row rather than changing an existing one.


##### `OnboardStore.live_claim`  (lines 72–93)

```
async def live_claim(self, surface: str, surface_ref: str) -> OnboardClaim | None
```

**Purpose**: This looks up the current unfinished onboarding claim for a particular surface and surface reference. It answers the question: “Is there already an active claim for this doorway?”

**Data flow**: It receives a `surface` and `surface_ref`, opens a database connection, and searches for a row with those values whose `resulting_workspace_id` is still empty. If no such row exists, it returns nothing. If it finds one, it turns the database row back into an `OnboardClaim`, converting timestamps through `_aware` so they are safe to use.

**Call relations**: This is called by onboarding code that needs to continue or inspect an existing claim. Inside, it calls `_aware` for time fields and constructs an `OnboardClaim` object so the rest of the application can work with a clear Python data object instead of raw database columns.

*Call graph*: calls 1 internal fn (_aware); 1 external calls (__init__).


##### `OnboardStore.record_attempt`  (lines 95–96)

```
async def record_attempt(self, claim_id: UUID, attempts: int) -> None
```

**Purpose**: This updates how many verification attempts have been made for a claim. It lets the onboarding flow keep count, which is important for limiting guessing or abuse.

**Data flow**: It receives a claim ID and the new attempt count. It passes those to the shared `_update` helper with the instruction to set the `attempts` field. It returns nothing; the visible change is in the database row.

**Call relations**: This method is a small, named wrapper around `_update`. Higher-level onboarding logic can say “record this attempt count” without needing to know the SQL details.

*Call graph*: calls 1 internal fn (_update).


##### `OnboardStore.mark_verified`  (lines 98–99)

```
async def mark_verified(self, claim_id: UUID) -> None
```

**Purpose**: This marks a claim as having passed verification. In practice, it records the current database time as the moment the claim was verified.

**Data flow**: It receives a claim ID. It asks `_update` to set `verified_at` to `now()`, which means the database server's current time. It returns nothing; after it runs, the claim row shows that verification happened.

**Call relations**: This method is used after the onboarding code has accepted the user's proof, such as a correct code. It delegates the actual database update to `_update` so the connection and execution pattern stays in one place.

*Call graph*: calls 1 internal fn (_update).


##### `OnboardStore.complete`  (lines 101–102)

```
async def complete(self, claim_id: UUID, resulting_workspace_id: str) -> None
```

**Purpose**: This records that an onboarding claim has produced a workspace. That turns the claim from active into finished, because active claims are defined as rows without a resulting workspace ID.

**Data flow**: It receives a claim ID and the ID of the workspace created from that claim. It calls `_update` to store that workspace ID in the database. It returns nothing; the important result is that the claim is no longer considered live.

**Call relations**: This method is used near the end of a successful onboarding path. By calling `_update`, it changes only the completion field while leaving the rest of the claim history intact.

*Call graph*: calls 1 internal fn (_update).


##### `OnboardStore.delete_claim`  (lines 104–106)

```
async def delete_claim(self, claim_id: UUID) -> None
```

**Purpose**: This removes an onboarding claim from the database. It is useful when a claim must be discarded instead of completed, such as after cancellation or cleanup.

**Data flow**: It receives a claim ID, opens a database connection, and deletes the matching row from the onboarding table. It returns nothing; after it finishes, that claim record is gone if it existed.

**Call relations**: This method is a direct database operation used by code that decides a claim should no longer be kept. It does not use `_update` because it removes the row rather than changing fields inside it.


##### `OnboardStore._update`  (lines 108–112)

```
async def _update(self, assignment: str, claim_id: UUID, *values: object) -> None
```

**Purpose**: This is the shared private helper for changing one field or expression on an existing claim row. It keeps the repeated pattern of opening a connection and running an update in one place.

**Data flow**: It receives a SQL assignment snippet, a claim ID, and any extra values needed by that assignment. It opens a database connection and runs an update against the row with that ID. It returns nothing; the output is the changed database row.

**Call relations**: `record_attempt`, `mark_verified`, and `complete` all call this helper when they need to change an existing claim. It is kept private because callers should use the clearer named methods instead of passing raw update instructions themselves.

*Call graph*: called by 3 (complete, mark_verified, record_attempt).


### Workspace access and creation
Invite gates and company-domain lookup decide whether onboarding joins an existing workspace or safely creates a new one.

### `control/src/ufo_control/gateway_invite.py`

`domain_logic` · `invite creation and signup/workspace-creation redemption`

This file is the “ticket desk” for workspace creation. Some people can join an existing workspace without a code, but creating a brand-new workspace needs an invite. This code creates those invites, stores only a safe fingerprint of each code in the database, and later checks and consumes the code when someone redeems it.

The important safety rule is that a code must not be reusable. To make that true, redemption happens inside a database transaction, which means several related database changes succeed or fail together. The row is also locked while it is being checked, like putting a hand on a paper form while stamping it, so two signups cannot both redeem the same code at the same time.

The file defines the database table shape, the random human-readable code format, and small result objects such as “unknown,” “expired,” “already consumed,” and “accepted.” The main class, `InviteCodes`, has two jobs: `mint` creates a new code for a waitlist object, and `redeem` validates a submitted code, marks it consumed, and links it to the claim record. Only the hash of the code is stored, not the code itself, so a database leak would not directly reveal usable invite codes.

#### Function details

##### `mint_code`  (lines 46–50)

```
def mint_code() -> str
```

**Purpose**: Creates a new random invite code that is easy enough for a person to type. The code is split into short groups and avoids confusing characters, like letters or numbers that are often mistaken for each other.

**Data flow**: It starts with no input. It repeatedly chooses random characters from the allowed invite alphabet, groups them into three chunks, joins the chunks with hyphens, and returns the finished code as text.

**Call relations**: When `InviteCodes.mint` needs a fresh invite, it calls this function first. The returned code is later hashed before storage, while the plain code is returned once so it can be sent to the invited person.

*Call graph*: called by 1 (mint); 1 external calls (choice).


##### `hash_invite`  (lines 53–54)

```
def hash_invite(code: str) -> str
```

**Purpose**: Turns an invite code into a secure fingerprint for storage and lookup. This lets the system compare codes later without keeping the actual code in the database.

**Data flow**: It receives a code as text, trims extra spaces, lowercases it so typing case does not matter, converts it to bytes, and runs it through SHA-256, a standard one-way hashing method. It returns the hash as a hexadecimal string.

**Call relations**: `InviteCodes.mint` uses this before saving a new code, and `InviteCodes.redeem` uses it to look up a submitted code. This keeps creation and redemption using the same normalized form, so harmless differences like uppercase letters do not break redemption.

*Call graph*: called by 2 (mint, redeem); 1 external calls (sha256).


##### `InviteCodes.mint`  (lines 91–130)

```
async def mint(self, object_number: int) -> MintedInvite
```

**Purpose**: Creates a new invite code for a specific waitlist object, unless that object is already identified or already has a still-valid code. It exists to prevent duplicate live invitations for the same object.

**Data flow**: It receives an object number. It creates a random code, calculates an expiry time, opens a database transaction, and checks whether this object already has a consumed code or a live unconsumed code. If the object is already identified or already has a live code, it raises `InviteError`. Otherwise, it removes any old expired unused code, stores a new row containing a fresh invite ID, the code hash, the object number, and the expiry time, then returns a `MintedInvite` containing the plain code and expiry details.

**Call relations**: This is called by the invite-issuing flow, such as an operator command that mints an invite for a waitlist object. It relies on `mint_code` for the human-facing secret and `hash_invite` for the database-safe version. If another process races to create a live code at the same time, the database uniqueness rule catches it and this method reports that a live code already exists.

*Call graph*: calls 2 internal fn (hash_invite, mint_code); 4 external calls (__init__, __init__, now, uuid4).


##### `InviteCodes.redeem`  (lines 132–161)

```
async def redeem(self, code: str, claim_id: UUID) -> InviteUnknown | InviteExpired | InviteConsumed | InviteAccepted
```

**Purpose**: Checks a submitted invite code and, if it is valid, consumes it and attaches it to a claim record. It returns a clear result describing whether the code was unknown, expired, already used, or accepted.

**Data flow**: It receives the typed code and the claim ID that should receive the invite link. It hashes the code, opens a database transaction, and looks up the invite row while locking it so nobody else can change it at the same moment. If no row exists, it returns `InviteUnknown`. If the row was already consumed, it returns `InviteConsumed`. If the expiry time has passed, it returns `InviteExpired`. Otherwise, it marks the invite as consumed, updates the related claim with the invite ID, and returns `InviteAccepted` with the invite ID, object number, and consumption time.

**Call relations**: This is used during the workspace-creation or signup flow when a person enters an invite code. It calls `hash_invite` so the typed code can be matched against the stored hash. Its transaction ties together two important actions — consuming the invite and linking it to the claim — so a crash cannot leave behind a used code that is not connected to the claim it opened.

*Call graph*: calls 1 internal fn (hash_invite); 5 external calls (__init__, __init__, __init__, __init__, now).


### `control/src/ufo_control/gateway_shared.py`

`domain_logic` · `hosted onboarding`

This file exists so that people from the same verified organization domain, such as everyone with an email ending in `@example.com`, land in the same shared workspace instead of each creating separate islands. Think of it like a building directory: if the company already has an office, new employees are sent there; if not, the first employee gets a new office created for the company.

The main class, `SharedWorkspaces`, uses a database connection pool to look up and create workspace records. It first checks whether a workspace already belongs to the domain. It does this in two ways: by looking for the predictable workspace id derived from the domain, and by checking the email domain of the earliest member in existing workspaces. If the domain appears to point to more than one workspace, it raises an error rather than guessing, because silently choosing the wrong workspace could put a user in the wrong organization.

When a workspace is ensured, the file creates the workspace row if needed, adds the user as a member, and creates a default agent for the workspace if one does not already exist. It also reports whether this user is the owner, meaning the earliest member, so later onboarding can offer owner-only choices such as billing setup.

#### Function details

##### `serve_dsn`  (lines 20–27)

```
def serve_dsn() -> str
```

**Purpose**: This function reads the database connection string used by hosted onboarding when it must write workspace data as the correct serve role. It stops immediately with a clear error if that required setting is missing.

**Data flow**: It reads the `UFO_CONTROL_SERVE_DSN` environment variable from the process environment. If the value is present, it returns that string. If it is absent or empty, it raises a runtime error explaining that hosted onboarding cannot safely write the workspace row without it.

**Call relations**: This is a small configuration helper for the onboarding path. Other startup or setup code can call it before building database access, so the system fails early instead of reaching a database write later with the wrong or missing credentials.


##### `SharedWorkspaces.exists`  (lines 47–48)

```
async def exists(self, domain: str) -> bool
```

**Purpose**: This method answers a simple question: does this organization domain already have a shared workspace? It is useful when onboarding wants to check before deciding what message or next step to show.

**Data flow**: It receives a domain name, passes it to the internal lookup method, and checks whether that lookup returned a workspace id. It returns `true` if a matching workspace was found and `false` if not.

**Call relations**: This is the lightweight public check on top of `SharedWorkspaces._existing`. It does not create or change anything; it only asks the same lookup logic used by `SharedWorkspaces.ensure` whether a domain is already known.

*Call graph*: calls 1 internal fn (_existing).


##### `SharedWorkspaces.ensure`  (lines 50–77)

```
async def ensure(self, domain: str, email: str) -> EnsuredWorkspace
```

**Purpose**: This method makes sure a verified domain has exactly one shared workspace and that the given email address is a member of it. It is the main onboarding action for joining or creating a domain-owned workspace.

**Data flow**: It receives a domain and an email address. First it looks for an existing workspace for the domain; if none is found, it creates a predictable workspace id from the lowercase domain. It lowercases and trims the email address, opens a workspace-scoped database transaction, inserts the workspace if it is missing, creates the member, inserts the default agent if needed, and asks which member is the owner. It returns an `EnsuredWorkspace` value containing the workspace id as text and a yes-or-no owner flag for this user.

**Call relations**: This method sits at the center of the hosted onboarding flow. It relies on `SharedWorkspaces._existing` to avoid duplicates, uses the workspace context and transaction helpers to write under the right workspace, calls member-seat helpers to add the user and find the owner, and uses PostgreSQL insert-on-conflict behavior so repeated onboarding attempts do not create duplicate workspace or agent rows.

*Call graph*: calls 1 internal fn (_existing); 8 external calls (__init__, insert, workspace_tx, create_member, owner_member_id, ws, uuid4, uuid5).


##### `SharedWorkspaces._existing`  (lines 79–97)

```
async def _existing(self, domain: str) -> UUID | None
```

**Purpose**: This internal method finds the workspace that already belongs to a domain, if there is one. It also protects the system from ambiguous domain mappings by refusing to continue if one domain appears tied to multiple workspaces.

**Data flow**: It receives a domain, lowercases it, and builds the deterministic workspace id that would be used for that domain. It then borrows a database connection from the async pool and runs a query that looks for either a workspace with that deterministic id or a workspace whose first member has an email address with the same domain. If no rows are found, it returns `None`; if one row is found, it returns that workspace id; if more than one row is found, it raises an error.

**Call relations**: Both public methods in this class depend on this lookup. `SharedWorkspaces.exists` uses it only to answer yes or no, while `SharedWorkspaces.ensure` uses it before creating or joining a workspace. By centralizing the lookup here, both paths share the same safety rule: one verified domain must not silently resolve to multiple workspaces.

*Call graph*: called by 2 (ensure, exists); 1 external calls (uuid5).


### `core/src/ufo/onboarding.py`

`orchestration` · `first-run startup / init`

This file is the “first day setup” checklist for the system. When someone runs the initial setup command, the system must create the permanent core records exactly once: a workspace, its owner member, and a default agent. If this file did not exist, a fresh install would not have a home workspace or owner, and later features would not know where to store or read user data.

The flow is careful about order. First it checks that the selected AI model has the environment variable it needs, if the platform knows one is required. An environment variable is a value supplied outside the program, often used for secret API keys. It also checks whether extension onboarding needs a credential store. This is done before writing to the database, so a failed setup does not leave behind a half-created workspace.

Then it opens a database transaction, checks whether an owner already exists, and refuses to run again if the system is already initialized. That protects the “first owner” from being accidentally duplicated. Inside the same transaction it creates the workspace, creates the member, and inserts the default agent.

Only after the core workspace exists does it run onboarding steps from installed extensions. Each extension gets its own scoped context, like giving each add-on its own labeled toolbox. If one extension step fails, the error is logged and the others still get a chance to run.

#### Function details

##### `run_onboarding_steps`  (lines 47–75)

```
async def run_onboarding_steps(manifests: tuple[Manifest, ...], workspace_id: UUID, credentials: CredentialStore | None) -> None
```

**Purpose**: Runs setup steps supplied by installed extensions after the main workspace already exists. It keeps extension failures from breaking the core setup or stopping other extensions.

**Data flow**: It receives the installed extension manifests, the newly created workspace ID, and an optional credential store. It enters the workspace context, looks at each extension, builds that extension’s scoped context when possible, and calls each onboarding step. If credentials are missing or a step crashes, it logs what happened instead of returning data or changing the core workspace result.

**Call relations**: This is called by Onboarding.run_steps after the workspace and owner have been created. For each extension, it asks context_for to build the extension-specific context, uses ws to mark which workspace the work belongs to, and uses log to record skipped or failed extension setup.

*Call graph*: called by 1 (run_steps); 3 external calls (context_for, log, ws).


##### `Onboarding.run`  (lines 89–92)

```
async def run(self) -> Onboarded
```

**Purpose**: Runs the complete first-time setup in the intended order. It creates the core workspace first, then runs extension setup steps, and finally returns the identities that were created.

**Data flow**: It starts with the onboarding object’s stored configuration, email, model, credentials, and manifests. It calls create to make the durable core records, then passes the resulting workspace and member information into run_steps. It returns the Onboarded result from the creation stage.

**Call relations**: This is the top-level method for the onboarding object. It delegates the irreversible core creation to Onboarding.create, then delegates add-on setup to Onboarding.run_steps so the two phases stay separate.

*Call graph*: calls 2 internal fn (create, run_steps).


##### `Onboarding.create`  (lines 94–100)

```
async def create(self) -> Onboarded
```

**Purpose**: Creates only the core, durable part of onboarding: required key checks, workspace, owner, and default agent. It intentionally does this before extension setup so add-ons cannot leave the system half-initialized.

**Data flow**: It uses the onboarding object’s selected model, configuration, credentials, manifests, and owner email. First it checks that the model key is available, then checks that extension steps have the credential support they need, and then writes the workspace records. It returns an Onboarded value containing the new workspace ID and member ID.

**Call relations**: Onboarding.run calls this as the first phase. This method performs its work by calling Onboarding._require_model_key, Onboarding._require_credentials_for_steps, and Onboarding._create_workspace in that order.

*Call graph*: calls 3 internal fn (_create_workspace, _require_credentials_for_steps, _require_model_key); called by 1 (run).


##### `Onboarding.run_steps`  (lines 102–103)

```
async def run_steps(self, onboarded: Onboarded) -> None
```

**Purpose**: Runs extension onboarding for a workspace that has already been created. It is a small bridge between the Onboarding object and the standalone extension-step runner.

**Data flow**: It receives an Onboarded object, takes the workspace ID from it, and combines that with the stored manifests and credential store on the Onboarding object. It does not return a value; its effect is to give extensions a chance to perform their setup.

**Call relations**: Onboarding.run calls this after Onboarding.create succeeds. It hands the actual work to run_onboarding_steps, which loops through the extensions and calls their setup functions.

*Call graph*: calls 1 internal fn (run_onboarding_steps); called by 1 (run).


##### `Onboarding._require_credentials_for_steps`  (lines 105–116)

```
def _require_credentials_for_steps(self) -> None
```

**Purpose**: Stops setup early if installed extensions have onboarding work that needs a credential store but no credential key is configured. This avoids creating a workspace that cannot finish its required extension setup.

**Data flow**: It reads the Onboarding object’s credential store, extension manifests, and configured credential-key environment name. If a credential store exists, it does nothing. If no credential store exists but any extension has onboarding steps, it raises an error explaining which environment setting is needed.

**Call relations**: Onboarding.create calls this before any database records are written. It is one of the preflight checks that must pass before Onboarding._create_workspace is allowed to create the workspace.

*Call graph*: called by 1 (create).


##### `Onboarding._require_model_key`  (lines 118–125)

```
def _require_model_key(self) -> None
```

**Purpose**: Checks that the selected model has its required secret key available before setup continues. This prevents a new workspace from being created for a model that cannot actually run its first turn.

**Data flow**: It asks Onboarding._model_key_env which environment variable, if any, is required for the selected model. If no known key is needed, it allows setup to continue. If a key name is known but the environment does not contain a value for it, it raises an error.

**Call relations**: Onboarding.create calls this as the first preflight check. It relies on Onboarding._model_key_env to find the right environment variable name, then uses the operating system environment to check whether the value is present.

*Call graph*: calls 1 internal fn (_model_key_env); called by 1 (create).


##### `Onboarding._model_key_env`  (lines 127–130)

```
def _model_key_env(self) -> str | None
```

**Purpose**: Finds the environment variable name that should contain the API key for the selected model, when the core system knows how to check it. If the model comes from an extension that resolves keys later, it may return nothing.

**Data flow**: It reads the configuration, installed manifests, and selected model name from the Onboarding object. It builds the model registry, asks that registry for the key environment variable for the model, and returns either the variable name or no value.

**Call relations**: Onboarding._require_model_key calls this when deciding whether it can check the model key up front. This method hands off model-specific knowledge to model_registry rather than hard-coding provider rules here.

*Call graph*: called by 1 (_require_model_key); 1 external calls (model_registry).


##### `Onboarding._create_workspace`  (lines 132–155)

```
async def _create_workspace(self) -> Onboarded
```

**Purpose**: Writes the first permanent records for a new installation: the workspace, the first member, and the default agent. It also protects the system from being initialized twice.

**Data flow**: It opens a workspace database transaction, checks whether any member email already exists, and raises AlreadyInitialized if an owner is already present. If the database is empty, it generates new IDs, inserts the workspace, creates the owner member from the supplied email, inserts the default agent using the selected model, and returns an Onboarded object with the new IDs.

**Call relations**: Onboarding.create calls this after all preflight checks pass. Inside the transaction it uses workspace_tx for the database boundary, create_member to create the owner, SQLAlchemy insert and select helpers to write and read rows, uuid4 to make new identifiers, and Onboarded to package the result.

*Call graph*: called by 1 (create); 7 external calls (__init__, __init__, insert, select, workspace_tx, create_member, uuid4).

## 📊 State Registers Touched

- `reg-workspace-directory` — The shared record of workspaces, members, owners, agents, and workspace boundaries.
- `reg-auth-session` — The login and token state that proves who a user or client is across gateway, web, terminal, and admin requests.
- `reg-onboarding-state` — The invite codes, email claim codes, onboarding records, and first-workspace setup state for new hosted users.
- `reg-agent-runtime-settings` — Persistent non-prompt agent configuration such as selected runtime profile, workflow/tool policy, internet-access setting, and conversation or surface agent bindings.
