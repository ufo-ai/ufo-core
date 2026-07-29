# Hosted onboarding and workspace provisioning  `stage-4`

This stage is the front door for hosted UFO. It runs during signup and first launch, before the normal workspace work begins. The gateway web server leads a new person through the path: prove an email address, check any invitation, find or create the right workspace, and return the token and workspace address the client will use next. The terminal and browser share the same conversation, but gateway_directives turns server instructions into small messages for the terminal, while gateway_web turns them into browser-friendly JSON.

Several parts protect the signup flow. gateway_claim creates short-lived email codes, stores only a safe hashed copy, and limits retries. gateway_invite tracks one-use invitations for company domains. gateway_email checks that addresses look like work emails and sends the verification and invite messages. gateway_store keeps the claim records in Postgres, the main database. gateway_shared maps each company domain to one shared workspace, creating it only when needed. gateway_token issues the temporary access token. Finally, core onboarding performs first-run setup inside the workspace, creating the first admin, the main assistant, and any extension-provided setup steps.

## Files in this stage

### Gateway conversation surfaces
The hosted gateway starts onboarding and exposes the same flow to terminal and browser clients.

### `control/src/ufo_control/gateway.py`

`entrypoint` · `startup and request handling`

This is the front door for onboarding. A terminal client or web page talks to this FastAPI server, and the server replies with simple “directives” such as “ask for an email,” “show this message,” “save this token,” or “exit.” Without this file, a new user would have no hosted path from “I ran the installer” to “I am signed into my company workspace.”

The main flow lives in the Onboarding class. It treats onboarding like a short conversation. First it asks for a work email. Then it sends and checks a code. After the email is verified, it either lets the user into an existing workspace or creates/ensures one for that email domain. If invites are required, it also checks whether that domain has a valid invite before opening a new workspace. At the end, it mints a signed token, returns the workspace URL, and chooses the next prompt.

The GatewayState class holds the live database connections and onboarding machinery. The gateway_app function builds the web app, sets up startup and shutdown work, and exposes routes for health checks, the installer script, login HTML, basic fleet stats, and onboarding. The file is careful about safety: required environment variables must be present, request sizes are capped, and startup fails early if the database schema or configuration is wrong.

#### Function details

##### `_stamp_script`  (lines 66–70)

```
def _stamp_script(text: str) -> str
```

**Purpose**: Prepares the downloadable client installer script before the server starts serving it. It gives the script a content-based version and fills in the public UFO base URL for this deployment.

**Data flow**: It receives the raw script text. It hashes the text to make a short version label, reads the public base URL from the environment or uses the default, replaces the development version marker and default URL line, and returns the stamped script text.

**Call relations**: This runs when the module is loaded to create STAMPED_SCRIPT. Later, the serve_script route simply returns that already-prepared script, so each request does not need to recompute the hash or reread configuration.

*Call graph*: 1 external calls (sha1).


##### `Onboarding.advance`  (lines 86–92)

```
async def advance(self, channel: str, session: str, body: str, install: bytes) -> bytes
```

**Purpose**: Moves one onboarding conversation forward by one step. It decides whether the user needs to enter an email, enter a verification code, or be signed into a workspace.

**Data flow**: It receives a channel name, a session id, the user’s latest text, and any installer output that should be included in replies. It looks up the live claim for that channel and session. If there is no claim, it starts email collection; if the claim is not verified, it checks the code; if the claim is verified, it resolves the workspace and returns the next client instructions as bytes.

**Call relations**: The web and terminal onboarding routes call this after validating the request. It then hands the conversation to _collect_email, _verify_code, or _resolve, depending on what the stored claim says.

*Call graph*: calls 3 internal fn (_collect_email, _resolve, _verify_code).


##### `Onboarding._collect_email`  (lines 94–111)

```
async def _collect_email(self, channel: str, session: str, body: str, install: bytes) -> bytes
```

**Purpose**: Starts the onboarding conversation by asking for a work email, then begins the email-code claim process once the user provides one.

**Data flow**: It receives the channel, session, user text, and installer bytes. If the user sent no text, it returns directives that introduce UFO and ask for a work email. If text is present, it tries to start a claim using that email. On bad email, policy failure, or claim error, it returns the error and asks again. On success, it returns a message saying a code was emailed and asks for the code.

**Call relations**: Onboarding.advance calls this when there is no existing live claim for the session. It uses the claim workflow to create the email challenge and uses directive rendering to turn the result into client-readable instructions.

*Call graph*: called by 1 (advance); 2 external calls (directive, render).


##### `Onboarding._verify_code`  (lines 113–120)

```
async def _verify_code(self, claim: OnboardClaim, body: str, install: bytes) -> bytes
```

**Purpose**: Checks the code the user typed after receiving an onboarding email. If the code is accepted, it immediately continues toward workspace sign-in.

**Data flow**: It receives the stored claim, the user’s submitted code, and installer bytes. It asks the claim workflow to verify the code. If verification fails, it returns a message explaining the problem and asks for the code again. If verification succeeds, it passes the claim onward to workspace resolution and returns that result.

**Call relations**: Onboarding.advance calls this when a claim exists but has not yet been verified. It is the bridge between proving email ownership and _resolve, which opens or finds the workspace.

*Call graph*: calls 1 internal fn (_resolve); called by 1 (advance); 2 external calls (directive, render).


##### `Onboarding._resolve`  (lines 122–129)

```
async def _resolve(self, claim: OnboardClaim, install: bytes) -> bytes
```

**Purpose**: Turns a verified email claim into an actual signed-in workspace session. It is where invite rules, workspace creation or lookup, completion of the claim, and final sign-in come together.

**Data flow**: It receives a verified claim and installer bytes. If invite gating is enabled and no workspace exists yet for the email domain, it checks the invite gate and may return a refusal screen. Otherwise it ensures the shared workspace exists, records the claim as complete with that workspace id, and returns the signed-in directives.

**Call relations**: This is called after verification, and also directly by advance when the stored claim is already verified. It may hand off to _invite_gate for invite decisions, then to _signed_in to produce the final client response.

*Call graph*: calls 2 internal fn (_invite_gate, _signed_in); called by 2 (_verify_code, advance).


##### `Onboarding._invite_gate`  (lines 131–168)

```
async def _invite_gate(self, claim: OnboardClaim, install: bytes) -> bytes | None
```

**Purpose**: Decides whether a verified email domain is allowed to open a new workspace when invites are required. It returns a clear stop message when the domain has no usable invite.

**Data flow**: It receives a verified claim and installer bytes. If the claim already has an invite id, it allows the flow to continue. Otherwise it tries to redeem an invite for the email domain. Accepted invites produce no refusal. Expired, already-used, or missing invites produce rendered messages explaining what happened and telling the client to exit successfully.

**Call relations**: _resolve calls this only when a new workspace would be opened under invite-required rules. It does not ask the user for more input, because the user’s verified email domain is the only fact needed to decide.

*Call graph*: called by 1 (_resolve); 2 external calls (directive, render).


##### `Onboarding._signed_in`  (lines 170–195)

```
def _signed_in(self, claim: OnboardClaim, ensured: EnsuredWorkspace, install: bytes) -> bytes
```

**Purpose**: Builds the final successful onboarding response. It gives the client a login token, the workspace URL, and the first prompt or choice the user should see.

**Data flow**: It receives the verified claim, the ensured workspace result, and installer bytes. It creates a signed token for the user and workspace, checks whether the user belongs to the operator email domain, and renders directives for token, workspace, optional debugger URL, sign-in message, and the next interaction. Workspace admins in the terminal get a billing-or-tour choice; others get the normal prompt.

**Call relations**: _resolve calls this after the workspace is ready and the claim is marked complete. It hands the finished instructions back up to the onboarding route, which sends them to the terminal client or web client.

*Call graph*: called by 1 (_resolve); 3 external calls (directive, render, mint_token).


##### `GatewayState.healthy`  (lines 205–213)

```
async def healthy(self) -> bool
```

**Purpose**: Checks whether the gateway is connected to the database in the roles it expects. This supports the health endpoint used by deployment systems.

**Data flow**: It reads the current database user from the owner connection pool and from a workspace transaction. If either query fails, it logs the failure and returns false. If both succeed, it compares them with the expected owner and serving roles and returns true only when both match.

**Call relations**: The healthz route calls this during health checks. It uses the same database paths the app relies on, so a positive health result means the gateway’s important database identities are in place.

*Call graph*: 2 external calls (text, workspace_tx).


##### `_require_env`  (lines 216–220)

```
def _require_env(name: str) -> str
```

**Purpose**: Reads a required environment variable and fails loudly if it is missing. This prevents the server from starting with an incomplete setup.

**Data flow**: It receives the name of an environment variable. It looks up the value. If the value is empty or absent, it raises a runtime error explaining that the gateway needs it; otherwise it returns the value.

**Call relations**: The startup lifespan uses this for required settings such as the workspace base URL and token secret. That means configuration mistakes are caught at startup rather than during a user’s onboarding attempt.

*Call graph*: called by 1 (lifespan).


##### `_invite_required`  (lines 223–232)

```
def _invite_required() -> bool
```

**Purpose**: Decides whether new workspace signup must be protected by invites. It defaults to requiring invites, so forgetting the setting does not accidentally open public signup.

**Data flow**: It reads the invite-required environment variable. Missing values are treated as true. Recognized true or false strings become a boolean. Any other value raises an error, so unclear configuration does not silently choose a behavior.

**Call relations**: The startup lifespan calls this while assembling the Onboarding object. The result later controls whether _resolve asks _invite_gate to approve new workspace creation.

*Call graph*: called by 1 (lifespan).


##### `_dsn_role`  (lines 235–239)

```
def _dsn_role(dsn: str) -> str
```

**Purpose**: Extracts the database role name from a database connection string. The gateway uses this to remember which database users it expects to see during health checks.

**Data flow**: It receives a database DSN, meaning a database address string that includes login information. It parses the string and returns the username portion. If no username is present, it raises an error because the health check would not know what role to expect.

**Call relations**: The startup lifespan calls this for both the owner database connection and serving database connection. GatewayState.healthy later compares live database roles against these saved values.

*Call graph*: called by 1 (lifespan).


##### `_request_body`  (lines 242–248)

```
async def _request_body(request: Request) -> str
```

**Purpose**: Reads a request body safely and turns it into trimmed text. It protects the onboarding endpoints from very large inputs.

**Data flow**: It receives a FastAPI request. It streams the body in chunks, keeping only up to the configured maximum plus one byte. If the body grows too large, it raises a request input error. Otherwise it decodes the bytes as UTF-8, replacing invalid characters, trims surrounding whitespace, and returns the text.

**Call relations**: Both onboard_web and onboard call this after checking headers and input sizes. If it raises a size error, those routes turn the problem into a friendly client directive instead of crashing the request.

*Call graph*: called by 2 (onboard, onboard_web); 2 external calls (__init__, stream).


##### `gateway_app`  (lines 251–379)

```
def gateway_app() -> FastAPI
```

**Purpose**: Builds the FastAPI web application for the gateway. It wires together startup setup, shutdown cleanup, health checks, installer serving, login, fleet stats, and onboarding routes.

**Data flow**: It starts with no live state. It defines a lifespan routine that will create the state at server startup, then defines HTTP routes that use that state during requests, and finally returns the configured FastAPI app object.

**Call relations**: The module calls this at the bottom to create the exported app used by the web server. The nested route functions and lifespan function are the actual moving parts that run during startup and requests.

*Call graph*: 1 external calls (FastAPI).


##### `gateway_app.lifespan`  (lines 255–307)

```
async def lifespan(app: FastAPI)
```

**Purpose**: Performs the gateway’s startup and shutdown work. It makes sure configuration and database schema are ready before the server accepts onboarding traffic.

**Data flow**: On startup, it reads database connection strings and required environment settings, verifies the control schema, opens the owner database pool, creates the store, invite, claim, workspace, and onboarding objects, initializes the serving database path, and optionally starts a Slack Connect invitation background task. It yields control while the app runs. On shutdown, it cancels the background task if present, clears state, disposes shared database resources, and closes the pool.

**Call relations**: FastAPI calls this around the lifetime of the web app. All request routes depend on the state it creates; cleanup happens here so database connections and background work do not leak after shutdown.

*Call graph*: calls 3 internal fn (_dsn_role, _invite_required, _require_env); 18 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, create_task, gather, create_pool (+8 more)).


##### `gateway_app.healthz`  (lines 312–315)

```
async def healthz() -> Response
```

**Purpose**: Answers whether the gateway is currently healthy enough to serve. It is meant for load balancers or deployment checks.

**Data flow**: It reads the shared state. If there is no state or the state’s database role check fails, it returns a JSON response with status unavailable and HTTP 503. If the check passes, it returns a JSON response with status ok.

**Call relations**: This route relies on GatewayState.healthy for the real check. It turns that internal result into a simple HTTP response that outside systems can understand.

*Call graph*: 1 external calls (JSONResponse).


##### `gateway_app.serve_script`  (lines 318–319)

```
async def serve_script() -> Response
```

**Purpose**: Serves the UFO installer/client script. This is what a user can download or run to start the terminal onboarding flow.

**Data flow**: It uses the already-stamped script text prepared at import time and returns it as plain text with a shell script media type. It does not read the database or inspect the request.

**Call relations**: This route depends on _stamp_script having already prepared STAMPED_SCRIPT. It is separate from onboarding itself: it gives users the client that will later call the onboarding routes.

*Call graph*: 1 external calls (PlainTextResponse).


##### `gateway_app.fleet`  (lines 322–325)

```
async def fleet() -> Response
```

**Purpose**: Returns a simple count of workspaces known to the gateway. The response names the count as craft.

**Data flow**: It reads the active gateway state, queries the owner database pool for the number of workspace rows, and returns that count in JSON.

**Call relations**: This route uses the database pool created during lifespan startup. It is a small reporting endpoint alongside the main onboarding routes.

*Call graph*: 1 external calls (JSONResponse).


##### `gateway_app.login`  (lines 328–329)

```
async def login() -> Response
```

**Purpose**: Serves the web login page. This gives browser users the HTML shell used to begin or continue web onboarding.

**Data flow**: It takes no request data beyond the HTTP request itself. It returns the prebuilt LOGIN_PAGE HTML as an HTML response.

**Call relations**: This route is the browser-facing companion to the web onboarding API. The page it serves can call onboard_web to advance the sign-in conversation.

*Call graph*: 1 external calls (HTMLResponse).


##### `gateway_app.onboard_web`  (lines 332–347)

```
async def onboard_web(request: Request) -> Response
```

**Purpose**: Processes one step of onboarding for the web client and returns structured JSON directives. It is the browser-friendly version of the onboarding endpoint.

**Data flow**: It reads the x-ufo-session header, rejects the request if the session is missing, checks the session length, reads the text body safely, and asks Onboarding.advance to continue the web-channel conversation. Input errors become rendered error directives. Unexpected failures are logged and become a generic onboarding failed response. The final directive bytes are parsed into JSON before returning.

**Call relations**: The login page or other web client calls this route during sign-in. It uses _request_body for safe input reading and Onboarding.advance for the actual onboarding decision, then converts the directive format into JSON for the browser.

*Call graph*: calls 1 internal fn (_request_body); 5 external calls (__init__, JSONResponse, directive, render, parse_directives).


##### `gateway_app.onboard`  (lines 350–377)

```
async def onboard(channel: str, request: Request) -> Response
```

**Purpose**: Processes one step of onboarding for non-web clients, such as the terminal installer. It returns plain-text directives that the client script can read.

**Data flow**: It reads the channel path value, the x-ufo-session header, and first-run installer information from headers. If the session is missing, it returns directives saying the header is required and exits. Otherwise it checks channel and session sizes, reads the body safely, and advances onboarding. Input errors and unexpected failures are turned into plain-text directive responses, with unexpected failures logged.

**Call relations**: The downloaded client script calls this route as the user answers prompts. It uses first_run_install so the server can include installation-related instructions, then delegates the conversation itself to Onboarding.advance.

*Call graph*: calls 1 internal fn (_request_body); 5 external calls (__init__, PlainTextResponse, directive, first_run_install, render).


### `control/src/ufo_control/gateway_directives.py`

`io_transport` · `request handling`

The UFO terminal client appears to receive simple line-based commands from the server. This file defines that tiny command format. Think of it like writing instructions on slips of paper: each slip starts with an action word, followed by optional fields, separated in a predictable way so the client can read it back safely.

The main helper, `directive`, turns a command verb and its text fields into bytes ready to send over the wire. It carefully escapes characters that would confuse the format, such as tabs and newlines, so a field stays one field instead of accidentally becoming a new command. `render` then joins several already-built byte lines into one response body.

The file also contains a small header lookup helper, `header_value`, because HTTP-style headers are meant to be matched without caring about letter case. That supports `first_run_install`, which decides whether to prepend an `install` directive. On a fresh `curl | sh` run, the client has not yet installed the UFO binary, so the server sends an install command first. Once the shell reports `x-ufo-installed: 1`, that extra instruction stops being sent. Without this file, the server and terminal client would lack a shared, safe way to express these server-driven instructions.

#### Function details

##### `directive`  (lines 8–13)

```
def directive(verb: str, *fields: str) -> bytes
```

**Purpose**: Builds one client instruction in the server-to-terminal wire format. It makes sure text fields cannot accidentally break the command layout by escaping special characters first.

**Data flow**: It receives a command word, called the verb, plus any number of text fields. It rewrites backslashes, tabs, and newlines into safe escaped forms, removes carriage returns, joins everything with tab characters, adds a final newline, and returns the finished instruction as bytes.

**Call relations**: When `first_run_install` needs to tell the client to install itself, it calls `directive` with the `install` verb. More generally, this is the low-level formatter other response-building code can use before sending directives to the terminal client.

*Call graph*: called by 1 (first_run_install).


##### `render`  (lines 16–17)

```
def render(*lines: bytes) -> bytes
```

**Purpose**: Combines several already-built directive byte strings into one byte response. It is useful when a screen or response is made from multiple instruction lines.

**Data flow**: It receives any number of byte chunks. It joins them in order without adding anything extra, and returns the combined bytes.

**Call relations**: This function sits at the final assembly step: after individual directives have been created elsewhere, `render` can stitch them together into the exact byte stream the client should receive.


##### `header_value`  (lines 20–25)

```
def header_value(headers: Mapping[str, str], name: str) -> str | None
```

**Purpose**: Looks up a header value without caring about uppercase or lowercase spelling. This matters because HTTP-style header names are case-insensitive, so `X-UFO-Installed` and `x-ufo-installed` should mean the same thing.

**Data flow**: It receives a mapping of header names to values and the header name to find. It compares each stored header name in lowercase form against the requested lowercase name, returns the matching value if found, or returns `None` if no match exists.

**Call relations**: `first_run_install` uses this helper to check whether the client says it is already installed. By hiding the case-insensitive lookup here, the install decision can stay simple and reliable.

*Call graph*: called by 1 (first_run_install).


##### `first_run_install`  (lines 28–35)

```
def first_run_install(headers: Mapping[str, str]) -> bytes
```

**Purpose**: Decides whether the server should send an initial `install` directive to a newly started shell session. It prevents repeated install prompts once the client reports that the UFO binary already exists.

**Data flow**: It receives request headers from the client. It reads the `x-ufo-installed` header using `header_value`; if the value is exactly `1`, it returns empty bytes, meaning no install command is needed. Otherwise, it creates and returns an `install` directive as bytes.

**Call relations**: This function is used at the start of rendering a session for a client. It calls `header_value` to learn the client’s install state, then calls `directive` only when it must prepend the install instruction to the outgoing screen.

*Call graph*: calls 2 internal fn (directive, header_value).


### `control/src/ufo_control/gateway_web.py`

`io_transport` · `request handling`

This file is the web “front desk” for signing in. The important idea is that the browser does not run a separate login process. Instead, it talks to the same onboarding state machine as the terminal client, using a generated browser session id to keep its place in the conversation. That matters because email prompts, verification codes, issued tokens, workspace links, and failure messages all come from one shared source of truth.

The Python side has a small parser for “directive lines.” A directive is a simple instruction such as say something, ask the user a question, provide a token, or provide a workspace URL. These arrive as tab-separated text lines, with special escaping so tabs and newlines inside a field are not mistaken for separators. The parser turns those lines into JSON-friendly dictionaries for the web response.

Most of the file is the self-contained login page: HTML, styling, and browser JavaScript. The page starts a random session, posts answers to `/v1/onboard/web`, displays `say` messages as a transcript, shows `ask` prompts as input fields, and when it receives both a token and workspace URL, it switches to a signed-in card. That card can post the token to the workspace without putting the token in the URL, which is safer because URLs are often logged or shared.

#### Function details

##### `parse_directives`  (lines 15–24)

```
def parse_directives(payload: bytes) -> list[dict[str, object]]
```

**Purpose**: This function turns the onboarding machine's line-based instructions into plain Python dictionaries that can be sent as JSON to the browser. It preserves real tabs and newlines inside fields, instead of confusing them with the line format.

**Data flow**: It receives raw bytes from the onboarding process. It decodes them into text, reads each non-empty line, splits the line into a command word and its tab-separated fields, unescapes each field, and returns a list like `{ "verb": "ask", "fields": [...] }` for the web page to consume.

**Call relations**: When the web endpoint needs to answer the browser, it uses this parser to translate the onboarding machine's text output into browser-friendly data. For each field it finds, it hands the field to `_unescape` so that protected characters are restored before the JSON is returned.

*Call graph*: calls 1 internal fn (_unescape).


##### `_unescape`  (lines 27–40)

```
def _unescape(field: str) -> str
```

**Purpose**: This helper restores special characters that were hidden so directive lines could be safely split apart. For example, it turns the two-character sequence `\n` back into an actual newline.

**Data flow**: It receives one already-split text field. It walks through the field character by character, replacing known escape sequences such as `\\`, `\t`, and `\n` with their real characters, while leaving unknown or ordinary characters unchanged. It returns the cleaned-up string.

**Call relations**: This is the small repair tool used by `parse_directives` after that function has split a directive line into fields. It does not drive the flow itself; it simply makes each field truthful again before the web layer sends it onward.

*Call graph*: called by 1 (parse_directives).


### Verification and invitations
Email claims, one-time invites, mail delivery, and claim persistence prove that a user may join or create a workspace.

### `control/src/ufo_control/gateway_claim.py`

`domain_logic` · `onboarding request handling`

This file protects onboarding so that someone can only claim access through a valid work email address. Without it, the system would have no reliable way to prove that a person controls an approved company email, and failed or expired codes might linger around forever.

The main idea is like a coat-check ticket: the system gives the user a temporary number by email, keeps a protected copy of that number, and later asks the user to show the same number back. The real code is never stored directly. Instead, hash_code turns it into a one-way fingerprint using SHA-256, so the stored value can be checked later without keeping the secret itself.

ClaimWorkflow.start begins the process. It checks whether the email is allowed by the work-email policy, creates a random 6-digit code, builds an OnboardClaim record with an expiry time, saves it, and sends the email. If sending fails, it removes the saved claim so there is no dead pending claim left behind.

ClaimWorkflow.verify finishes the process. It rejects claims that have too many failed attempts or are past their time limit, records each attempt before checking the code, compares hashes in a timing-safe way, and marks the claim verified if the code is right.

#### Function details

##### `hash_code`  (lines 27–28)

```
def hash_code(code: str) -> str
```

**Purpose**: This turns a verification code into a fixed fingerprint using SHA-256, a one-way hashing method. It lets the system compare codes later without storing the actual email code.

**Data flow**: It receives the plain text code as a string. It encodes that text, runs it through SHA-256, and returns the resulting hexadecimal fingerprint. It does not change any stored data by itself.

**Call relations**: ClaimWorkflow.start calls this when saving a new claim, so only the fingerprint goes into storage. ClaimWorkflow.verify calls it again on the code the user typed, then compares the new fingerprint with the saved one.

*Call graph*: called by 2 (start, verify); 1 external calls (sha256).


##### `ClaimWorkflow.start`  (lines 39–62)

```
async def start(self, email: str, surface: str, surface_ref: str) -> str
```

**Purpose**: This begins an email verification claim for onboarding. It checks that the email is acceptable, creates a temporary code, saves the claim, and sends the code to the user.

**Data flow**: It receives an email address plus information about where the onboarding request came from, called the surface and surface reference. It validates the email domain, generates a random 6-digit code, stores a claim containing the normalized email, code hash, expiry time, and attempt count, then sends a verification email. If the email cannot be sent, it deletes the claim and raises a ClaimError; if all goes well, it returns the validated email domain.

**Call relations**: This is the opening step of the claim flow. It uses hash_code to avoid storing the raw code, builds an OnboardClaim record, asks verification_email to create the message text, and relies on the configured store and email sender to save the claim and deliver the message. Later, ClaimWorkflow.verify uses the saved claim created here.

*Call graph*: calls 1 internal fn (hash_code); 6 external calls (__init__, __init__, now, randbelow, verification_email, uuid4).


##### `ClaimWorkflow.verify`  (lines 64–74)

```
async def verify(self, claim: OnboardClaim, code: str) -> None
```

**Purpose**: This checks whether a submitted verification code proves the user controls the email address. It also enforces the safety rules: the code must not be expired, and the user only gets a limited number of tries.

**Data flow**: It receives an existing claim record and the code entered by the user. First it checks whether the claim has already used too many attempts or has expired; in either case it deletes the claim and raises a ClaimError. Otherwise it records one more attempt, hashes the submitted code, compares that hash with the stored hash, and marks the claim verified if they match. If the code is wrong, it raises a ClaimError and leaves the claim available until the attempt limit or expiry is reached.

**Call relations**: This is the closing step after ClaimWorkflow.start has created and emailed a claim. It uses hash_code to make the user's entered code comparable with the stored fingerprint, and hmac.compare_digest for a safer comparison that does not reveal useful timing clues. It hands the final state back to the store by recording attempts, deleting unusable claims, or marking successful claims as verified.

*Call graph*: calls 1 internal fn (hash_code); 3 external calls (__init__, now, compare_digest).


### `control/src/ufo_control/gateway_invite.py`

`domain_logic` · `admin invite creation and workspace signup`

This file solves a gatekeeping problem: not every verified email should be able to open a new workspace, but approved waitlist entries need a safe way in. Instead of sending a secret code that someone must copy and paste, the system grants an invitation to an email domain, such as a company domain. A person proves they belong to that domain by verifying their work email.

The main class, InviteCodes, talks to a PostgreSQL database through asyncpg, an asynchronous database library. It can create an invite, called “minting,” and later redeem it when someone from the invited domain starts workspace creation.

The database table records the invite ID, waitlist object number, email address, email domain, expiry time, and whether it has been consumed. Two unique database indexes prevent two live invites from existing for the same waitlist object or domain. Think of it like a ticket desk that will not print a second valid ticket for the same seat.

The important safety feature is redemption. When an invite is redeemed, the row is locked in the database transaction before it is marked consumed. That lock is what stops two simultaneous signup attempts from both using the same invite. In the same transaction, the invite is attached to the workspace claim, so the system does not end up with a used invite that is not connected to the claim that used it.

#### Function details

##### `InviteCodes.mint`  (lines 92–126)

```
async def mint(self, object_number: int, email: str) -> MintedInvite
```

**Purpose**: Creates a new live invite for a waitlist object and a work email domain. It refuses to create one if that object or domain is already known to the system or already has a still-valid invite.

**Data flow**: It receives a waitlist object number and an email address. It normalizes the email, checks that it is acceptable as a work email, calculates an expiry time, then opens a database transaction. Inside that transaction it checks for existing consumed or live invites, removes any expired live invite for the same object or domain, inserts a new invite row with a fresh unique ID, and returns a MintedInvite containing the object number, normalized email, and expiry time. If another process creates a conflicting invite at the same time, the database uniqueness check catches it and this function raises InviteError.

**Call relations**: This is the public creation path for invites, such as when an operator runs the invite command. During its checks it calls InviteCodes._refuse_standing twice: once to check the waitlist object and once to check the email domain. It also relies on email normalization and work-email validation before anything is written, so bad or unsuitable addresses are rejected before the database ledger changes.

*Call graph*: calls 1 internal fn (_refuse_standing); 6 external calls (__init__, __init__, __init__, now, normalize_email, uuid4).


##### `InviteCodes._refuse_standing`  (lines 128–142)

```
async def _refuse_standing(self, connection: asyncpg.Connection, column: str, value: object, subject: str, now: datetime) -> None
```

**Purpose**: Checks whether a particular object number or email domain already has a meaningful invite record. It blocks minting when the subject is already identified or already has a live invite.

**Data flow**: It receives an open database connection, the column to search, the value to look for, a human-readable subject name, and the current time. It queries the invite table for a matching record that is either already consumed or not yet expired. If nothing is found, it returns quietly. If the record was consumed, it raises InviteError saying the subject is already identified. If the record is still live, it raises InviteError with the invite expiry time.

**Call relations**: InviteCodes.mint calls this helper before inserting a new invite. It is the guard at the door: mint asks it, first for the object and then for the domain, whether issuing another invite would duplicate something the ledger already knows.

*Call graph*: called by 1 (mint); 2 external calls (__init__, fetchrow).


##### `InviteCodes.redeem`  (lines 144–175)

```
async def redeem(self, email_domain: str, claim_id: UUID) -> InviteUnknown | InviteExpired | InviteConsumed | InviteAccepted
```

**Purpose**: Attempts to use an invite for an email domain during workspace creation. It returns a clear outcome: no invite exists, the invite expired, it was already used, or it was accepted.

**Data flow**: It receives an email domain and a workspace claim ID. It opens a database transaction and looks up the most relevant invite for that domain while locking the row, which prevents another concurrent redemption from changing it at the same time. If no row exists, it returns InviteUnknown. If the invite was already consumed, it returns InviteConsumed. If the expiry time has passed, it returns InviteExpired with the expiry time. Otherwise it marks the invite consumed, writes the invite ID onto the workspace claim row, and returns InviteAccepted with the invite ID, object number, and consumption time.

**Call relations**: This is used by the workspace-creation flow after the user has proved control of their email domain. It does not call the minting checks, because redemption has a different job: take the existing grant, lock it, consume it once, and attach it to the claim in the same transaction so later retry behavior can be safe and predictable.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, now).


### `control/src/ufo_control/gateway_email.py`

`io_transport` · `signup and invite email sending`

This file is the email front desk for the control service. First, it checks that an address is shaped like an email address and rejects common personal or throwaway domains, so a workspace is tied to an organization rather than to a free mailbox. Then it creates simple text emails: one for verification codes and one for invites.

For delivery, the file offers two senders. In real deployments, `SesEmailSender` talks directly to Amazon SES, Amazon's email-sending service. It first exchanges the pod's web identity token for short-lived AWS credentials, then signs the SES request using SigV4, Amazon's request-signing method that proves the caller is allowed to send the email. All network calls are asynchronous, so the service is not blocked while waiting for AWS.

For local development, `ConsoleEmailSender` writes the same email to the process log instead of sending it. This is like putting a letter in a visible outbox rather than mailing it. `email_sender_from_env` chooses between these modes from environment variables and fails loudly if required settings are missing, which prevents silent mail loss in production.

#### Function details

##### `normalize_email`  (lines 119–125)

```
def normalize_email(email: str) -> tuple[str, str]
```

**Purpose**: Cleans up an email address by trimming spaces, making it lowercase, and extracting its domain. It rejects text that is not shaped like a normal address before the rest of the system trusts it.

**Data flow**: It takes a raw email string. It strips surrounding whitespace, lowercases it, and checks it against the email pattern. If the address is valid, it returns the cleaned address and its domain; if not, it raises a work-email error.

**Call relations**: The work-email policy uses this before checking the denylist, and the invite-email builder uses it to discover the invite's domain. It is the shared first gate for email text.

*Call graph*: called by 2 (validate, invite_email); 1 external calls (__init__).


##### `WorkEmailPolicy.validate`  (lines 132–136)

```
def validate(self, email: str) -> str
```

**Purpose**: Checks whether an email belongs to an acceptable work domain. It blocks known free and disposable email providers so workspace access is connected to an organization.

**Data flow**: It receives an email address, asks `normalize_email` for the cleaned domain, and compares that domain with the denylist. It returns the domain when allowed, or raises an error when the domain is blocked.

**Call relations**: This is called when the service needs to decide whether a user-provided address can be treated as a work email. It relies on `normalize_email` so malformed addresses are rejected before domain rules are applied.

*Call graph*: calls 1 internal fn (normalize_email); 1 external calls (__init__).


##### `public_apex_host`  (lines 139–147)

```
def public_apex_host() -> str
```

**Purpose**: Finds the public website host used in invite instructions. This lets emails point users at the correct front door for the service.

**Data flow**: It reads the public base URL from the environment, or uses the default public URL if none is set. It strips the scheme and path, returns only the host name, and raises an error if the setting is not a proper base URL.

**Call relations**: Invite creation can use this host when building the install command shown to a user. It depends on URL parsing to turn a full URL into the plain host needed in the message.

*Call graph*: 1 external calls (urlsplit).


##### `verification_email`  (lines 150–156)

```
def verification_email(code: str, expires_at: datetime, ttl: timedelta) -> tuple[str, str]
```

**Purpose**: Builds the subject and plain-text body for a verification-code email. It keeps the wording of these short-lived code messages in one place.

**Data flow**: It receives a code, an expiry time, and a time-to-live duration. It formats the expiry time and duration into a human-readable message, then returns the subject and body text.

**Call relations**: Signup or sign-in flows can call this before handing the result to an `EmailSender`. It does not send anything itself; it only prepares the text to be sent.

*Call graph*: 2 external calls (strftime, total_seconds).


##### `invite_email`  (lines 159–169)

```
def invite_email(email: str, expires_at: datetime, apex_host: str) -> tuple[str, str]
```

**Purpose**: Builds the subject and plain-text body for an invite email. The message tells the recipient how to install and sign in, and names the email domain covered by the invite.

**Data flow**: It takes an email address, an expiry time, and the public host name. It normalizes the email to get the domain, formats the expiry time in UTC, and returns the invite subject and body.

**Call relations**: The invite command or invite flow can call this before sending mail. It uses `normalize_email` so the domain named in the invite comes from a valid address.

*Call graph*: calls 1 internal fn (normalize_email); 1 external calls (astimezone).


##### `EmailSender.send`  (lines 173–173)

```
async def send(self, email: str, subject: str, text: str) -> None
```

**Purpose**: Defines the common promise that all email senders must keep: given a recipient, subject, and text body, send the message somehow. It is a protocol, meaning other classes can fit this shape without inheriting behavior.

**Data flow**: It describes inputs only: email address, subject, and body text. Implementations decide what happens next, such as delivering through SES or logging locally, and they return nothing when the send step is complete.

**Call relations**: Higher-level code can depend on this shape instead of knowing which sender is being used. `SesEmailSender.send` and `ConsoleEmailSender.send` are the concrete versions of this operation.


##### `SesEmailSender.send`  (lines 196–217)

```
async def send(self, email: str, subject: str, text: str) -> None
```

**Purpose**: Sends one email through Amazon SES, the real outbound email service used in deployment. It turns the prepared subject and body into an SES request and proves permission by signing the request.

**Data flow**: It receives the recipient address, subject, and text body. It first gets temporary AWS credentials, builds the SES JSON request, signs it, posts it to the SES endpoint, and raises an error if SES reports failure. On success, nothing is returned.

**Call relations**: This is the real implementation of the `EmailSender` idea. It calls `_assume_role` to get credentials, `_sigv4_headers` to sign the request, and then uses an asynchronous HTTP client to hand the message to SES.

*Call graph*: calls 2 internal fn (_assume_role, _sigv4_headers); 3 external calls (now, AsyncClient, dumps).


##### `SesEmailSender._assume_role`  (lines 219–238)

```
async def _assume_role(self) -> SesCredentials
```

**Purpose**: Gets short-lived AWS credentials from STS, Amazon's security-token service, using the pod's web identity token. This avoids storing long-lived AWS keys in the service.

**Data flow**: It reads the token file configured on the sender, sends that token and role information to STS, checks for an error response, and parses the returned XML into credentials. It returns those temporary credentials for the email send.

**Call relations**: `SesEmailSender.send` calls this at the start of every SES delivery. After STS responds, this function hands the response text to `_parse_assume_role_credentials` so the signing step has usable keys.

*Call graph*: calls 1 internal fn (_parse_assume_role_credentials); called by 1 (send); 1 external calls (AsyncClient).


##### `_parse_assume_role_credentials`  (lines 241–254)

```
def _parse_assume_role_credentials(payload: str) -> SesCredentials
```

**Purpose**: Extracts the temporary AWS keys from the XML response returned by STS. It turns a document-shaped response into a small credentials object the sender can use.

**Data flow**: It receives XML text from STS, parses it, and looks for the access key, secret key, and session token. If any required value is missing, it raises an error; otherwise it returns `SesCredentials`.

**Call relations**: `SesEmailSender._assume_role` calls this after a successful STS request. It uses its inner `credential` helper to fetch each required field consistently.

*Call graph*: called by 1 (_assume_role); 2 external calls (__init__, fromstring).


##### `_parse_assume_role_credentials.credential`  (lines 244–248)

```
def credential(name: str) -> str
```

**Purpose**: Fetches one named credential value from the parsed STS XML. It makes sure missing AWS credential fields are caught immediately instead of causing a confusing failure later.

**Data flow**: It receives the name of a credential field, searches the parsed XML tree for that field, and returns the text value. If the value is absent or empty, it raises an error that names the missing field.

**Call relations**: This helper is used inside `_parse_assume_role_credentials` once for each required credential value. It keeps the XML lookup and error checking in one repeated pattern.


##### `_sigv4_headers`  (lines 257–290)

```
def _sigv4_headers(host: str, body: bytes, region: str, credentials: SesCredentials, now: datetime) -> dict[str, str]
```

**Purpose**: Creates the special HTTP headers Amazon requires for a signed SES request. SigV4 is AWS's way of proving that the request was made by someone holding valid credentials and that the request was not changed in transit.

**Data flow**: It takes the target host, request body, AWS region, temporary credentials, and current time. It hashes the body, builds AWS's canonical request text, derives a signing key, calculates the signature, and returns headers to attach to the HTTP request.

**Call relations**: `SesEmailSender.send` calls this just before posting to SES. It calls `_signing_key` to derive the secret bytes used for the final signature.

*Call graph*: calls 1 internal fn (_signing_key); called by 1 (send); 3 external calls (strftime, sha256, new).


##### `_signing_key`  (lines 293–297)

```
def _signing_key(secret_key: str, date_stamp: str, region: str) -> bytes
```

**Purpose**: Derives the AWS signing key used for one date, region, and service. This is a required step in AWS SigV4 signing.

**Data flow**: It starts with the AWS secret key, then repeatedly applies HMAC-SHA256, a standard keyed hashing method, using the date, region, SES service name, and final AWS marker. It returns the derived bytes used to sign the request.

**Call relations**: _sigv4_headers calls this while building the authorization header. This function does not know about emails directly; it supplies the cryptographic key needed by the SES request signer.

*Call graph*: called by 1 (_sigv4_headers); 1 external calls (new).


##### `ConsoleEmailSender.send`  (lines 307–308)

```
async def send(self, email: str, subject: str, text: str) -> None
```

**Purpose**: Pretends to send an email by writing it to the application log. This is useful for local development where a real SES account is not wanted.

**Data flow**: It receives the recipient, subject, and body text. Instead of contacting any mail service, it writes those values to the logger and returns nothing.

**Call relations**: `email_sender_from_env` chooses this sender when console mode is enabled. It provides the same `send` shape as `SesEmailSender`, so higher-level code can send mail without caring whether it goes to AWS or the log.


##### `email_sender_from_env`  (lines 311–325)

```
def email_sender_from_env() -> EmailSender
```

**Purpose**: Chooses and builds the email sender based on environment variables. It makes deployment behavior explicit: either real SES delivery or local console logging.

**Data flow**: It reads the email mode from the environment. In console mode, it returns a `ConsoleEmailSender`; in SES mode, it reads required SES and AWS identity settings and returns a configured `SesEmailSender`. If the mode is unknown or required settings are missing, it raises an error.

**Call relations**: Startup or setup code can call this once to get the sender used by signup and invite flows. It calls `_require_env` for settings that must be present before real email can be sent.

*Call graph*: calls 1 internal fn (_require_env); 3 external calls (__init__, __init__, Path).


##### `_require_env`  (lines 328–332)

```
def _require_env(name: str) -> str
```

**Purpose**: Reads a required environment variable and fails clearly if it is missing. This prevents the service from starting or sending mail with incomplete email configuration.

**Data flow**: It receives the environment variable name, looks up its value, and returns the value when it is present and non-empty. If the value is absent, it raises a runtime error naming the missing setting.

**Call relations**: `email_sender_from_env` uses this when building the SES sender. It is the small guardrail that turns misconfiguration into a clear error message.

*Call graph*: called by 1 (email_sender_from_env).


### `control/src/ufo_control/gateway_store.py`

`io_transport` · `request handling during onboarding`

This file is the database doorway for the hosted onboarding claim ledger. An onboarding claim is a temporary record saying, in effect, “this email address is trying to claim access through this surface,” where a surface might be some external entry point and the surface reference identifies the exact place or session. Without this file, the system would have no reliable memory of pending onboarding attempts, verification status, retry counts, or which workspace was eventually created.

The file defines the shape of the Postgres table and a unique index that allows only one active claim for the same surface and reference at a time. That matters because two unfinished onboarding flows for the same place could otherwise conflict.

The `OnboardClaim` data class is the in-memory version of one database row. The `OnboardStore` class is the practical tool used by the rest of the application. It opens a database connection from an async connection pool, inserts new claims, looks up active claims, records failed or repeated attempts, marks a claim as verified, marks it as completed with a resulting workspace, or deletes it.

A small helper, `_aware`, makes sure dates read from the database include timezone information. That avoids subtle bugs where one part of the system thinks a time is local and another thinks it is UTC.

#### Function details

##### `_aware`  (lines 46–49)

```
def _aware(value: datetime | None) -> datetime | None
```

**Purpose**: This helper makes a timestamp safe to compare and store by ensuring it has timezone information. If the timestamp is missing a timezone, it treats it as UTC, the standard reference time.

**Data flow**: It receives either a date-time value or nothing. If it receives nothing, it returns nothing. If it receives a date-time that already has timezone information, it returns it unchanged. If the date-time has no timezone, it returns a copy marked as UTC.

**Call relations**: When `OnboardStore.live_claim` reads date fields from Postgres, it calls `_aware` before building the in-memory `OnboardClaim`. This keeps expiry and verification times consistent for later onboarding checks.

*Call graph*: called by 1 (live_claim); 1 external calls (replace).


##### `OnboardStore.insert_claim`  (lines 56–70)

```
async def insert_claim(self, claim: OnboardClaim) -> None
```

**Purpose**: This saves a new onboarding claim in the database. It is used when the system starts a fresh onboarding attempt and needs a durable record of the email, verification code hash, surface, retry count, and expiry time.

**Data flow**: It receives an `OnboardClaim` object from the application. It opens a database connection from the pool and writes the claim’s main fields into the onboarding table. It does not return a value; the change is the new row stored in Postgres.

**Call relations**: This function is the entry point for creating a pending claim in the ledger. Later steps in the onboarding flow can find that same claim with `OnboardStore.live_claim`, update it after code attempts, or complete it after a workspace is created.


##### `OnboardStore.live_claim`  (lines 72–93)

```
async def live_claim(self, surface: str, surface_ref: str) -> OnboardClaim | None
```

**Purpose**: This looks up the currently active onboarding claim for a given surface and surface reference. It ignores claims that have already produced a workspace, because those are no longer active.

**Data flow**: It receives a surface name and surface reference. It asks Postgres for a matching row whose `resulting_workspace_id` is still empty. If no row exists, it returns `None`. If a row exists, it turns the database fields into an `OnboardClaim`, cleaning up timestamp timezone information along the way.

**Call relations**: Other onboarding code calls this when it needs to resume or validate a pending claim. Inside, it relies on `_aware` to normalize dates and then constructs an `OnboardClaim` object so the rest of the application can work with a plain Python value instead of a raw database row.

*Call graph*: calls 1 internal fn (_aware); 1 external calls (__init__).


##### `OnboardStore.record_attempt`  (lines 95–96)

```
async def record_attempt(self, claim_id: UUID, attempts: int) -> None
```

**Purpose**: This updates how many verification attempts have been made for a claim. It helps enforce retry limits or audit repeated code submissions.

**Data flow**: It receives a claim ID and the new attempt count. It passes an update instruction to the shared `_update` helper, which writes the new count into the database. Nothing is returned; the database row is changed.

**Call relations**: During onboarding, after someone submits or retries a verification code, higher-level code can call `record_attempt`. This function delegates the actual SQL update work to `OnboardStore._update` to avoid repeating the same database boilerplate.

*Call graph*: calls 1 internal fn (_update).


##### `OnboardStore.mark_verified`  (lines 98–99)

```
async def mark_verified(self, claim_id: UUID) -> None
```

**Purpose**: This records that a claim’s verification step succeeded. It stamps the claim with the current database time so later code can tell that the email/code proof was completed.

**Data flow**: It receives the claim ID. It asks `_update` to set `verified_at` to `now()` in Postgres. It returns nothing; the visible result is that the claim row now has a verification timestamp.

**Call relations**: This is called after the onboarding flow accepts the user’s proof, such as a correct code. It uses `OnboardStore._update` for the database write, then later parts of the flow can complete the claim or inspect its verified state.

*Call graph*: calls 1 internal fn (_update).


##### `OnboardStore.complete`  (lines 101–102)

```
async def complete(self, claim_id: UUID, resulting_workspace_id: str) -> None
```

**Purpose**: This marks an onboarding claim as finished by recording the workspace that resulted from it. Once this field is set, the claim is no longer considered active.

**Data flow**: It receives a claim ID and the workspace ID that was created or assigned. It passes those to `_update`, which stores the workspace ID in the database row. It returns nothing; the claim’s state changes from pending to completed.

**Call relations**: This is used near the end of the onboarding flow, after the system has produced a workspace. Because `OnboardStore.live_claim` only returns claims without a resulting workspace, completing a claim also removes it from future active-claim lookups.

*Call graph*: calls 1 internal fn (_update).


##### `OnboardStore.delete_claim`  (lines 104–106)

```
async def delete_claim(self, claim_id: UUID) -> None
```

**Purpose**: This removes an onboarding claim from the database entirely. It is useful when a claim must be cancelled, cleaned up, or discarded instead of completed.

**Data flow**: It receives a claim ID. It opens a database connection and runs a delete command for that row. It returns nothing; the row is gone if it existed.

**Call relations**: This function stands apart from the shared update helper because deletion is not a field change. Higher-level onboarding cleanup code can call it when a pending claim should no longer exist at all.


##### `OnboardStore._update`  (lines 108–112)

```
async def _update(self, assignment: str, claim_id: UUID, *values: object) -> None
```

**Purpose**: This is the shared private helper for simple updates to one onboarding claim row. It keeps the repeated pattern of opening a database connection and running an update in one place.

**Data flow**: It receives a piece of SQL describing which column to change, the claim ID to target, and any extra values needed for the change. It opens a connection, runs the update against the matching row, and returns nothing. The database row is changed if the ID matches an existing claim.

**Call relations**: `record_attempt`, `mark_verified`, and `complete` all call this helper when they need to change one field on a claim. It is not the high-level onboarding action itself; it is the common database-writing tool those actions share.

*Call graph*: called by 3 (complete, mark_verified, record_attempt).


### Workspace access provisioning
Verified domains are mapped to shared workspaces and issued compatible bearer tokens for hosted access.

### `control/src/ufo_control/gateway_shared.py`

`domain_logic` · `hosted onboarding / signup request handling`

This file solves a common onboarding problem: when someone signs up with an email like `alice@example.com`, the system needs to decide which shared workspace belongs to `example.com`. If the workspace already exists, the person should join it. If it does not, the system should create it, make the first person an administrator, and add the default agent so the workspace is usable right away.

The main class, `SharedWorkspaces`, is like a front desk for organization workspaces. Given a domain, it can check whether a workspace is already known. Given a domain and email address, it can make sure the right workspace exists and that the email address is a member of it.

The file uses a deterministic UUID, which means the same domain always produces the same workspace identifier. That helps avoid duplicate workspaces. It also has a safety check for older or alternate data: it can find a workspace by looking at the first member email domain too. If both methods point to more than one workspace, it raises an error instead of guessing.

The creation path is careful about race conditions, where two signups happen at the same time. It inserts the workspace if missing, locks the workspace row, checks whether this is the first member, creates the member, and inserts the default agent only if it is not already there.

#### Function details

##### `serve_dsn`  (lines 20–27)

```
def serve_dsn() -> str
```

**Purpose**: This function reads the database connection string used by hosted onboarding when it needs to write workspace data under the special serve role. It fails loudly if that setting is missing, because onboarding cannot safely create the workspace without it.

**Data flow**: It looks in the process environment for `UFO_CONTROL_SERVE_DSN`. If the value is present, it returns that string. If it is absent or empty, it raises a runtime error explaining which setting is missing and why it matters.

**Call relations**: This is a small setup helper for code that needs the hosted onboarding database connection. It does not call other project functions; it simply checks configuration before later onboarding code tries to write workspace rows.


##### `SharedWorkspaces.exists`  (lines 46–47)

```
async def exists(self, domain: str) -> bool
```

**Purpose**: This method answers the simple question: “Is there already a shared workspace for this verified domain?” It is useful when the caller only needs to know whether onboarding would join an existing workspace or create a new one.

**Data flow**: It receives a domain name, passes it to `_existing`, and checks whether `_existing` found a workspace ID. It returns `true` if one was found and `false` if not.

**Call relations**: This is the lightweight public check on `SharedWorkspaces`. It relies on `_existing` to do the actual database lookup and domain-to-workspace matching, then turns that result into a yes-or-no answer.

*Call graph*: calls 1 internal fn (_existing).


##### `SharedWorkspaces.ensure`  (lines 49–100)

```
async def ensure(self, domain: str, email: str) -> EnsuredWorkspace
```

**Purpose**: This method makes sure a person with a verified email address belongs to the shared workspace for their domain. If the workspace is new, it creates it, makes the first member an administrator, and adds the default agent.

**Data flow**: It receives a domain and an email address. First it looks for an existing workspace for the domain; if none exists, it creates a predictable workspace ID from the domain. It normalizes the email, opens a workspace-scoped database transaction, inserts the workspace if needed, locks it so simultaneous signups do not disagree, checks whether any member already exists, creates or updates the member, inserts the default agent if needed, then reads whether this member is an administrator. It returns an `EnsuredWorkspace` containing the workspace ID as text and the member’s admin status.

**Call relations**: This is the main onboarding action in the file. It first asks `_existing` whether there is already a workspace. It then uses database transaction helpers, SQL insert/select operations, the workspace context, and `create_member` to safely build or join the workspace. At the end it packages the result in `EnsuredWorkspace` so the caller can continue onboarding and, for administrators, offer billing or management options.

*Call graph*: calls 1 internal fn (_existing); 8 external calls (__init__, insert, select, workspace_tx, create_member, ws, uuid4, uuid5).


##### `SharedWorkspaces._existing`  (lines 102–120)

```
async def _existing(self, domain: str) -> UUID | None
```

**Purpose**: This private helper finds the workspace already associated with a domain, if there is one. It is deliberately strict: if the domain appears to point to more than one workspace, it raises an error rather than choosing the wrong one.

**Data flow**: It receives a domain, lowercases it, and creates the deterministic UUID that should belong to that domain. It then queries the database in two ways: first for a workspace with that deterministic ID, and second for a workspace whose first member has an email address ending in that domain. If it finds no rows, it returns `None`. If it finds one row, it returns that workspace ID. If it finds more than one, it raises a runtime error because the mapping is ambiguous.

**Call relations**: This is the lookup engine used by both public methods on `SharedWorkspaces`. `exists` uses it to answer yes or no. `ensure` uses it before deciding whether to join an existing workspace or create the deterministic one for the domain.

*Call graph*: called by 2 (ensure, exists); 1 external calls (uuid5).


### `control/src/ufo_control/gateway_token.py`

`domain_logic` · `authentication/token issuance`

This file is a small wrapper around the shared token-making code. Its job is to mint a gateway token for a member: a string the client can store in its local credentials file and later present as proof that it is allowed to use a workspace. Think of it like printing a temporary membership pass. The pass includes who it belongs to, which workspace it is for, and when it expires.

The file deliberately does not invent its own token format. Instead, it calls the shared `ufo.bearer.mint_token` function, which creates a signed bearer token. A bearer token is a credential where possession of the token is enough to be treated as authenticated, so the signature matters: it stops someone from editing the token contents without knowing the secret. HMAC is the signing method implied here; in plain terms, it is a tamper-evident seal made with a secret key.

Two constants define the local policy: tokens last 30 days, and the secret is expected to come from the `UFO_TOKEN_SECRET` environment variable. Keeping these details here makes the gateway token policy clear while keeping the low-level encoding and signing in one shared place.

#### Function details

##### `mint_token`  (lines 13–14)

```
def mint_token(secret: str, workspace_id: str, email: str, now: datetime | None=None) -> str
```

**Purpose**: Creates a signed gateway bearer token for one workspace member. It is used when the system needs to issue a credential that the client can store and later show to prove access.

**Data flow**: It receives a secret key, a workspace ID, an email address, and optionally the current time. It adds this file’s fixed 30-day lifetime, then passes all of that to the shared bearer-token maker. The result is a token string that contains the member claim and expiry in a signed form.

**Call relations**: This function is the gateway-specific front door for token creation. When called, it immediately hands the real token construction to `ufo.bearer.mint_token`, ensuring this side of the system uses the same signed shape that the verification side expects.

*Call graph*: 1 external calls (mint_token).


### First-run workspace setup
Core onboarding creates the initial workspace, admin user, assistant agent, and extension-provided setup safely once.

### `core/src/ufo/onboarding.py`

`orchestration` · `first-run startup`

This file is the system’s “move-in day” checklist. When someone runs the initial setup command, UFO needs a real workspace to exist, an admin user who can log in, and a default assistant agent ready to use. Without this file, a fresh install would have no durable home, no first user, and no main agent to talk to.

The flow is deliberately split into two parts. First, the core setup checks that the chosen model can be used, usually by making sure the required environment variable for the model key is present. It also checks whether extension setup steps need a credential store. Then it opens a database transaction, verifies that no member already exists, creates a workspace, creates the first admin member, and creates the main agent. If a member already exists, it raises `AlreadyInitialized` instead of creating a second “first” workspace.

After the core workspace exists, the file runs onboarding steps from installed extensions. Each extension gets its own scoped context, like giving each add-on a labeled toolbox containing only the credential slots it declared. If one extension’s step fails, the error is logged and the next extension can still run. This protects the core workspace from being stranded by an optional add-on failure.

#### Function details

##### `run_onboarding_steps`  (lines 46–74)

```
async def run_onboarding_steps(manifests: tuple[Manifest, ...], workspace_id: UUID, credentials: CredentialStore | None) -> None
```

**Purpose**: Runs the setup steps provided by installed extensions after the main workspace has already been created. It gives each extension its own context and makes sure one broken extension does not stop the others.

**Data flow**: It receives the installed extension manifests, the new workspace ID, and an optional credential store. It enters the workspace context, checks each manifest for onboarding steps, builds an extension-specific context when credentials are available, and calls each step. If a step fails, it records a log message and continues; it does not return data.

**Call relations**: After `Onboarding.run_steps` is asked to run add-on setup, it delegates here. This function uses `ws` to make the workspace current, `context_for` to build the extension’s scoped working context, and `log` to record skipped or failed extension setup without stopping the whole onboarding flow.

*Call graph*: called by 1 (run_steps); 3 external calls (context_for, log, ws).


##### `Onboarding.run`  (lines 89–92)

```
async def run(self) -> Onboarded
```

**Purpose**: Runs the complete first-time setup in the intended order: create the core workspace first, then run extension setup steps. This is the high-level method a caller uses when it wants the full onboarding flow.

**Data flow**: It starts with the `Onboarding` object’s stored configuration, email address, model name, credentials, and extension manifests. It calls `create` to build the durable core records, then passes the resulting workspace information to `run_steps`. It returns the `Onboarded` result containing the new workspace ID and member ID.

**Call relations**: This is the top of the file’s flow. It calls `Onboarding.create` first because the core workspace and admin must exist before extensions can safely do their own setup, then calls `Onboarding.run_steps` to let extensions add their optional first-run work.

*Call graph*: calls 2 internal fn (create, run_steps).


##### `Onboarding.create`  (lines 94–100)

```
async def create(self) -> Onboarded
```

**Purpose**: Creates the core first-run records, but only after checking that required keys and credentials are available. This keeps the system from ending up with a half-created workspace that cannot actually run.

**Data flow**: It reads the onboarding object’s model, configuration, credential store, manifests, and admin email. It first checks for the model key, then checks whether extension onboarding requires a credential key, then creates the workspace, admin member, and main agent in the database. It returns an `Onboarded` object with the new IDs.

**Call relations**: `Onboarding.run` calls this before any extension steps. Inside, it calls `_require_model_key`, `_require_credentials_for_steps`, and finally `_create_workspace`, so validation happens before the database is changed.

*Call graph*: calls 3 internal fn (_create_workspace, _require_credentials_for_steps, _require_model_key); called by 1 (run).


##### `Onboarding.run_steps`  (lines 102–103)

```
async def run_steps(self, onboarded: Onboarded) -> None
```

**Purpose**: Starts the extension portion of onboarding for a workspace that was just created. It is a small bridge between the `Onboarding` object and the standalone extension-step runner.

**Data flow**: It receives an `Onboarded` result, reads the workspace ID from it, and combines that with the object’s manifests and credential store. It passes those values to `run_onboarding_steps`. It does not return anything or directly change data itself.

**Call relations**: `Onboarding.run` calls this after `Onboarding.create` succeeds. This method then hands control to `run_onboarding_steps`, which does the actual per-extension work.

*Call graph*: calls 1 internal fn (run_onboarding_steps); called by 1 (run).


##### `Onboarding._require_credentials_for_steps`  (lines 105–116)

```
def _require_credentials_for_steps(self) -> None
```

**Purpose**: Checks whether installed extensions have onboarding steps that require a credential store, and stops early if the needed credential key is missing. This avoids creating a workspace and only later discovering that extension setup cannot safely store secrets.

**Data flow**: It reads the `credentials` field and the list of extension manifests from the `Onboarding` object. If a credential store exists, it allows setup to continue. If any manifest has onboarding steps but no credential store is available, it raises an error explaining which environment setting is needed.

**Call relations**: `Onboarding.create` calls this before touching the database. It sits beside the model-key check as an early safety gate, making sure required setup inputs are present before `_create_workspace` creates durable records.

*Call graph*: called by 1 (create).


##### `Onboarding._require_model_key`  (lines 118–125)

```
def _require_model_key(self) -> None
```

**Purpose**: Checks that the selected AI model has the environment-provided key it needs before first use. This prevents setup from succeeding when the first assistant turn would immediately fail because the model cannot authenticate.

**Data flow**: It asks `_model_key_env` which environment variable, if any, should contain the key for the chosen model. If no key name is required, it allows setup to continue. If a key name is required but that environment variable is empty or missing, it raises an error.

**Call relations**: `Onboarding.create` calls this as the first validation step. It relies on `_model_key_env` to identify the right environment variable, then either lets the creation flow continue or stops before `_create_workspace` changes the database.

*Call graph*: calls 1 internal fn (_model_key_env); called by 1 (create).


##### `Onboarding._model_key_env`  (lines 127–130)

```
def _model_key_env(self) -> str | None
```

**Purpose**: Finds the name of the environment variable that should hold the key for the selected model. If the provider comes from an extension and cannot be checked this early, it may return nothing.

**Data flow**: It reads the onboarding configuration, installed manifests, and selected model name. It builds or consults the model registry, which is the project’s lookup table for available model providers, and asks it for the needed key environment variable. It returns that variable name or `None`.

**Call relations**: `Onboarding._require_model_key` calls this when deciding whether it can check for a model key immediately. This function delegates model-provider knowledge to `model_registry` instead of hard-coding it in onboarding.

*Call graph*: called by 1 (_require_model_key); 1 external calls (model_registry).


##### `Onboarding._create_workspace`  (lines 132–156)

```
async def _create_workspace(self) -> Onboarded
```

**Purpose**: Creates the actual database records for a fresh UFO installation: the workspace, the first admin member, and the main assistant agent. It also protects against running first-time setup twice.

**Data flow**: It opens a workspace database transaction, then looks for any existing member email. If it finds one, it raises `AlreadyInitialized`. If not, it generates new IDs, inserts a workspace row, creates the admin member using the provided email, inserts the main agent with the default prompt and selected model, and returns an `Onboarded` object containing the workspace and member IDs.

**Call relations**: `Onboarding.create` calls this only after key and credential checks pass. It uses `workspace_tx` to group the database changes together, `create_member` to create the first admin seat, SQLAlchemy insert/select calls to read and write database rows, and `uuid4` to give the new workspace and agent unique IDs.

*Call graph*: called by 1 (create); 7 external calls (__init__, __init__, insert, select, workspace_tx, create_member, uuid4).

## 📊 State Registers Touched

- `reg-database-schema` — The shared database layout and migration version that define which long-term records the system can store.
- `reg-workspace-boundary` — The current workspace or tenant boundary used to keep each customer’s data and actions separate.
- `reg-auth-session` — The signed login and identity state that proves which member or operator is using the system.
- `reg-workspace-objects` — The shared records for workspaces, agents, members, conversations, artifacts, memories, sources, and other workspace objects.
- `reg-membership-and-seats` — The shared membership, admin role, paid seat, and seat-limit state for a workspace.
- `reg-agent-identity` — The saved identity and settings of each agent, including its main workspace role and whether it may use the internet.
- `reg-onboarding-claims` — The hosted signup state for email claims, invitations, company-domain workspace mapping, and temporary access tokens.
