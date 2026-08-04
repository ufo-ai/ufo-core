# Hosted Control-Plane Onboarding  `stage-4`

This stage is the front door for hosted UFO workspaces. It runs before normal workspace use, when a new customer or employee is proving who they are, joining the right workspace, and getting a sign-in token. The main web server in gateway.py coordinates the flow: install the client, enter a work email, pass any invite requirement, create or find a workspace, and sign in.

The client can be guided in two ways. gateway_directives.py sends small text instructions to the terminal client, including first-time install help. gateway_web.py turns the same steps into browser pages and JSON for the web interface. gateway_claim.py handles email proof by creating a short-lived secret code, emailing it, and later checking it safely. gateway_email.py rejects unsuitable personal addresses, builds verification and invite emails, and sends them. gateway_store.py keeps temporary signup claims in PostgreSQL. gateway_invite.py manages one-use invitations. gateway_shared.py maps a verified company domain to a shared workspace. gateway_slack_connect.py starts Slack Connect invites in the background. gateway_token.py creates the short-lived proof used to authenticate with the gateway.

## Files in this stage

### Public onboarding surface
The gateway exposes the hosted signup flow and renders the terminal or browser instructions users follow.

### `control/src/ufo_control/gateway.py`

`entrypoint` · `startup, request handling, shutdown`

This is the front door for hosted UFO onboarding. Without it, new users would not have a simple path from “I ran the installer” to “I am signed in to the right shared workspace.” It also exposes small web endpoints for health checks, login, the install script, and fleet size.

The main idea is a short conversation with the user. The server remembers a claim for a browser or terminal session. If there is no claim yet, it asks for a work email and sends a code. If the email is waiting for proof, it checks the code. Once the email is verified, it finds or creates the shared workspace for that email domain, records that the onboarding claim is complete, and returns instructions called directives. A directive is a small command-like line such as “say this,” “ask this,” “store this token,” or “use this workspace.”

The file also sets up the FastAPI app, which is the web framework receiving HTTP requests. At startup it checks required environment variables, checks the database schema, creates a database connection pool, starts optional Slack invite delivery, and builds the shared GatewayState. On shutdown it cancels background work and closes database resources. The health endpoint deliberately checks both the owner database role and the workspace-serving role, so a deployment can catch a badly wired database before users rely on it.

#### Function details

##### `_stamp_script`  (lines 66–70)

```
def _stamp_script(text: str) -> str
```

**Purpose**: Prepares the downloadable UFO installer script for this deployment. It replaces the development version marker with a short fingerprint and points the script at the configured public base URL.

**Data flow**: It takes the raw script text in, hashes that text to make a short version label, reads the public base URL from the environment or uses a default, then returns a modified script string. The original script file is not changed; only the served copy is stamped.

**Call relations**: This runs when the module is loaded to create STAMPED_SCRIPT. Later, gateway_app.serve_script sends that prepared script to users who visit /ufo.

*Call graph*: 1 external calls (sha1).


##### `Onboarding.advance`  (lines 86–92)

```
async def advance(self, channel: str, session: str, body: str, install: bytes) -> bytes
```

**Purpose**: Moves one onboarding session forward by exactly one step. It decides whether the user needs to enter an email, enter a code, or be signed into a workspace.

**Data flow**: It receives a channel name, a session id, the user’s latest text, and optional install instructions. It looks up any live onboarding claim for that channel and session, then routes the request to the right next step. It returns rendered bytes that tell the client what to show or do next.

**Call relations**: The web and terminal onboarding endpoints call this after checking request size and session headers. It hands off to Onboarding._collect_email, Onboarding._verify_code, or Onboarding._resolve depending on what is already known about the session.

*Call graph*: calls 3 internal fn (_collect_email, _resolve, _verify_code).


##### `Onboarding._collect_email`  (lines 94–111)

```
async def _collect_email(self, channel: str, session: str, body: str, install: bytes) -> bytes
```

**Purpose**: Starts onboarding by asking for and validating a work email address. If the email is acceptable, it starts the email-code claim process.

**Data flow**: It receives the channel, session, user text, and install bytes. If the user has not typed anything, it returns a welcome message and an email prompt. If text is present, it tries to start a claim; on errors it explains the problem and asks again, and on success it tells the user that a code was emailed.

**Call relations**: Onboarding.advance calls this when there is no existing live claim. It uses the claim workflow to send or record the verification code, and uses directive/render helpers to package the next prompt for the client.

*Call graph*: called by 1 (advance); 2 external calls (directive, render).


##### `Onboarding._verify_code`  (lines 113–122)

```
async def _verify_code(self, claim: OnboardClaim, body: str, install: bytes) -> bytes
```

**Purpose**: Checks the code the user typed after receiving an email verification message. It keeps the flow moving even if another request already verified the same claim.

**Data flow**: It receives an existing onboarding claim, the user’s code text, and install bytes. It asks the claim workflow to verify the code. If verification fails, it reloads the current claim to decide whether to continue, ask for the code again, or go back to asking for email. If verification succeeds, it continues to workspace resolution.

**Call relations**: Onboarding.advance calls this for claims that have an email but are not yet verified. It either renders an error prompt itself or hands the verified claim to Onboarding._resolve.

*Call graph*: calls 1 internal fn (_resolve); called by 1 (advance); 2 external calls (directive, render).


##### `Onboarding._resolve`  (lines 124–131)

```
async def _resolve(self, claim: OnboardClaim, install: bytes) -> bytes
```

**Purpose**: Turns a verified email claim into access to a workspace. It applies the invite rule when needed, creates or finds the workspace, marks onboarding complete, and prepares the signed-in response.

**Data flow**: It receives a verified claim and install bytes. If invites are required and the email domain does not already have a workspace, it checks whether the domain has a usable invite. If allowed, it ensures a workspace exists for the domain, records the workspace id on the claim, and returns the signed-in directives.

**Call relations**: Onboarding.advance and Onboarding._verify_code call this once email proof is available. It may call Onboarding._invite_gate to stop uninvited domains, and then calls Onboarding._signed_in to produce the final client instructions.

*Call graph*: calls 2 internal fn (_invite_gate, _signed_in); called by 2 (_verify_code, advance).


##### `Onboarding._invite_gate`  (lines 133–170)

```
async def _invite_gate(self, claim: OnboardClaim, install: bytes) -> bytes | None
```

**Purpose**: Decides whether a verified email domain is allowed to create a new workspace when invites are required. It returns a clear refusal message when the invite is missing, expired, or already used.

**Data flow**: It receives a verified claim and install bytes. If the claim already has an invite id, it allows the flow to continue. Otherwise it tries to redeem an invite for the email domain and claim id. A valid invite produces no refusal; an invalid state becomes a rendered message that ends the session cleanly.

**Call relations**: Onboarding._resolve calls this only before opening a new workspace under the invite-required policy. It uses the invite code store to redeem grants, then uses directive/render helpers to tell the client why onboarding stops if access is refused.

*Call graph*: called by 1 (_resolve); 2 external calls (directive, render).


##### `Onboarding._signed_in`  (lines 172–197)

```
def _signed_in(self, claim: OnboardClaim, ensured: EnsuredWorkspace, install: bytes) -> bytes
```

**Purpose**: Builds the final successful onboarding response. It gives the client a sign-in token, the workspace URL, and the next prompt or menu the user should see.

**Data flow**: It receives the verified claim, the ensured workspace record, and install bytes. It creates a token tied to the workspace and email, checks whether the user is from the operator email domain, then renders directives for token, workspace, optional debugger URL, signed-in message, and next action.

**Call relations**: Onboarding._resolve calls this after workspace access is ready and the claim has been completed. It uses mint_token for the credential and directive/render helpers to package the result for terminal or web clients.

*Call graph*: called by 1 (_resolve); 3 external calls (directive, render, mint_token).


##### `GatewayState.healthy`  (lines 207–215)

```
async def healthy(self) -> bool
```

**Purpose**: Checks whether the running gateway can talk to the database using the expected roles. This is more than “is the process alive”; it confirms the important database wiring is correct.

**Data flow**: It reads the current user from the owner database pool and from a workspace-serving transaction. If either query fails, it logs the failure and returns false. If both work, it returns true only when the roles match the roles captured at startup.

**Call relations**: gateway_app.healthz calls this when a health check request arrives. It uses workspace_tx to test the shared workspace database path as well as the gateway’s own database pool.

*Call graph*: 2 external calls (text, workspace_tx).


##### `_require_env`  (lines 218–222)

```
def _require_env(name: str) -> str
```

**Purpose**: Reads a required environment variable and fails loudly if it is missing. This prevents the server from starting in a half-configured state.

**Data flow**: It receives an environment variable name, looks it up, and returns the value if present. If the value is empty or unset, it raises a runtime error naming the missing setting.

**Call relations**: gateway_app.lifespan uses this during startup for settings such as the workspace base URL and token secret. That makes configuration mistakes appear before the server accepts requests.

*Call graph*: called by 1 (lifespan).


##### `_invite_required`  (lines 225–234)

```
def _invite_required() -> bool
```

**Purpose**: Decides whether new workspace creation must be protected by invites. The safe default is yes, so forgetting the setting does not accidentally open public signup.

**Data flow**: It reads UFO_INVITE_REQUIRED from the environment, treats true/1 as enabled and false/0 as disabled, and returns a boolean. If the value is anything else, it raises an error instead of guessing.

**Call relations**: gateway_app.lifespan calls this at startup and stores the result inside Onboarding. Later, Onboarding._resolve uses that stored policy to decide whether to run the invite gate.

*Call graph*: called by 1 (lifespan).


##### `_dsn_role`  (lines 237–241)

```
def _dsn_role(dsn: str) -> str
```

**Purpose**: Extracts the database username, also called the role, from a database connection string. The gateway uses this to remember which database identities it expects to be using.

**Data flow**: It receives a database DSN string, parses it, and returns the username part. If the DSN has no username, it raises an error because the health check would not be meaningful.

**Call relations**: gateway_app.lifespan calls this during startup for both the owner and serving database URLs. GatewayState.healthy later compares live database users against these stored expected roles.

*Call graph*: called by 1 (lifespan).


##### `_request_body`  (lines 244–250)

```
async def _request_body(request: Request) -> str
```

**Purpose**: Safely reads a small text request body from an HTTP request. It protects the onboarding endpoints from oversized input.

**Data flow**: It streams the request body in chunks, keeping only up to the configured maximum plus one byte. If the body grows too large, it raises a request input error. Otherwise it decodes the bytes as UTF-8, replaces invalid characters, trims whitespace, and returns the text.

**Call relations**: gateway_app.onboard_web and gateway_app.onboard call this after validating headers. Those endpoints catch its input errors and turn them into user-facing directive messages.

*Call graph*: called by 2 (onboard, onboard_web); 2 external calls (__init__, stream).


##### `gateway_app`  (lines 253–381)

```
def gateway_app() -> FastAPI
```

**Purpose**: Creates and returns the FastAPI web application for the gateway. It defines startup and shutdown behavior, then registers all HTTP routes used by onboarding and small status pages.

**Data flow**: It starts with no shared state, defines a lifespan function that will build that state later, creates a FastAPI app, attaches route functions to paths, and returns the app object. The module-level app variable is built from this return value.

**Call relations**: This is the top-level assembly point for the file. Its nested routes call into GatewayState, Onboarding, and helper functions while FastAPI calls the routes in response to HTTP traffic.

*Call graph*: 1 external calls (FastAPI).


##### `gateway_app.lifespan`  (lines 257–309)

```
async def lifespan(app: FastAPI)
```

**Purpose**: Sets up everything the gateway needs before serving requests and cleans it up after shutdown. It is the server’s opening and closing checklist.

**Data flow**: On startup it reads database URLs and environment settings, checks the control schema, opens a database pool, creates stores and workflows, builds GatewayState, initializes the workspace database path, and optionally starts a Slack Connect background task. On shutdown it cancels that task if present, clears state, disposes database resources, and closes the pool.

**Call relations**: FastAPI runs this automatically around the app’s lifetime. It calls helpers such as _require_env, _invite_required, and _dsn_role, constructs Onboarding and GatewayState, and supplies the shared state that all request handlers depend on.

*Call graph*: calls 3 internal fn (_dsn_role, _invite_required, _require_env); 18 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, create_task, gather, create_pool (+8 more)).


##### `gateway_app.healthz`  (lines 314–317)

```
async def healthz() -> Response
```

**Purpose**: Answers a health-check request. It tells load balancers or deployment tools whether the gateway is ready and correctly connected.

**Data flow**: It reads the shared state. If the state is missing or the deeper health check fails, it returns a JSON response with status unavailable and HTTP 503. If all checks pass, it returns status ok.

**Call relations**: FastAPI calls this for GET /healthz. It relies on GatewayState.healthy to test the database roles before reporting success.

*Call graph*: 1 external calls (JSONResponse).


##### `gateway_app.serve_script`  (lines 320–321)

```
async def serve_script() -> Response
```

**Purpose**: Serves the install script that users can download or run. The script has already been stamped with the right version and public URL for this deployment.

**Data flow**: It reads the module-level STAMPED_SCRIPT and returns it as plain text with a shell-script media type. It does not read the script file on each request.

**Call relations**: FastAPI calls this for GET /ufo. The served content comes from _stamp_script, which prepared the script when the module loaded.

*Call graph*: 1 external calls (PlainTextResponse).


##### `gateway_app.fleet`  (lines 324–327)

```
async def fleet() -> Response
```

**Purpose**: Reports how many workspaces exist. It is a tiny status endpoint for the size of the hosted fleet.

**Data flow**: It queries the database pool for the count of rows in the workspace table, then returns that count as JSON under the name craft.

**Call relations**: FastAPI calls this for GET /fleet. It depends on the database pool created in gateway_app.lifespan.

*Call graph*: 1 external calls (JSONResponse).


##### `gateway_app.login`  (lines 330–331)

```
async def login() -> Response
```

**Purpose**: Serves the browser login page. This gives web users a human-facing entry page instead of only terminal-style responses.

**Data flow**: It returns the LOGIN_PAGE HTML as an HTML response. It does not perform login itself; it only serves the page that can start the web flow.

**Call relations**: FastAPI calls this for GET /login. The actual web onboarding conversation continues through gateway_app.onboard_web.

*Call graph*: 1 external calls (HTMLResponse).


##### `gateway_app.onboard_web`  (lines 334–349)

```
async def onboard_web(request: Request) -> Response
```

**Purpose**: Runs one step of onboarding for the web client. It accepts browser-style input and returns structured JSON directives that the web page can interpret.

**Data flow**: It requires an x-ufo-session header, checks the session length, reads the small request body, and calls the onboarding flow using the web channel. If input is bad or an unexpected error happens, it creates failure directives. It parses the rendered directive bytes into JSON-friendly data and returns them.

**Call relations**: FastAPI calls this for POST /v1/onboard/web. It uses _request_body for safe input reading, then hands the real onboarding decision to Onboarding.advance through the shared state.

*Call graph*: calls 1 internal fn (_request_body); 5 external calls (__init__, JSONResponse, directive, render, parse_directives).


##### `gateway_app.onboard`  (lines 352–379)

```
async def onboard(channel: str, request: Request) -> Response
```

**Purpose**: Runs one step of onboarding for non-web channels, especially the terminal installer/client. It returns plain text directives that the client can read line by line.

**Data flow**: It extracts the channel from the URL, reads the x-ufo-session header, gathers first-run install directives from request headers, checks channel and session sizes, reads the body, and calls the onboarding flow. Missing or bad input becomes a rendered message plus an exit directive; unexpected failures become a generic onboarding failed message.

**Call relations**: FastAPI calls this for POST /v1/onboard/{channel}. It uses first_run_install to include setup instructions, _request_body to limit incoming text, and Onboarding.advance to decide the next onboarding step.

*Call graph*: calls 1 internal fn (_request_body); 5 external calls (__init__, PlainTextResponse, directive, first_run_install, render).


### `control/src/ufo_control/gateway_directives.py`

`io_transport` · `request handling`

The terminal client and the server need a simple way to talk about what the client should do next. This file defines that tiny command format. Each command is a line of bytes: a verb, optional fields separated by tabs, and a newline at the end. Before sending fields, it escapes special characters like tabs, backslashes, and newlines so the client can read the message safely and not confuse part of a field for part of the format.

Think of it like writing labels on boxes before shipping them. The label has to use a predictable layout, and any odd characters inside the label text must be written in a safe form so the receiver does not misread the package.

The file also contains a small helper for reading HTTP-style headers without caring about letter case. That matters because header names such as `X-UFO-Installed` and `x-ufo-installed` mean the same thing.

Its most specific job is the first-run install check. When someone runs UFO through a fresh `curl | sh` flow, the server can prepend an `install` directive. That tells the shell to download UFO into the user's home directory and keep going with the same session. Once the client reports that UFO is already installed, this file stops sending that install directive.

#### Function details

##### `directive`  (lines 8–13)

```
def directive(verb: str, *fields: str) -> bytes
```

**Purpose**: Builds one server-to-client command as bytes. It takes a command name and optional text fields, escapes characters that would break the wire format, and returns a single newline-ended message ready to send.

**Data flow**: It receives a verb and any number of text fields. Each field is cleaned up for transport: backslashes and tabs are escaped, carriage returns are removed, and newlines are written as the two characters `\n`. It then joins the verb and fields with tab characters, adds a final newline, and turns the whole thing into bytes.

**Call relations**: This is the basic command maker for this file. `first_run_install` calls it when it decides the client should receive an `install` instruction.

*Call graph*: called by 1 (first_run_install).


##### `render`  (lines 16–17)

```
def render(*lines: bytes) -> bytes
```

**Purpose**: Combines several already-built byte messages into one byte string. Someone would use it when a response screen is made from multiple directives.

**Data flow**: It receives any number of byte chunks. It places them one after another without adding anything extra. The result is one combined byte string that can be sent onward.

**Call relations**: This helper stands ready for code that wants to assemble multiple directive lines into one response. In the provided call facts, no listed function calls it directly.


##### `header_value`  (lines 20–25)

```
def header_value(headers: Mapping[str, str], name: str) -> str | None
```

**Purpose**: Finds a header value by name while ignoring capitalization. This is useful because HTTP-style header names are meant to be case-insensitive.

**Data flow**: It receives a mapping of header names to values and the header name to look for. It lowercases the requested name, compares it with each available header name in lowercase form, and returns the matching value if found. If nothing matches, it returns `None`.

**Call relations**: This function supports the first-run install decision. `first_run_install` uses it to check whether the client has reported `x-ufo-installed`.

*Call graph*: called by 1 (first_run_install).


##### `first_run_install`  (lines 28–35)

```
def first_run_install(headers: Mapping[str, str]) -> bytes
```

**Purpose**: Decides whether to send an initial `install` directive to a newly connected shell. It prevents repeat installs by checking whether the client says UFO is already installed.

**Data flow**: It receives request headers from the client. It asks `header_value` for the `x-ufo-installed` header. If that value is exactly `1`, it returns empty bytes, meaning no install command is needed. Otherwise, it returns the bytes for an `install` directive built by `directive`.

**Call relations**: This is the file's main decision point. During request handling, server code can call it before rendering the first screen. It reads the install status through `header_value`, and when installation is needed, it hands off to `directive` to create the actual command sent to the terminal client.

*Call graph*: calls 2 internal fn (directive, header_value).


### `control/src/ufo_control/gateway_web.py`

`io_transport` · `web login and onboarding request handling`

This file is the web face of an onboarding flow, meaning the process that signs a person in and connects them to a workspace. The important idea is that the browser is not running a separate sign-in system. It is only another display for the same onboarding machine used by the terminal client. That keeps the rules in one place, so the web page and terminal cannot drift apart.

The Python part includes a small translator, parse_directives, that reads directive lines from the onboarding engine. A directive is a simple command such as “say this message,” “ask this question,” “here is the token,” or “here is the workspace.” The translator turns those line-based commands into dictionaries that can be sent to browser JavaScript as JSON.

The large LOGIN_PAGE string is a complete self-contained HTML page. Its JavaScript creates a random browser session id, calls POST /v1/onboard/web to advance the onboarding flow, shows messages, prompts for answers, and finally displays a signed-in card. That final card includes the member email, workspace URL, a terminal install command, and sometimes a session debugger button for operator-domain accounts. Tokens are submitted through POST forms, not placed in URLs, which helps avoid leaking sensitive bearer tokens through browser history or logs.

#### Function details

##### `parse_directives`  (lines 15–24)

```
def parse_directives(payload: bytes) -> list[dict[str, object]]
```

**Purpose**: This function converts the onboarding engine’s plain text directive stream into a browser-friendly list of dictionaries. It exists so the web page can receive the same instructions as the terminal client, but in JSON form.

**Data flow**: It receives bytes containing lines of tab-separated directive text. It decodes the bytes into text, skips blank lines, splits each line into a command word and its fields, unescapes any protected tabs or newlines inside the fields, and returns a list like “verb plus fields” objects that can be encoded as JSON.

**Call relations**: When the web endpoint needs to answer the browser, it uses this function to translate the onboarding output before sending it back. During that translation, this function calls _unescape for each field so that values containing real tabs or line breaks are restored correctly.

*Call graph*: calls 1 internal fn (_unescape).


##### `_unescape`  (lines 27–40)

```
def _unescape(field: str) -> str
```

**Purpose**: This helper restores special characters that were safely escaped inside a directive field. It makes sure a field’s real tabs, newlines, and backslashes survive the line-based format.

**Data flow**: It receives one text field that may contain escape sequences such as backslash-t or backslash-n. It walks through the text character by character, replaces known escape sequences with their real characters, leaves unknown or ordinary characters alone, and returns the restored string.

**Call relations**: This function is used by parse_directives while preparing directive data for the browser. It is the small decoding step that makes the larger web translation safe and accurate, especially when messages or prompts contain formatting characters.

*Call graph*: called by 1 (parse_directives).


### Email proof and delivery
These files validate work email addresses, issue verification claims, deliver onboarding mail, and persist claim state.

### `control/src/ufo_control/gateway_claim.py`

`domain_logic` · `request handling during onboarding email verification`

This file protects the onboarding flow by proving that a person can receive email at a permitted work address. Without it, anyone could claim an organization domain without access to that domain's email, or verification codes might be stored in a form that is unsafe if the database leaks.

The flow works like a coat-check ticket. When onboarding starts, the system checks that the email address is allowed, creates a random six-digit code, stores only a fingerprint of that code, and sends the real code by email. The stored onboarding claim also records when the code expires, how many guesses have been made, and what part of the product started the request.

When the user submits a code, the workflow compares the submitted code's fingerprint with the stored fingerprint. It uses a safe comparison method so timing differences do not reveal clues about the code. The code expires after a fixed time, and the user only gets a limited number of attempts. If the email cannot be sent, the claim is removed so there is no dead verification session left behind.

A key detail is that updates are cautious. The store methods are expected to confirm that the claim has not changed underneath this request. If another verification attempt changed the same session first, the user gets a specific “session changed” error rather than silently overwriting newer state.

#### Function details

##### `hash_code`  (lines 26–27)

```
def hash_code(code: str) -> str
```

**Purpose**: Turns a verification code into a one-way fingerprint. This lets the system check a code later without keeping the readable code in storage.

**Data flow**: A plain text code goes in. The function encodes it and runs it through SHA-256, a standard one-way hashing method. A hexadecimal hash string comes out, which can be stored or compared without revealing the original code.

**Call relations**: ClaimWorkflow.start uses this when it first creates the code, so only the hash is saved in the claim. ClaimWorkflow.verify uses it again on the user's submitted code, then compares that new hash with the saved one.

*Call graph*: called by 2 (start, verify); 1 external calls (sha256).


##### `ClaimWorkflow.start`  (lines 38–61)

```
async def start(self, email: str, surface: str, surface_ref: str) -> str
```

**Purpose**: Begins an email verification session for onboarding. It validates the work email, creates and stores a temporary claim, sends the verification email, and returns the approved email domain.

**Data flow**: An email address, a product surface name, and a surface reference go in. The email policy checks the address and extracts its domain. The function generates a random six-digit code, builds an onboarding claim with a new ID, a lowercased email, the hashed code, an expiry time, and zero attempts, then saves it. It creates the email text and sends it. If sending fails, it deletes the saved claim and raises a user-facing ClaimError. If all succeeds, the verified candidate domain comes out as the return value.

**Call relations**: This is called when a user starts onboarding and needs to prove access to a work email. Inside that flow it relies on hash_code to avoid storing the raw code, creates an OnboardClaim as the record of the pending session, uses the email template builder to prepare the message, and raises ClaimError when the user should be shown a clean failure message.

*Call graph*: calls 1 internal fn (hash_code); 6 external calls (__init__, __init__, now, randbelow, verification_email, uuid4).


##### `ClaimWorkflow.verify`  (lines 63–79)

```
async def verify(self, claim: OnboardClaim, code: str) -> None
```

**Purpose**: Checks a submitted verification code for an existing onboarding claim. It enforces expiry, limits wrong guesses, records success, and reports clear errors when the session is stale or invalid.

**Data flow**: An existing claim and the code typed by the user go in. The function first checks whether the claim has expired. If it has, it tries to delete the unverified claim and reports expiration. If not expired, it hashes the submitted code and safely compares it to the stored hash. A match records verification and returns nothing on success. A wrong code increases the attempt count, possibly deletes the claim if the maximum has been reached, or records one more failed attempt and raises an error saying the code is incorrect. If the stored claim changed between reading and writing, it raises the session-changed error instead.

**Call relations**: This is used after a user submits the code from their email. It calls hash_code so it can compare fingerprints rather than readable codes, and it uses hmac.compare_digest for a safer equality check. It raises ClaimError for all outcomes the onboarding UI needs to explain to the user: expired code, too many attempts, wrong code, or a verification session changed by another attempt.

*Call graph*: calls 1 internal fn (hash_code); 3 external calls (__init__, now, compare_digest).


### `control/src/ufo_control/gateway_email.py`

`io_transport` · `onboarding and invite request handling`

This file is the email front door for the control service. First, it protects the product’s “workspace equals real organization” rule: it normalizes an email address, checks that it is shaped like a real address, and rejects well-known free or disposable domains such as Gmail or Mailinator. That keeps temporary or personal addresses from creating organization workspaces.

It also creates the plain-text messages the service sends: a short verification-code email and an invite email that tells someone how to install and sign in. The public host used in the invite is read from configuration so the same code can work in local, staging, and production environments.

For delivery, the file defines a small sender interface, like a mail slot that different senders can fit into. In production, `SesEmailSender` sends through Amazon SES version 2. It reads the pod’s web identity token, asks AWS STS for temporary credentials, signs the SES request using AWS Signature Version 4, and posts it asynchronously with `httpx`. In local development, `ConsoleEmailSender` writes the same message to the process log instead of sending real mail. `email_sender_from_env` chooses between those modes and fails loudly if required settings are missing, which avoids silent email loss.

#### Function details

##### `normalize_email`  (lines 119–125)

```
def normalize_email(email: str) -> tuple[str, str]
```

**Purpose**: Cleans up an email address and extracts its domain, such as `example.com`. It rejects addresses that do not look like normal email addresses so later code does not make decisions from bad input.

**Data flow**: It receives a raw email string → trims surrounding spaces, lowercases it, and checks it against a simple email pattern → returns the cleaned full address plus the domain. If the address is malformed, it raises `WorkEmailError` instead of returning uncertain data.

**Call relations**: This is the shared first step for `WorkEmailPolicy.validate` and `invite_email`. Those functions call it before either enforcing the work-email rule or placing the domain into an invite message.

*Call graph*: called by 2 (validate, invite_email); 1 external calls (__init__).


##### `WorkEmailPolicy.validate`  (lines 132–136)

```
def validate(self, email: str) -> str
```

**Purpose**: Checks whether an email belongs to an acceptable work domain. It is used to block free personal providers and disposable inboxes from being treated as organization identities.

**Data flow**: It receives an email string → asks `normalize_email` for the cleaned domain → compares that domain with the built-in denylist → returns the domain when allowed, or raises `WorkEmailError` when blocked.

**Call relations**: This method builds directly on `normalize_email`. It is the policy gate: callers use it when they need to know whether an email can represent a real workplace.

*Call graph*: calls 1 internal fn (normalize_email); 1 external calls (__init__).


##### `public_apex_host`  (lines 139–147)

```
def public_apex_host() -> str
```

**Purpose**: Finds the public website host used in invite instructions. This lets generated emails point at the right front door for the deployment.

**Data flow**: It reads `UFO_PUBLIC_BASE_URL` from the environment, or falls back to `https://flyingobject.ai` → strips away the scheme and path using URL parsing → returns just the host. If the value is not a usable base URL, it raises an error.

**Call relations**: This helper supplies the host that can be passed into `invite_email`. It relies on standard URL parsing rather than guessing by string slicing.

*Call graph*: 1 external calls (urlsplit).


##### `verification_email`  (lines 150–156)

```
def verification_email(code: str, expires_at: datetime, ttl: timedelta) -> tuple[str, str]
```

**Purpose**: Builds the subject and body for a verification-code email. It keeps the wording of this message in one place.

**Data flow**: It receives a code, an expiry time, and a time-to-live duration → formats the expiry as a UTC time and converts the duration to minutes → returns a subject string and a plain-text body string.

**Call relations**: This function does not send anything itself. It prepares the message text that an `EmailSender` implementation, such as `SesEmailSender.send` or `ConsoleEmailSender.send`, can deliver.

*Call graph*: 2 external calls (strftime, total_seconds).


##### `invite_email`  (lines 159–169)

```
def invite_email(email: str, expires_at: datetime, apex_host: str) -> tuple[str, str]
```

**Purpose**: Builds the subject and body for an invite email. The message tells the recipient how to install the tool and which email/domain the invite applies to.

**Data flow**: It receives an email address, an expiry time, and the public host → uses `normalize_email` to get the email domain → formats the expiry in UTC and inserts the email, domain, and host into the invite text → returns the subject and body.

**Call relations**: This function reuses `normalize_email` so the domain mentioned in the invite is derived consistently. Like `verification_email`, it only creates text; a sender is responsible for delivery.

*Call graph*: calls 1 internal fn (normalize_email); 1 external calls (astimezone).


##### `EmailSender.send`  (lines 173–173)

```
async def send(self, email: str, subject: str, text: str) -> None
```

**Purpose**: Defines the common shape of an email sender. Any class with this async `send` method can be used wherever the service needs to send email.

**Data flow**: It is a protocol method, so it does not perform work itself. The expected inputs are recipient email, subject, and text body → an implementing sender delivers or records the message → no value is returned.

**Call relations**: Both `SesEmailSender.send` and `ConsoleEmailSender.send` follow this shape. `email_sender_from_env` returns an object that fits this protocol so the rest of the service does not need to care which delivery mode was selected.


##### `SesEmailSender.send`  (lines 196–217)

```
async def send(self, email: str, subject: str, text: str) -> None
```

**Purpose**: Sends one plain-text email through Amazon SES. It is the production delivery path for verification and invite messages.

**Data flow**: It receives the destination address, subject, and body → gets temporary AWS credentials by calling `_assume_role` → builds the JSON request SES expects → signs that request with `_sigv4_headers` → posts it to the SES endpoint with an asynchronous HTTP client. If SES reports an error, it raises a runtime error with the status and part of the response body; otherwise it returns nothing.

**Call relations**: This method is the main user of the AWS helper functions in this file. It calls `_assume_role` first because the service starts with only a web identity token, then calls `_sigv4_headers` so SES will trust the HTTP request.

*Call graph*: calls 2 internal fn (_assume_role, _sigv4_headers); 3 external calls (now, AsyncClient, dumps).


##### `SesEmailSender._assume_role`  (lines 219–238)

```
async def _assume_role(self) -> SesCredentials
```

**Purpose**: Trades the pod’s web identity token for temporary AWS credentials. This is needed because the service does not keep long-lived AWS keys in its configuration.

**Data flow**: It reads the token file path stored on the sender → sends that token, the role ARN, and session details to AWS STS → checks for an error response → parses the XML response with `_parse_assume_role_credentials` → returns a `SesCredentials` object containing the temporary access key, secret key, and session token.

**Call relations**: `SesEmailSender.send` calls this before every email send. After this function gets credentials from STS, `send` uses them to sign the SES request.

*Call graph*: calls 1 internal fn (_parse_assume_role_credentials); called by 1 (send); 1 external calls (AsyncClient).


##### `_parse_assume_role_credentials`  (lines 241–254)

```
def _parse_assume_role_credentials(payload: str) -> SesCredentials
```

**Purpose**: Extracts temporary AWS credentials from the XML response returned by STS. It turns a bulky XML document into a small Python data object the sender can use.

**Data flow**: It receives the raw XML payload as text → parses it into an XML tree → uses the nested `credential` helper to find each required field → returns `SesCredentials`. If any required field is missing, the helper raises an error instead of returning incomplete credentials.

**Call relations**: `SesEmailSender._assume_role` calls this after a successful STS HTTP response. The result then flows back to `SesEmailSender.send`, where it is used for request signing.

*Call graph*: called by 1 (_assume_role); 2 external calls (__init__, fromstring).


##### `_parse_assume_role_credentials.credential`  (lines 244–248)

```
def credential(name: str) -> str
```

**Purpose**: Looks up one named credential value inside the STS XML response. It makes missing credential fields fail clearly.

**Data flow**: It receives the name of a credential field, such as `AccessKeyId` → searches the parsed XML tree under the STS credentials section → returns the text value. If the value is absent or empty, it raises a runtime error naming the missing field.

**Call relations**: This helper lives inside `_parse_assume_role_credentials` and is used there three times: once each for the access key, secret key, and session token.


##### `_sigv4_headers`  (lines 257–290)

```
def _sigv4_headers(host: str, body: bytes, region: str, credentials: SesCredentials, now: datetime) -> dict[str, str]
```

**Purpose**: Creates the AWS Signature Version 4 headers required for SES to accept a request. Signature Version 4 is AWS’s standard way to prove a request came from someone with valid credentials and was not changed in transit.

**Data flow**: It receives the target host, request body bytes, AWS region, temporary credentials, and current time → hashes the body, builds the canonical request AWS expects, creates a string to sign, gets a signing key from `_signing_key`, and calculates the final signature → returns HTTP headers including authorization, date, body hash, and session token.

**Call relations**: `SesEmailSender.send` calls this immediately before making the SES HTTP request. It delegates the layered key calculation to `_signing_key`, then hands signed headers back to the sender for the POST.

*Call graph*: calls 1 internal fn (_signing_key); called by 1 (send); 3 external calls (strftime, sha256, new).


##### `_signing_key`  (lines 293–297)

```
def _signing_key(secret_key: str, date_stamp: str, region: str) -> bytes
```

**Purpose**: Builds the derived secret key used for AWS request signing. This is a small cryptographic helper for Signature Version 4.

**Data flow**: It receives the AWS secret key, date stamp, and region → repeatedly applies HMAC-SHA256, which is a keyed hash used to prove authenticity, over the date, region, SES service name, and AWS request label → returns the final signing key as bytes.

**Call relations**: _sigv4_headers calls this while assembling the SES authorization header. This function does not know about email messages; it only performs the key-derivation step needed by AWS.

*Call graph*: called by 1 (_sigv4_headers); 1 external calls (new).


##### `ConsoleEmailSender.send`  (lines 307–308)

```
async def send(self, email: str, subject: str, text: str) -> None
```

**Purpose**: Pretends to send an email by writing it to the application log. This is useful for local development where a real SES account is not available or not desired.

**Data flow**: It receives the destination address, subject, and body → writes all three into a log message marked as console email mode → returns nothing and does not contact any network service.

**Call relations**: `email_sender_from_env` creates this sender when `UFO_CONTROL_EMAIL_MODE` is set to `console`. It follows the same `EmailSender.send` shape as the SES sender, so the rest of the service can use it the same way.


##### `email_sender_from_env`  (lines 311–325)

```
def email_sender_from_env() -> EmailSender
```

**Purpose**: Chooses the email delivery method from environment variables. It gives deployments one switch for real SES delivery versus local log-only delivery.

**Data flow**: It reads `UFO_CONTROL_EMAIL_MODE` → if the mode is `console`, it returns a `ConsoleEmailSender` → if the mode is `ses`, it reads required SES and AWS identity settings with `_require_env`, builds a token-file path, and returns a `SesEmailSender`. If the mode is unknown, it raises an error.

**Call relations**: This is the setup function for the sender system. It calls `_require_env` so SES mode cannot start with missing critical configuration, and it constructs the sender object that later code will call through the `EmailSender` interface.

*Call graph*: calls 1 internal fn (_require_env); 3 external calls (__init__, __init__, Path).


##### `_require_env`  (lines 328–332)

```
def _require_env(name: str) -> str
```

**Purpose**: Reads a required environment variable and fails clearly if it is missing. This prevents email setup from quietly continuing with blank AWS or SES settings.

**Data flow**: It receives the name of an environment variable → reads that variable from the process environment → returns its value when present. If the value is missing or empty, it raises a runtime error that names the required setting.

**Call relations**: `email_sender_from_env` calls this while building a production `SesEmailSender`. It is the guardrail that makes configuration mistakes visible at startup or sender creation time.

*Call graph*: called by 1 (email_sender_from_env).


### `control/src/ufo_control/gateway_store.py`

`io_transport` · `request handling during onboarding`

This file is the database shelf for the hosted onboarding flow. When someone starts onboarding, the system needs a durable place to keep the email address, the hashed verification code, how many times the user has tried, when the code expires, and whether the claim has already produced a workspace. Without this file, that state would be lost between requests or server restarts, and the system could not safely enforce retries, expiration, or one active claim per signup surface.

The file defines the PostgreSQL table shape in `DDL`, including a special unique rule: for a given `surface` and `surface_ref`, there can only be one active claim that has not yet created a workspace. Think of it like allowing only one open ticket for the same counter and customer reference.

`OnboardClaim` is a small data container for one claim. `OnboardStore` wraps an `asyncpg` connection pool, which is a reusable set of database connections for asynchronous Python code. Each method borrows a connection, performs one database action, then gives the connection back.

A few methods are deliberately careful about race conditions. For example, retry and verification updates only succeed if the caller’s known attempt count still matches the database and the claim has not already been verified. That prevents two overlapping requests from both changing the same claim as if they were first.

#### Function details

##### `_aware`  (lines 46–49)

```
def _aware(value: datetime | None) -> datetime | None
```

**Purpose**: This helper makes sure a timestamp has timezone information. It treats a timestamp without a timezone as UTC, which means “Coordinated Universal Time,” the common neutral time standard used by servers.

**Data flow**: It receives either a date-time value or nothing. If it gets nothing, it returns nothing. If the date-time already says what timezone it belongs to, it leaves it alone; otherwise it adds UTC and returns the safer, timezone-aware value.

**Call relations**: It is used when `OnboardStore.live_claim` rebuilds an `OnboardClaim` from a database row. That keeps times read from PostgreSQL consistent before the claim is handed back to the onboarding code.

*Call graph*: called by 1 (live_claim); 1 external calls (replace).


##### `OnboardStore.insert_claim`  (lines 56–70)

```
async def insert_claim(self, claim: OnboardClaim) -> None
```

**Purpose**: This saves a newly created onboarding claim into PostgreSQL. It is used when a person begins onboarding and the system needs to remember the verification code details and expiration time.

**Data flow**: It receives an `OnboardClaim` object. It borrows a database connection from the pool, writes the claim’s id, email, email domain, hashed code, signup surface, attempt count, and expiration time into the onboard claim table, then returns nothing after the database accepts the insert.

**Call relations**: This is an early step in the onboarding flow. Other onboarding code creates the claim, then calls this method so later requests can find that same claim with `OnboardStore.live_claim`.


##### `OnboardStore.live_claim`  (lines 72–93)

```
async def live_claim(self, surface: str, surface_ref: str) -> OnboardClaim | None
```

**Purpose**: This looks up the currently active claim for a particular signup surface and reference. It only returns claims that have not yet resulted in a workspace.

**Data flow**: It receives a `surface` and `surface_ref`, which identify where the onboarding attempt came from. It asks PostgreSQL for the matching unfinished row. If no row exists, it returns `None`; if a row is found, it converts that row into an `OnboardClaim`, including normalizing timestamp fields with `_aware`.

**Call relations**: This method is called when the system needs to continue or check an existing onboarding attempt. It relies on `_aware` to make database timestamps safe to compare and then hands a plain `OnboardClaim` object back to the caller.

*Call graph*: calls 1 internal fn (_aware); 1 external calls (__init__).


##### `OnboardStore.record_attempt`  (lines 95–103)

```
async def record_attempt(self, claim_id: UUID, attempts: int) -> bool
```

**Purpose**: This records a failed or ordinary verification attempt by increasing the attempt counter. It also protects against stale requests by only updating if the attempt count is still what the caller expected.

**Data flow**: It receives a claim id and the attempt count the caller last saw. It asks PostgreSQL to add one to the stored attempt count, but only if the claim id matches, the stored count equals the supplied count, and the claim has not already been verified. It returns `true` if the update happened and `false` if something had changed first.

**Call relations**: This is used during code-checking flows when the submitted code is not accepted or when an attempt must still be counted. The boolean result tells the caller whether it safely won the race to update the claim.


##### `OnboardStore.record_verification`  (lines 105–113)

```
async def record_verification(self, claim_id: UUID, attempts: int) -> bool
```

**Purpose**: This marks a claim as successfully verified and counts that verification attempt. It is the database step that says, “the user proved control of this email.”

**Data flow**: It receives a claim id and the attempt count the caller believes is current. It updates the row by increasing the attempt count and setting `verified_at` to the database server’s current time, but only if the claim is still unverified and the attempt count matches. It returns `true` when the verification was recorded, or `false` if another request already changed the claim.

**Call relations**: This is called after the onboarding flow decides a submitted verification code is correct. Its guarded update helps prevent two simultaneous submissions from both thinking they verified the same claim first.


##### `OnboardStore.complete`  (lines 115–121)

```
async def complete(self, claim_id: UUID, resulting_workspace_id: str) -> None
```

**Purpose**: This marks an onboarding claim as finished by attaching the workspace id that was created from it. After this, the claim is no longer considered active.

**Data flow**: It receives a claim id and the resulting workspace id. It updates the matching database row so `resulting_workspace_id` is filled in, then returns nothing.

**Call relations**: This comes near the end of onboarding, after verification and workspace creation. Once it runs, `OnboardStore.live_claim` will no longer return this claim as active because it only looks for rows without a resulting workspace.


##### `OnboardStore.delete_claim`  (lines 123–125)

```
async def delete_claim(self, claim_id: UUID) -> None
```

**Purpose**: This removes an onboarding claim from the database, regardless of whether it was verified. It is a direct cleanup operation for a known claim id.

**Data flow**: It receives a claim id. It borrows a database connection, deletes the row with that id if it exists, and returns nothing.

**Call relations**: Other onboarding or cleanup code can call this when a claim should be discarded completely. Unlike `OnboardStore.delete_unverified_claim`, it does not check the attempt count or verification state.


##### `OnboardStore.delete_unverified_claim`  (lines 127–135)

```
async def delete_unverified_claim(self, claim_id: UUID, attempts: int) -> bool
```

**Purpose**: This deletes a claim only if it is still unverified and has the expected attempt count. It is a safer cleanup method for cases where the caller wants to avoid deleting a claim that changed meanwhile.

**Data flow**: It receives a claim id and the attempt count the caller last observed. It asks PostgreSQL to delete the row only if the id matches, the stored attempt count still matches, and `verified_at` is still empty. It returns `true` if the row was deleted and `false` if the claim was already changed, verified, or missing.

**Call relations**: This fits into cleanup or cancellation paths before verification is complete. Like the attempt-recording methods, it uses the attempt count as a simple guard against overlapping requests acting on stale information.


### Invite gates and shared domains
One-time invitations and verified domains determine whether a customer can create or join a shared workspace.

### `control/src/ufo_control/gateway_invite.py`

`domain_logic` · `invite creation and workspace creation`

This file is the gatekeeper for new workspace invites. An invite is not a secret code someone types in. Instead, it is a database record saying: “this numbered waitlist object has granted this email domain permission to create a workspace.” The person proves they belong to that domain by verifying their work email.

The file defines the invite table, including when an invite expires and when it was consumed. It also defines small result objects, such as “unknown,” “expired,” “already consumed,” and “accepted,” so callers can clearly tell what happened.

The main class, InviteCodes, has two jobs. First, it can mint, or create, a new invite for an object number and email address. Before doing that, it normalizes the email address, checks that it is a work email, refuses duplicates, removes old expired live invites for the same object or domain, and then inserts the new invite.

Second, it can redeem an invite when someone with a matching email domain tries to create a workspace. Redemption happens inside a database transaction, with a row lock. A row lock is like putting a “do not touch” sign on that invite while one process is using it. This prevents two people from spending the same invite at the same time. The file also links the consumed invite to the workspace claim in the same transaction, so a crash cannot leave a used invite floating unattached.

#### Function details

##### `InviteCodes.mint`  (lines 92–126)

```
async def mint(self, object_number: int, email: str) -> MintedInvite
```

**Purpose**: Creates a fresh invite for one waitlist object and one email domain. It refuses to create the invite if that object or domain is already identified or already has a still-active invite.

**Data flow**: It receives an object number and an email address. It cleans and splits the email into an address and domain, checks that the address is allowed as a work email, calculates an expiry time, then opens a database transaction. Inside that transaction it checks for existing standing invites, deletes expired unconsumed invites for the same object or domain, and inserts the new invite with a new unique ID. If all goes well, it returns a MintedInvite containing the object number, normalized email, and expiry time; if there is a conflict, it raises InviteError.

**Call relations**: This is the public creation path for invites. During its checks it calls InviteCodes._refuse_standing twice: once to protect the object number, and once to protect the email domain. It also relies on email normalization and work-email validation before anything is written, so the database only stores consistent invite records.

*Call graph*: calls 1 internal fn (_refuse_standing); 6 external calls (__init__, __init__, __init__, now, normalize_email, uuid4).


##### `InviteCodes._refuse_standing`  (lines 128–142)

```
async def _refuse_standing(self, connection: asyncpg.Connection, column: str, value: object, subject: str, now: datetime) -> None
```

**Purpose**: Checks whether a particular object number or email domain already has a meaningful invite history that should block a new invite. It is the helper that turns database state into a clear refusal message.

**Data flow**: It receives an open database connection, the column to search, the value to look for, a human-readable subject name, and the current time. It asks the database whether there is a consumed invite or a still-live invite for that subject. If none exists, it returns silently. If one was already consumed, it raises InviteError saying the subject is already identified. If one is still live, it raises InviteError with the expiry time.

**Call relations**: InviteCodes.mint calls this helper before inserting a new invite. The helper keeps the duplicate-checking rules in one place, so mint can stay focused on the larger create-invite flow.

*Call graph*: called by 1 (mint); 2 external calls (__init__, fetchrow).


##### `InviteCodes.redeem`  (lines 144–187)

```
async def redeem(self, email_domain: str, claim_id: UUID) -> InviteUnknown | InviteExpired | InviteConsumed | InviteAccepted
```

**Purpose**: Attempts to spend an invite for an email domain when a workspace claim is being made. It returns a clear outcome: no invite, expired invite, already-used invite, or successfully accepted invite.

**Data flow**: It receives an email domain and a claim ID. It opens a database transaction, finds the most relevant invite for that domain, and locks that row so no other redemption can change it at the same time. If no invite exists, it returns InviteUnknown. If the invite was already consumed, it checks whether it was consumed by this same claim; if yes, it returns InviteAccepted again, making retries safe, and if not, it returns InviteConsumed. If the invite is expired, it returns InviteExpired. Otherwise, it marks the invite as consumed, attaches the invite ID to the claim record, and returns InviteAccepted with the invite ID, object number, and consumption time.

**Call relations**: This is the public redemption path used by the workspace-creation flow. It does not call the minting helper; instead, it directly reads and updates the invite and claim tables in one transaction. Its careful ordering means the invite is consumed and attached to the claim as one all-or-nothing database change.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, now).


### `control/src/ufo_control/gateway_shared.py`

`domain_logic` · `hosted onboarding / request handling`

This file solves a practical onboarding problem: once the system trusts that a user owns an organization email domain, it needs to decide which workspace that organization should use. The rule is: one verified domain should map to one shared workspace. If the workspace already exists, the user joins it. If it does not, the file creates it.

The main piece is SharedWorkspaces. It can check whether a domain already has a workspace, and it can ensure that a workspace exists for a given domain and email address. The workspace ID is deterministic: it is made from the domain name using a UUID based on DNS naming. That means the same domain naturally points to the same ID every time, like using a company name to find the same mailbox.

When creating or joining, the code writes database rows for the workspace, the member, and a default agent. The first member becomes an administrator, so someone can manage billing and setup. Later members are added without that automatic admin status. The code also locks the workspace row while deciding who is first, which prevents two people signing up at the same time from both being treated as the first member.

A safety check in _existing looks for conflicting mappings. If one domain appears to point to more than one workspace, it raises an error instead of guessing.

#### Function details

##### `serve_dsn`  (lines 20–27)

```
def serve_dsn() -> str
```

**Purpose**: This reads the database connection string used by the hosted onboarding path when it must write workspace data as the special serve role. If the setting is missing, it stops immediately with a clear error rather than writing with the wrong permissions.

**Data flow**: It reads the UFO_CONTROL_SERVE_DSN environment variable from the process environment. If a value is present, it returns that string. If no value is set, it raises a RuntimeError explaining that hosted onboarding needs this database connection string.

**Call relations**: This function is a small setup helper for code that needs the serve-role database connection. Nothing in this file calls it directly, but it protects the larger onboarding flow by making a missing required setting fail loudly.


##### `SharedWorkspaces.exists`  (lines 46–47)

```
async def exists(self, domain: str) -> bool
```

**Purpose**: This answers the simple question: does this organization domain already have a shared workspace? It is useful when onboarding needs to decide whether a user is joining an existing organization or starting a new one.

**Data flow**: It receives a domain name, such as example.com. It asks _existing to look up the workspace for that domain. It returns true if _existing found one, and false if it found nothing.

**Call relations**: This is the quick-check entry into the lookup logic. It delegates the real database search to SharedWorkspaces._existing, then turns that result into a yes-or-no answer for callers that do not need the workspace ID.

*Call graph*: calls 1 internal fn (_existing).


##### `SharedWorkspaces.ensure`  (lines 49–100)

```
async def ensure(self, domain: str, email: str) -> EnsuredWorkspace
```

**Purpose**: This makes sure a verified domain has a shared workspace and that the given email address is a member of it. If the workspace is new, it creates the workspace, adds the user as the first administrator, and creates the default agent for that workspace.

**Data flow**: It receives a domain and an email address. First it looks for an existing workspace for the domain; if none is found, it creates the predictable workspace ID from the lowercased domain. It normalizes the email, opens a workspace-scoped database transaction, inserts the workspace if needed, locks that workspace row, checks whether any member already exists, creates or updates the member with admin rights only if they are first, inserts the default main agent if needed, then reads back whether this member is an admin. It returns an EnsuredWorkspace value containing the workspace ID as text and the admin flag.

**Call relations**: This is the main onboarding action in the file. It starts by calling SharedWorkspaces._existing so it does not create duplicates. It then uses the workspace context and transaction helpers to make database changes safely, calls create_member to add the user, and uses PostgreSQL insert helpers so repeated attempts do not create duplicate workspace or agent rows. At the end it hands the caller a compact result that says which workspace was ensured and whether the user can administer it.

*Call graph*: calls 1 internal fn (_existing); 8 external calls (__init__, insert, select, workspace_tx, create_member, ws, uuid4, uuid5).


##### `SharedWorkspaces._existing`  (lines 102–120)

```
async def _existing(self, domain: str) -> UUID | None
```

**Purpose**: This finds the workspace already associated with a domain, if there is one. It also acts as a guardrail: if the domain seems to map to more than one workspace, it raises an error instead of silently choosing the wrong one.

**Data flow**: It receives a domain name and lowercases it. It computes the deterministic workspace UUID for that domain, then queries the database in two ways: first for a workspace with that exact deterministic ID, and second for any workspace whose first member has an email address on that domain. If no rows are found, it returns None. If exactly one row is found, it returns that workspace ID. If multiple rows are found, it raises a RuntimeError because the domain mapping is ambiguous.

**Call relations**: This is the shared lookup helper used by both SharedWorkspaces.exists and SharedWorkspaces.ensure. The yes-or-no path uses it to check for a workspace, while the creation path uses it before deciding whether to reuse an existing workspace or create the deterministic one.

*Call graph*: called by 2 (ensure, exists); 1 external calls (uuid5).


### Signup completion services
After hosted onboarding succeeds, these services create Slack Connect follow-up and issue the hosted gateway authentication token.

### `control/src/ufo_control/gateway_slack_connect.py`

`orchestration` · `background polling after gateway startup`

This file is the background delivery system for Slack Connect onboarding. When a customer finishes the invite-wall signup and creates a workspace, the system records that durable event in the database. This file later notices that record, creates a public channel in UFO's own Slack workspace, and sends a Slack Connect invite to the signup email. Signup does not depend on Slack being fast or available.

The database table is the checklist. A row starts as pending, is temporarily claimed by one gateway worker, then becomes delivered, failed, or pending again for retry. A lease is used like a library checkout slip: one worker owns the row for a short time, and renews that ownership while Slack calls are in progress so another worker does not send a duplicate invite.

The channel name is deterministic, based on the email domain label, so a lost Slack response can often be recovered by looking up the same name later. The code also records that an invite was attempted before making the Slack call. If the response is lost, it checks Slack's own invite and sharing state instead of blindly sending another invite.

The file separates Slack API calling from workflow control. SlackConnectClient knows how to talk to Slack safely and classify errors. SlackConnectInviter knows how to poll the database, claim work, retry temporary failures, and mark permanent failures for operator review.

#### Function details

##### `SlackTransientError.__init__`  (lines 167–169)

```
def __init__(self, message: str, retry_after: float | None=None) -> None
```

**Purpose**: Creates an error for Slack problems that may go away, such as network trouble, rate limiting, or a temporary Slack outage. It can also carry a suggested wait time before retrying.

**Data flow**: It receives an error message and, optionally, a retry-after number of seconds. It stores the message as the exception text and saves the retry delay on the error object so later code can schedule the next attempt.

**Call relations**: SlackConnectClient._call raises this when a Slack request fails in a way that should be retried. Later, the delivery flow catches this kind of error and reschedules the database row instead of marking it permanently failed.

*Call graph*: called by 1 (_call).


##### `rearm_failed_delivery`  (lines 188–203)

```
async def rearm_failed_delivery(pool: asyncpg.Pool, onboard_claim_id: UUID) -> datetime | None
```

**Purpose**: Lets an operator retry one failed Slack Connect delivery after the underlying problem has been fixed. It only reopens failed rows; delivered rows are left alone.

**Data flow**: It receives a database pool and an onboarding claim ID. It looks for a matching row whose state is failed, resets its retry fields back to a fresh pending state, and returns the time it had previously been updated. If no failed row was changed, it returns nothing.

**Call relations**: This is an operator recovery hook outside the normal poll loop. It talks directly to the database through asyncpg.Pool.fetchval and does not call Slack, so it simply puts work back where SlackConnectInviter can pick it up later.

*Call graph*: 1 external calls (fetchval).


##### `SlackConnectClient.team_id`  (lines 215–216)

```
async def team_id(self) -> str
```

**Purpose**: Asks Slack which workspace the configured bot token belongs to. The inviter uses this as a safety check before creating or inviting anything.

**Data flow**: It sends an auth.test request to Slack, receives Slack's response, and extracts the team_id text from it. The result is the Slack workspace ID tied to the token.

**Call relations**: It uses SlackConnectClient._call to make the Slack API request and SlackConnectClient._text to read the needed field. SlackConnectInviter._verify_team relies on this check before any channel is changed.

*Call graph*: calls 2 internal fn (_call, _text).


##### `SlackConnectClient.create_channel`  (lines 218–220)

```
async def create_channel(self, name: str) -> str
```

**Purpose**: Creates the public Slack channel that will be used for the customer's Slack Connect relationship. The channel name is supplied by the database workflow.

**Data flow**: It receives a channel name, sends it to Slack's conversations.create API, and extracts the new channel ID from Slack's reply. The returned channel ID is later saved in the delivery row.

**Call relations**: It delegates the HTTP work to SlackConnectClient._call and extracts the channel ID with SlackConnectClient._text. The channel-opening step of the inviter uses this during delivery.

*Call graph*: calls 2 internal fn (_call, _text).


##### `SlackConnectClient.channel_id_by_name`  (lines 222–243)

```
async def channel_id_by_name(self, name: str) -> str
```

**Purpose**: Finds an existing Slack channel by its exact name, including archived channels. This is how the system recovers if Slack says the deterministic channel name is already taken.

**Data flow**: It receives a channel name, pages through Slack's public-channel list, and compares each returned channel's name to the requested name. If it finds a match, it returns that channel's ID; if not, it raises a permanent error because Slack's state is inconsistent with the earlier name-taken result.

**Call relations**: It uses SlackConnectClient._call for each Slack list request and SlackConnectClient._text to pull out the ID. It is part of the recovery path after channel creation reports a taken name.

*Call graph*: calls 2 internal fn (_call, _text); 1 external calls (__init__).


##### `SlackConnectClient.outgoing_invite_id`  (lines 245–276)

```
async def outgoing_invite_id(self, channel_id: str) -> str | None
```

**Purpose**: Checks whether this channel already has a live outgoing Slack Connect invitation. This prevents a second invitation when an earlier Slack response was lost.

**Data flow**: It receives a channel ID, pages through Slack's outgoing Connect invitations, and looks for entries for that channel. If it finds a live invitation, it returns the invite ID; if it only finds dead invitations, it returns nothing; if Slack shows an unknown status or too many pages, it raises a permanent review-needed error.

**Call relations**: It calls SlackConnectClient._call to read Slack's invite list and SlackConnectClient._text to extract a live invite ID. SlackConnectInviter._invite uses it during reconciliation for rows where an invite was already attempted.

*Call graph*: calls 2 internal fn (_call, _text); 1 external calls (__init__).


##### `SlackConnectClient.is_externally_shared`  (lines 278–281)

```
async def is_externally_shared(self, channel_id: str) -> bool
```

**Purpose**: Checks whether a Slack channel is already shared or pending sharing with an external workspace. This is another recovery signal after a possibly lost invite response.

**Data flow**: It receives a channel ID, asks Slack for channel details, and reads the sharing flags from the returned channel object. It returns true if Slack says the channel is externally shared or pending external sharing, otherwise false.

**Call relations**: It relies on SlackConnectClient._call to fetch channel information. SlackConnectInviter._invite uses this after no live invite is found, to decide whether delivery may already have succeeded.

*Call graph*: calls 1 internal fn (_call).


##### `SlackConnectClient.invite_shared`  (lines 283–290)

```
async def invite_shared(self, channel_id: str, email: str) -> str
```

**Purpose**: Sends the actual Slack Connect invitation email for a channel. It also blocks impossible recipient emails before asking Slack.

**Data flow**: It receives a channel ID and an email address. If the email is too long, it raises a permanent error; otherwise it calls Slack's conversations.inviteShared API and returns Slack's invitation ID from the response.

**Call relations**: It uses SlackConnectClient._call for the Slack request and SlackConnectClient._text for the invite ID. SlackConnectInviter._invite calls it only after recording that an invite attempt is about to happen.

*Call graph*: calls 2 internal fn (_call, _text); 1 external calls (__init__).


##### `SlackConnectClient.redact`  (lines 292–293)

```
def redact(self, message: str) -> str
```

**Purpose**: Removes the bot token from error text before that text is stored or logged. This keeps secrets out of logs and database fields.

**Data flow**: It receives a message string, replaces any occurrence of the bot token with a placeholder, and cuts the result down to a safe maximum length. The returned text is safe to record as an error summary.

**Call relations**: The retry and failure paths use this before writing errors. It does not call other project functions; it is a small safety helper inside the Slack client.


##### `SlackConnectClient._call`  (lines 295–323)

```
async def _call(self, method: str, params: dict[str, str | int]) -> dict[str, Any]
```

**Purpose**: Performs one Slack Web API request and turns Slack or network failures into clear error types. This is the central gate for all outbound Slack calls.

**Data flow**: It receives a Slack method name and request parameters. It sends an authenticated HTTP POST, removes empty parameters, parses the JSON response, and returns the payload if Slack says ok. Network errors, rate limits, server errors, known temporary Slack errors, name conflicts, and permanent Slack errors are converted into the matching exceptions.

**Call relations**: All higher-level SlackConnectClient methods call this instead of doing HTTP themselves. It uses httpx.AsyncClient for the network request, _retry_after for rate-limit timing, SlackTransientError.__init__ for retryable failures, and SlackNameTakenError or SlackTerminalError for non-retryable cases.

*Call graph*: calls 2 internal fn (__init__, _retry_after); called by 6 (channel_id_by_name, create_channel, invite_shared, is_externally_shared, outgoing_invite_id, team_id); 3 external calls (__init__, __init__, AsyncClient).


##### `SlackConnectClient._text`  (lines 325–331)

```
def _text(self, payload: dict[str, Any], *path: str) -> str
```

**Purpose**: Safely extracts a required text field from a nested Slack response. It treats missing fields as a permanent Slack response problem.

**Data flow**: It receives a response dictionary and a path of keys to follow. It walks through the nested dictionaries and returns the final value as text. If any key is missing or the shape is not what was expected, it raises a permanent error.

**Call relations**: The Slack client methods use this after SlackConnectClient._call has returned a successful payload. It creates a SlackTerminalError when Slack's response lacks data the workflow needs.

*Call graph*: called by 5 (channel_id_by_name, create_channel, invite_shared, outgoing_invite_id, team_id); 1 external calls (__init__).


##### `_retry_after`  (lines 334–338)

```
def _retry_after(response: httpx.Response) -> float | None
```

**Purpose**: Reads Slack's rate-limit wait time from an HTTP response. It keeps that wait bounded so one response cannot pause retries for an excessive time.

**Data flow**: It receives an HTTP response, checks the retry-after header, and returns a number of seconds if the header is a simple number. If the header is missing or invalid, it returns nothing; if it is too large, it returns the configured maximum.

**Call relations**: SlackConnectClient._call uses this when Slack returns HTTP 429, meaning too many requests. The result is stored on SlackTransientError so the database row can be retried later.

*Call graph*: called by 1 (_call).


##### `SlackConnectInviter.run`  (lines 364–388)

```
async def run(self) -> None
```

**Purpose**: Runs the Slack Connect delivery poller forever. It keeps the background task alive even when one sweep fails, because stopping it would strand later signups.

**Data flow**: It starts with a zero failure count, repeatedly calls poll, and sleeps only when no row was claimed. If a non-cancellation error happens, it logs the growing failure count and keeps going; if the task is cancelled, it exits normally by re-raising the cancellation.

**Call relations**: This is the long-lived loop that calls SlackConnectInviter.poll. It uses asyncio.sleep between quiet polling rounds so the service does not spin when there is no work.

*Call graph*: calls 1 internal fn (poll); 1 external calls (sleep).


##### `SlackConnectInviter.poll`  (lines 390–405)

```
async def poll(self) -> bool
```

**Purpose**: Performs one sweep of the delivery system: discover eligible signups, claim one due row, and try to move it forward. It returns whether it actually claimed work.

**Data flow**: It first materializes completed signup claims into delivery rows. Then it tries to claim one pending or expired row. If none is available, it returns false; otherwise it starts a lease-renewal task, advances the delivery, cancels the renewal task, waits for cleanup, and returns true.

**Call relations**: SlackConnectInviter.run calls this on every loop. This function coordinates SlackConnectInviter._materialize, _claim, _renew_lease, and _advance, using asyncio.create_task and asyncio.gather to keep the lease alive while the Slack work runs.

*Call graph*: calls 4 internal fn (_advance, _claim, _materialize, _renew_lease); called by 1 (run); 2 external calls (create_task, gather).


##### `SlackConnectInviter._materialize`  (lines 407–421)

```
async def _materialize(self) -> None
```

**Purpose**: Creates delivery-table rows for completed signup claims that have earned a Slack Connect invite. It does this in a way that one bad or duplicate channel name does not block everyone else.

**Data flow**: It opens a database transaction, takes a PostgreSQL advisory lock, and runs the materialization insert query. Eligible signup records become pending delivery rows, while conflicts are skipped rather than crashing the whole batch.

**Call relations**: SlackConnectInviter.poll calls this before claiming work. It does not hand off to other project functions; its job is to make sure the database queue reflects completed signups.

*Call graph*: called by 1 (poll).


##### `SlackConnectInviter._claim`  (lines 423–435)

```
async def _claim(self) -> _Delivery | None
```

**Purpose**: Claims one due delivery row for this worker. Claiming marks that this gateway replica owns the row for now.

**Data flow**: It runs the claim query with the worker ID and lease length. If no row is due, it returns nothing. If a row is claimed, it builds a _Delivery object containing the claim ID, email, channel details, invitation details, attempt time, and attempt count.

**Call relations**: SlackConnectInviter.poll calls this after materialization. When it returns a _Delivery, poll starts lease renewal and passes that delivery to _advance.

*Call graph*: called by 1 (poll); 1 external calls (__init__).


##### `SlackConnectInviter._renew_lease`  (lines 437–459)

```
async def _renew_lease(self, onboard_claim_id: UUID) -> None
```

**Purpose**: Keeps this worker's claim on a delivery row alive while Slack calls are in progress. This reduces the chance that another gateway replica will pick the same row and send a duplicate invite.

**Data flow**: It receives an onboarding claim ID, sleeps for the renewal interval, and then updates the row's lease expiration if the same worker still owns it. It repeats until cancelled, logging database renewal failures but continuing to try.

**Call relations**: SlackConnectInviter.poll starts this as a background task while _advance runs, then cancels it afterward. It uses asyncio.sleep between renewal attempts.

*Call graph*: called by 1 (poll); 1 external calls (sleep).


##### `SlackConnectInviter._advance`  (lines 461–488)

```
async def _advance(self, delivery: _Delivery) -> None
```

**Purpose**: Moves one claimed delivery row through the full Slack workflow: verify token, create or recover the channel, send or reconcile the invite, then mark the row delivered. It is the main delivery decision point.

**Data flow**: It receives a _Delivery snapshot. It verifies the Slack token's team, obtains a channel ID if the row lacks one, obtains an invitation ID or proof of sharing if needed, and writes delivered state on success. Temporary errors are rescheduled, permanent errors are marked failed, unexpected errors are logged and failed, and lost leases are allowed to bubble up.

**Call relations**: SlackConnectInviter.poll calls this after claiming a row. It coordinates _verify_team, _open_channel, _invite, _reschedule, _fail, and _write to move the row to its next database state.

*Call graph*: calls 6 internal fn (_fail, _invite, _open_channel, _reschedule, _verify_team, _write); called by 1 (poll).


##### `SlackConnectInviter._verify_team`  (lines 490–498)

```
async def _verify_team(self) -> None
```

**Purpose**: Checks that the Slack bot token belongs to the expected operator workspace before any Slack channel is changed. This protects against a misconfigured token creating channels in the wrong Slack workspace.

**Data flow**: It asks Slack for the token's team ID and compares it with the configured expected team ID. If they differ, it raises a configuration error; otherwise it returns without changing anything.

**Call relations**: SlackConnectInviter._advance calls this as its first safety gate. When it raises SlackConnectConfigError, the advance flow treats that as a permanent failure for the row.

*Call graph*: called by 1 (_advance); 1 external calls (__init__).


##### `SlackConnectInviter._open_channel`  (lines 500–506)

```
async def _open_channel(self, delivery: _Delivery) -> str
```

**Purpose**: Creates the customer's operator-workspace Slack channel, or recovers the existing channel ID if Slack says the chosen name is already taken. It then records the channel ID in the database.

**Data flow**: It receives a delivery with a deterministic channel name. It asks Slack for a channel ID through the client path, handles the name-taken recovery case, writes the channel ID to the delivery row, and returns that ID.

**Call relations**: SlackConnectInviter._advance calls this when the delivery row does not yet have a channel ID. It hands the saved channel ID back to _advance so the invite step can use it, and it uses _write to persist the result only if this worker still owns the lease.

*Call graph*: calls 1 internal fn (_write); called by 1 (_advance).


##### `SlackConnectInviter._invite`  (lines 508–525)

```
async def _invite(self, delivery: _Delivery, channel_id: str) -> str | None
```

**Purpose**: Sends the Slack Connect invite, but first reconciles any earlier uncertain attempt. This is the part that avoids blind duplicate invitations.

**Data flow**: It receives the delivery and channel ID. If an invite was previously attempted, it checks Slack for a live outgoing invite or for an already externally shared channel. If neither is found, it records a fresh attempt time, sends the invitation through Slack, saves the invitation ID, and returns it.

**Call relations**: SlackConnectInviter._advance calls this after a channel ID is available. It uses _write to record the attempt time and _persist_invitation to save Slack's invitation ID when one is known.

*Call graph*: calls 2 internal fn (_persist_invitation, _write); called by 1 (_advance).


##### `SlackConnectInviter._persist_invitation`  (lines 527–529)

```
async def _persist_invitation(self, delivery: _Delivery, invitation_id: str) -> str
```

**Purpose**: Stores Slack's invitation ID on the delivery row. This gives the system a durable record of the specific invite Slack created.

**Data flow**: It receives a delivery and an invitation ID. It writes that ID to the database row and then returns the same ID to its caller.

**Call relations**: SlackConnectInviter._invite calls this after finding or creating a live invitation. It relies on _write so the update only succeeds while this worker still owns the row.

*Call graph*: calls 1 internal fn (_write); called by 1 (_invite).


##### `SlackConnectInviter._reschedule`  (lines 531–550)

```
async def _reschedule(self, delivery: _Delivery, error: SlackTransientError) -> None
```

**Purpose**: Puts a delivery row back into pending state after a temporary Slack problem, with a delay before the next try. After too many tries, it turns the row into a failure instead.

**Data flow**: It receives the delivery and a temporary error. If the attempt count has reached the limit, it fails the row. Otherwise it chooses a delay from Slack's retry-after value or an exponential backoff, writes the row as pending with next_attempt_at set in the future, and stores a redacted error message.

**Call relations**: SlackConnectInviter._advance calls this when it catches SlackTransientError. It may call _fail when retries are exhausted, or _write when scheduling another attempt.

*Call graph*: calls 2 internal fn (_fail, _write); called by 1 (_advance); 1 external calls (timedelta).


##### `SlackConnectInviter._fail`  (lines 552–563)

```
async def _fail(self, delivery: _Delivery, error: Exception) -> None
```

**Purpose**: Marks a delivery row as failed for operator review. This is used when the problem is permanent or when retries are exhausted.

**Data flow**: It receives the delivery and an error. It redacts the error message, writes failed state to the database, clears lease and retry timing fields, and logs the failure.

**Call relations**: SlackConnectInviter._advance calls this for permanent or unexpected errors, and _reschedule calls it when the retry limit has been reached. It uses _write so a worker cannot overwrite a row it no longer owns.

*Call graph*: calls 1 internal fn (_write); called by 2 (_advance, _reschedule).


##### `SlackConnectInviter._write`  (lines 565–574)

```
async def _write(self, onboard_claim_id: UUID, assignment: str, *values: object) -> None
```

**Purpose**: Writes a change to a delivery row only if this worker still owns the lease. This is the guardrail that prevents stale workers from changing rows claimed by someone else.

**Data flow**: It receives a claim ID, a SQL assignment fragment, and optional values for that assignment. It updates the matching row only when both the claim ID and worker ID match, and refreshes updated_at. If no row was updated, it raises a lease-lost error.

**Call relations**: The main delivery steps call this whenever they persist progress: _advance, _open_channel, _invite, _persist_invitation, _reschedule, and _fail. If it raises _LeaseLost, the poll flow abandons the writeback path.

*Call graph*: called by 6 (_advance, _fail, _invite, _open_channel, _persist_invitation, _reschedule); 1 external calls (__init__).


##### `slack_connect_from_env`  (lines 577–592)

```
def slack_connect_from_env(pool: asyncpg.Pool) -> SlackConnectInviter | None
```

**Purpose**: Builds the Slack Connect inviter from environment variables at startup, or returns nothing when the feature is disabled. It fails clearly if the feature is enabled but required settings are missing or malformed.

**Data flow**: It receives a database pool, reads the enable switch from the environment, and either returns None, creates a SlackConnectClient and SlackConnectInviter, or raises an error for an invalid boolean. For enabled mode, it reads the bot token and expected Slack team ID and creates a worker ID from hostname and process ID.

**Call relations**: Startup code can call this to decide whether to launch the background poller. It uses _require_env for mandatory settings, constructs SlackConnectClient and SlackConnectInviter, and uses socket.gethostname and os.getpid for the worker identity.

*Call graph*: calls 1 internal fn (_require_env); 4 external calls (__init__, __init__, getpid, gethostname).


##### `_require_env`  (lines 595–599)

```
def _require_env(name: str) -> str
```

**Purpose**: Reads a required environment variable and gives a clear startup error if it is missing. This prevents a half-configured Slack Connect setup from reaching Slack.

**Data flow**: It receives an environment variable name, looks it up, and returns its value if present and non-empty. If the value is absent, it raises a runtime error explaining that the variable is required when Slack Connect delivery is enabled.

**Call relations**: slack_connect_from_env calls this for the bot token and expected team ID. It is a small configuration helper used only during inviter construction.

*Call graph*: called by 1 (slack_connect_from_env).


### `control/src/ufo_control/gateway_token.py`

`domain_logic` · `token issuance during member setup or credential refresh`

This file is a small but important wrapper around token creation. In plain terms, it makes the “membership pass” that a client stores locally and later shows to prove who they are. The pass is not just text: it is signed with HMAC, which means it carries a tamper-evident seal made from a shared secret. If someone changes the workspace or email inside the token, verification should fail later.

The file sets two project-level rules. First, gateway member tokens last 30 days. Second, the secret used to sign them is expected to come from the environment variable named `UFO_TOKEN_SECRET`. The actual token format is not invented here. Instead, this file calls the shared `ufo.bearer` token maker. That matters because both the code that creates tokens and the code that verifies them must agree on the exact shape of the signed data. Using one shared codec is like having one official ticket printer and one official ticket scanner; they stay compatible because they follow the same design.

Without this wrapper, different parts of the system might accidentally choose different expiry rules or token formats, causing valid users to be rejected or unsafe tokens to be accepted.

#### Function details

##### `mint_token`  (lines 13–14)

```
def mint_token(secret: str, workspace_id: str, email: str, now: datetime | None=None) -> str
```

**Purpose**: Creates a gateway bearer token for one workspace member. A bearer token is a credential that grants access to whoever presents it, so this function signs the workspace ID and email with a secret and gives it a fixed 30-day lifetime.

**Data flow**: It receives a signing secret, a workspace ID, an email address, and optionally the current time. It adds this file’s standard 30-day expiry rule, then passes all of that to the shared bearer-token creator. The result is a token string that can be stored by the client and later checked by gateway-facing surfaces.

**Call relations**: When some higher-level gateway or control flow needs to issue credentials for a hosted member, it calls this function instead of building a token by hand. This function immediately hands the real signing work to `ufo.bearer.mint_token`, so token creation stays consistent with the verifier that understands the same bearer-token format.

*Call graph*: 1 external calls (mint_token).

## 📊 State Registers Touched

- `reg-workspace-tenant-state` — The saved customer workspace boundary, including its owners, admins, limits, main agent, and tenant separation rules.
- `reg-identity-auth-state` — The current proof of who is calling, such as member identity, cookies, bearer tokens, operator sessions, and signed access tokens.
- `reg-onboarding-claims-invites` — The temporary signup claims, email verification codes, invite records, and hosted gateway tokens used to admit new users.
- `reg-hosted-domain-routing-state` — The hosted-control mapping from verified company domains and invite policy to the shared workspace a signer should join.
- `reg-member-seat-state` — The durable workspace membership and seat-assignment state used to decide who belongs, who is an admin, and whether a member may admit or run work.
