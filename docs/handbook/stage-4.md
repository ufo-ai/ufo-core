# Onboarding, workspace creation, and member provisioning  `stage-4`

This stage is the front door for a hosted UFO user. It runs during startup for a new person or team: proving they are allowed in, creating or finding their workspace, adding them as a member, and handing back the address and bearer token, which is the secret pass they use to connect later.

The main web server is built in gateway.py. It guides the user through installing the client, entering an email, using an invite if required, and receiving their connection details. gateway_claim.py manages the email proof: it creates a short-lived code and rejects expired or repeatedly wrong attempts. gateway_email.py decides whether an address looks like a work email and sends the code, either through Amazon SES or to the local log in development. gateway_invite.py checks one-time invite codes. gateway_directives.py formats simple instructions sent to the terminal client. gateway_web.py presents the same flow in a browser. gateway_shared.py links a verified email domain to one shared workspace, creates it when needed, adds the member, and ensures a default agent exists. core/src/ufo/onboarding.py performs the deeper first-run workspace setup.

## Files in this stage

### Onboarding gateway
The public gateway coordinates the hosted signup conversation and delegates verification, invite, and workspace setup steps.

### `control/src/ufo_control/gateway.py`

`entrypoint` · `startup, request handling, shutdown`

This file is the front door for hosted UFO onboarding. Without it, new users could not download the client script, sign in from the terminal or web page, prove that they own a work email address, or be placed into the right shared workspace.

The server is a FastAPI app, which means it defines HTTP routes such as health checks, script download, login page, and onboarding endpoints. The central idea is a short conversation with the user. First, the server asks for a work email. Then it sends and checks a verification code. After that, it either finds an existing workspace for that email domain or creates one. If new workspaces require invite codes, it pauses and asks for an invite before creating the workspace. Finally, it returns small “directives,” which are simple commands for the client, such as “say this message,” “ask this question,” “store this token,” or “use this workspace URL.”

The file also sets up shared resources at startup: database connections, onboarding storage tables, invite-code storage, email sending, workspace resolution, and secrets used to mint login tokens. On shutdown it closes those resources. A few guardrails keep public requests small and predictable, so a bad or accidental request cannot send unlimited data into the onboarding flow.

#### Function details

##### `_stamp_script`  (lines 60–64)

```
def _stamp_script(text: str) -> str
```

**Purpose**: This prepares the downloadable client script before the server starts serving it. It replaces the development version marker with a short fingerprint of the script text, and fills in the public base URL the client should contact.

**Data flow**: It takes the raw script text as input. It hashes the text to make a short version label, reads the public base URL from the environment or uses a default, then substitutes both values into the script. It returns the finished script text that users download.

**Call relations**: This runs while the module is being loaded, before requests arrive. The `/ufo` route later serves the stamped result directly, so each downloader gets a script with a stable version and the right server address.

*Call graph*: 1 external calls (sha1).


##### `Onboarding.advance`  (lines 80–86)

```
async def advance(self, channel: str, session: str, body: str, install: bytes) -> bytes
```

**Purpose**: This is the main stepper for the onboarding conversation. Given a user’s channel, session, and latest reply, it decides what question or result should come next.

**Data flow**: It receives the channel name, session ID, user text, and any install instructions to include. It looks up any live claim for that channel and session. If there is no claim yet, it starts email collection; if the email is not verified, it checks the code; if verification is done, it resolves the workspace and signs the user in. It returns bytes containing client directives.

**Call relations**: The web and terminal onboarding routes call this for each request. It acts like a receptionist: it checks where the user is in the process, then sends the work to `_collect_email`, `_verify_code`, or `_resolve`.

*Call graph*: calls 3 internal fn (_collect_email, _resolve, _verify_code).


##### `Onboarding._collect_email`  (lines 88–106)

```
async def _collect_email(self, channel: str, session: str, body: str, install: bytes) -> bytes
```

**Purpose**: This handles the first part of onboarding: asking for a work email and starting a verification claim. It also turns validation or email-sending problems into friendly prompts.

**Data flow**: It receives the channel, session, user input, and install bytes. If the user has not typed anything, it returns directives that introduce UFO and ask for an email. If there is input, it tries to start a claim using that email. On failure it returns an error message and asks again; on success it tells the user a code was emailed and asks for that code.

**Call relations**: Only `Onboarding.advance` sends users here, when no live claim exists yet. This function does not finish onboarding; it creates the first stored claim and returns the conversation to the caller for the next user reply.

*Call graph*: called by 1 (advance); 2 external calls (directive, render).


##### `Onboarding._verify_code`  (lines 108–115)

```
async def _verify_code(self, claim: OnboardClaim, body: str, install: bytes) -> bytes
```

**Purpose**: This checks the emailed verification code for an existing onboarding claim. It keeps the user in the code-entry step until the code is accepted.

**Data flow**: It receives the stored claim, the user’s latest text, and install bytes. It asks the claim workflow to verify the code. If verification fails, it returns a message explaining the problem and asks for the code again. If it succeeds, it immediately continues into workspace resolution and returns that result.

**Call relations**: `Onboarding.advance` calls this after a claim exists but has not yet been verified. When the code is correct, this function hands off to `_resolve`, so the user can move directly from verification to workspace setup without making another request.

*Call graph*: calls 1 internal fn (_resolve); called by 1 (advance); 2 external calls (directive, render).


##### `Onboarding._resolve`  (lines 117–132)

```
async def _resolve(self, claim: OnboardClaim, answer: str | None, install: bytes) -> bytes
```

**Purpose**: This turns a verified email claim into an actual workspace login. It enforces invite-code rules for brand-new workspaces, creates or finds the workspace, marks the claim complete, and signs the user in.

**Data flow**: It receives a verified claim, the user’s latest answer if any, and install bytes. If invites are required and there is no workspace for the email domain yet, it runs the invite gate. Once the gate is clear, it ensures a workspace exists for the email domain, records the completed claim with that workspace ID, and returns signed-in directives with a token and workspace URL.

**Call relations**: This is reached from `Onboarding.advance` for already verified claims and from `_verify_code` immediately after successful code verification. It may call `_invite_gate` first, then finishes through `_signed_in`.

*Call graph*: calls 2 internal fn (_invite_gate, _signed_in); called by 2 (_verify_code, advance); 1 external calls (directive).


##### `Onboarding._invite_gate`  (lines 134–175)

```
async def _invite_gate(self, claim: OnboardClaim, answer: str | None, install: bytes) -> bytes | InviteAccepted | None
```

**Purpose**: This checks whether a verified user is allowed to create a new workspace when invite codes are required. It asks for a code, accepts a valid one, or explains why a code cannot be used.

**Data flow**: It receives the claim, the user’s possible invite answer, and install bytes. If the claim already has an invite attached, it lets the flow continue. If no answer was given, it asks for an invite. If a code was given, it tries to redeem it and returns either an accepted invite record or directives explaining that the code expired, was already used, was unknown, or should be tried again.

**Call relations**: `_resolve` calls this only when a new workspace would be created under an invite-required setup. It either blocks the flow with another prompt or hands back an accepted invite so `_resolve` can continue.

*Call graph*: called by 1 (_resolve); 2 external calls (directive, render).


##### `Onboarding._signed_in`  (lines 177–196)

```
def _signed_in(self, claim: OnboardClaim, workspace_id: str, install: bytes, accepted: bytes) -> bytes
```

**Purpose**: This creates the final successful onboarding response. It gives the client its login token, workspace address, and a signed-in message.

**Data flow**: It receives the completed claim, workspace ID, install bytes, and any message about an accepted invite. It mints a token for the user and workspace, checks whether the email domain belongs to the operator, and renders directives for the client. Operator-domain users also receive a debugger URL; other users do not.

**Call relations**: `_resolve` calls this after the workspace is ready and the claim is complete. It is the last step in the onboarding path and returns the data the terminal or web client needs to continue as a signed-in user.

*Call graph*: called by 1 (_resolve); 3 external calls (directive, render, mint_token).


##### `GatewayState.healthy`  (lines 206–214)

```
async def healthy(self) -> bool
```

**Purpose**: This checks whether the gateway is connected to the database using the expected roles. It is used by the health-check endpoint so deployments can tell whether the service is really ready.

**Data flow**: It reads from the owner database pool and from a workspace transaction, asking each connection which database user it is running as. If either check fails, it logs the failure and returns false. If both roles match the expected startup values, it returns true.

**Call relations**: The `/healthz` route calls this during health checks. It uses the normal database access paths, so the health result reflects whether the gateway can actually talk to the database in the way later requests need.

*Call graph*: 2 external calls (text, workspace_tx).


##### `_require_env`  (lines 217–221)

```
def _require_env(name: str) -> str
```

**Purpose**: This reads a required environment variable and fails loudly if it is missing. It prevents the gateway from starting with missing secrets or URLs.

**Data flow**: It receives the variable name. It looks that name up in the process environment. If a non-empty value is present, it returns it; otherwise it raises a startup error explaining which setting is missing.

**Call relations**: The startup lifespan uses this when building `Onboarding`, especially for the workspace base URL and token secret. This keeps configuration mistakes from becoming confusing runtime failures during a user’s sign-in.

*Call graph*: called by 1 (lifespan).


##### `_invite_required`  (lines 224–233)

```
def _invite_required() -> bool
```

**Purpose**: This decides whether creating a new workspace must be protected by invite codes. Its default is safe: invites are required unless the deployment explicitly disables them.

**Data flow**: It reads the invite-required environment setting, normalizes the text, and accepts true/false-style values. It returns a boolean. If the value is unclear, it raises an error instead of guessing.

**Call relations**: The startup lifespan calls this before building the onboarding object. `_resolve` later uses the resulting setting to decide whether to run the invite gate for new workspaces.

*Call graph*: called by 1 (lifespan).


##### `_dsn_role`  (lines 236–240)

```
def _dsn_role(dsn: str) -> str
```

**Purpose**: This extracts the database role name from a database connection string. The gateway uses that role name later to confirm its database connections are what it expected.

**Data flow**: It receives a database DSN, meaning a connection string. It parses the string and reads the username part. If there is no username, it raises an error; otherwise it returns the role name.

**Call relations**: The startup lifespan calls this for both owner and serving database URLs. Those expected role names are stored in `GatewayState`, and `GatewayState.healthy` compares live connections against them.

*Call graph*: called by 1 (lifespan).


##### `_request_body`  (lines 243–249)

```
async def _request_body(request: Request) -> str
```

**Purpose**: This safely reads a request body as short text. It protects onboarding endpoints from overly large submissions.

**Data flow**: It receives a FastAPI request. It reads the body in chunks, keeping only up to the allowed size plus one byte to detect overflow. If the body is too large, it raises an input error. Otherwise it decodes the bytes as UTF-8 text, replaces invalid characters, trims surrounding whitespace, and returns the string.

**Call relations**: Both onboarding routes use this before sending user input into the onboarding conversation. If it raises an input error, those routes turn the problem into a clear client directive or JSON response instead of letting the request crash.

*Call graph*: called by 2 (onboard, onboard_web); 2 external calls (__init__, stream).


##### `gateway_app`  (lines 252–366)

```
def gateway_app() -> FastAPI
```

**Purpose**: This builds the FastAPI web application for the gateway. It defines the startup and shutdown behavior, then registers every public route the onboarding server exposes.

**Data flow**: It creates an empty state slot, defines a lifespan function that will fill and clear that state, creates the FastAPI app, attaches route functions, and returns the finished app object. At the bottom of the file, that returned app becomes the ASGI application served by the web server.

**Call relations**: This is the file’s top-level assembly point. The nested route functions depend on the shared state created by `gateway_app.lifespan`, and outside infrastructure talks to the returned FastAPI app to serve requests.

*Call graph*: 1 external calls (FastAPI).


##### `gateway_app.lifespan`  (lines 256–296)

```
async def lifespan(app: FastAPI)
```

**Purpose**: This is the startup and shutdown routine for the gateway app. It opens database connections, prepares storage, builds the onboarding services, and cleans everything up when the server stops.

**Data flow**: On startup, it reads database URLs, creates an owner database pool, ensures onboarding and invite tables exist, initializes the serving database connection, reads required settings, and stores a `GatewayState` containing all shared services. It then lets the app serve requests. On shutdown, it clears the state, disposes the database setup, and closes the pool.

**Call relations**: FastAPI calls this around the app’s lifetime. Every route that needs shared resources relies on the state this function creates, while helper functions like `_require_env`, `_invite_required`, and `_dsn_role` keep startup configuration explicit and checked.

*Call graph*: calls 3 internal fn (_dsn_role, _invite_required, _require_env); 14 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, create_pool, dispose_db, init_db (+4 more)).


##### `gateway_app.healthz`  (lines 301–304)

```
async def healthz() -> Response
```

**Purpose**: This answers health-check requests. It tells load balancers or deployment tools whether the gateway is ready to serve real traffic.

**Data flow**: It looks at the shared state and asks it to run a database role check. If there is no state or the check fails, it returns a JSON response with status `unavailable` and HTTP status 503. If everything is healthy, it returns `ok`.

**Call relations**: This route depends on `GatewayState.healthy` for the real readiness test. It is usually called by monitoring systems rather than end users.

*Call graph*: 1 external calls (JSONResponse).


##### `gateway_app.serve_script`  (lines 307–308)

```
async def serve_script() -> Response
```

**Purpose**: This serves the UFO client installer script. It is what a user or install command downloads from `/ufo`.

**Data flow**: It takes no request body. It returns the already stamped script text with a shell-script media type, so clients treat it as a script rather than ordinary JSON or HTML.

**Call relations**: It uses the `STAMPED_SCRIPT` prepared at module load time by `_stamp_script`. This keeps request handling fast because the script does not need to be reread or restamped for every download.

*Call graph*: 1 external calls (PlainTextResponse).


##### `gateway_app.fleet`  (lines 311–314)

```
async def fleet() -> Response
```

**Purpose**: This reports a small public count of existing workspaces, named `craft` in the response. It is a lightweight status-style endpoint.

**Data flow**: It reads the shared database pool from state, asks the database how many rows are in the workspace table, and returns that number as JSON.

**Call relations**: This route is only available after lifespan startup has filled the shared state. It talks directly to the database pool rather than going through the onboarding conversation.

*Call graph*: 1 external calls (JSONResponse).


##### `gateway_app.login`  (lines 317–318)

```
async def login() -> Response
```

**Purpose**: This serves the browser login page. It gives web users the HTML page that can drive the onboarding flow through the web endpoint.

**Data flow**: It takes the incoming GET request and returns the stored login-page HTML as an HTML response. It does not read the database or user input.

**Call relations**: The page returned here is paired with `gateway_app.onboard_web`, which performs the actual step-by-step onboarding conversation for browser clients.

*Call graph*: 1 external calls (HTMLResponse).


##### `gateway_app.onboard_web`  (lines 321–336)

```
async def onboard_web(request: Request) -> Response
```

**Purpose**: This is the JSON-based onboarding endpoint for the browser login flow. It reads the user’s session and reply, advances onboarding, and returns structured directives for the web page.

**Data flow**: It requires an `x-ufo-session` header, checks that the session is not too long, reads a small request body, and passes the web channel, session, and body into `state.onboarding.advance`. If input is bad or something unexpected fails, it creates error directives instead. It parses the directive bytes into JSON-friendly data and returns them.

**Call relations**: The browser login page calls this while the user signs in. It shares the same onboarding engine as the terminal endpoint, but converts the final directives into JSON because web code is easier to drive from structured data.

*Call graph*: calls 1 internal fn (_request_body); 5 external calls (__init__, JSONResponse, directive, render, parse_directives).


##### `gateway_app.onboard`  (lines 339–364)

```
async def onboard(channel: str, request: Request) -> Response
```

**Purpose**: This is the plain-text onboarding endpoint for terminal or channel-based clients. It returns the directive stream in the simple text format those clients expect.

**Data flow**: It receives a channel from the URL, reads the `x-ufo-session` header, decides whether first-run install directives should be included, checks channel and session length, reads a small body, and advances onboarding. Missing or invalid input becomes text directives that tell the client what went wrong and exit. A successful step returns the next onboarding directives as plain text.

**Call relations**: Terminal clients call this endpoint during installation and sign-in. It uses `_request_body` for safe input reading, `first_run_install` to add install-time instructions when needed, and the shared `Onboarding.advance` flow to decide what the client should show or store next.

*Call graph*: calls 1 internal fn (_request_body); 5 external calls (__init__, PlainTextResponse, directive, first_run_install, render).


### Signup verification gates
These modules validate work-email eligibility, issue and check short-lived email claims, and enforce one-time invite codes.

### `control/src/ufo_control/gateway_claim.py`

`domain_logic` · `onboarding request handling`

This file is the gatekeeper for proving that someone controls a work email address. That matters because onboarding should not continue just because a person typed an address; they must be able to receive mail there. The flow is like a hotel front desk giving a temporary key card: the key only works for a short time, and too many failed tries means you must start over.

The main piece is ClaimWorkflow. When onboarding starts, it first asks the email policy whether the address is allowed and what company domain it belongs to. It then creates a random six-digit code, stores only a hash of that code, and emails the real code to the user. A hash is a one-way fingerprint: the system can check a later code without keeping the original readable code in storage. If sending the email fails, the stored claim is deleted so there is no half-created verification request left behind.

When the user submits a code, the workflow checks three things: whether they have already tried too many times, whether the code has expired, and whether the submitted code matches the stored hash. Expired or over-used claims are removed. A correct code marks the claim as verified. The important safety detail is that the code itself never needs to be stored after it is emailed.

#### Function details

##### `hash_code`  (lines 27–28)

```
def hash_code(code: str) -> str
```

**Purpose**: Turns a verification code into a secure fingerprint using SHA-256, a standard one-way hashing method. This lets the system compare codes later without saving the real code in readable form.

**Data flow**: It takes a code as text, converts it into bytes, and runs it through SHA-256. The output is a fixed-looking string that represents the code but cannot practically be turned back into the original code.

**Call relations**: ClaimWorkflow.start uses this before saving a new claim, so the database stores only the fingerprint of the emailed code. ClaimWorkflow.verify uses it again on the user’s submitted code, then compares the two fingerprints to decide whether the user entered the right code.

*Call graph*: called by 2 (start, verify); 1 external calls (sha256).


##### `ClaimWorkflow.start`  (lines 39–61)

```
async def start(self, email: str, surface: str, surface_ref: str) -> str
```

**Purpose**: Begins a new email verification claim. It checks that the email is allowed, creates a temporary code, stores the claim, and sends the code to the user.

**Data flow**: It receives an email address plus information about where onboarding started, such as the surface and its reference. It validates the email, makes a random six-digit code, builds an onboarding claim with an expiry time and zero attempts, stores that claim, and sends the code by email. If email sending fails, it deletes the stored claim and raises a clear ClaimError. On success, it returns the validated email domain.

**Call relations**: This is called when a user starts onboarding with a work email. Inside the flow it calls hash_code so the stored claim contains only a code fingerprint, creates the claim record, asks the store to save it, and asks the email sender to deliver the real code. If anything goes wrong during delivery, it turns that failure into a ClaimError for the onboarding flow to show back to the user.

*Call graph*: calls 1 internal fn (hash_code); 5 external calls (__init__, __init__, now, randbelow, uuid4).


##### `ClaimWorkflow.verify`  (lines 63–73)

```
async def verify(self, claim: OnboardClaim, code: str) -> None
```

**Purpose**: Checks a submitted verification code against an existing claim. It enforces the attempt limit and expiration time before marking the claim as verified.

**Data flow**: It receives a stored claim and the code the user typed. First it checks whether the claim has already used too many attempts; if so, it deletes the claim and reports that onboarding must restart. Next it checks whether the claim has expired; expired claims are also deleted. If the claim is still usable, it records one more attempt, hashes the submitted code, compares that hash with the stored hash, and either raises ClaimError for a mismatch or marks the claim as verified.

**Call relations**: This runs after the user receives an email and submits the code back to the onboarding flow. It calls hash_code to make the user’s entered code comparable with the stored fingerprint, and it uses a safe comparison function so the check does not reveal hints through timing differences. It hands successful cases to the store by marking the claim verified, while failed or stale cases become ClaimError messages for the surrounding onboarding process.

*Call graph*: calls 1 internal fn (hash_code); 3 external calls (__init__, now, compare_digest).


### `control/src/ufo_control/gateway_email.py`

`io_transport` · `request handling during signup/onboarding; also startup-style sender selection from environment`

This file supports the signup or onboarding path where a person must prove they control a company email address. First, it checks the email address itself. It rejects malformed addresses and common personal or disposable domains, such as Gmail or temporary-mail services, because the workspace is meant to map to a real organization rather than an anonymous inbox. This is a “fail closed” rule: if the address does not clearly look acceptable, it is refused.

The second job is delivering the short verification code. The file defines a shared sender shape, `EmailSender`, so the rest of the gateway can ask for “send this code” without caring how delivery happens. In production, `SesEmailSender` sends through Amazon SES, Amazon’s email service. It reads the pod’s web identity token, asks AWS STS for temporary credentials, signs the SES request locally using AWS Signature Version 4, and sends the email with asynchronous HTTP so the server is not blocked while waiting on the network. For local work, `ConsoleEmailSender` writes the email contents to the process log instead of contacting AWS.

Configuration comes from environment variables. Missing production settings cause clear runtime errors, which is intentional: silent email failure would break account verification.

#### Function details

##### `normalize_email`  (lines 122–128)

```
def normalize_email(email: str) -> tuple[str, str]
```

**Purpose**: This function cleans up an email address and extracts its domain. It is the first safety gate before deciding whether an address is allowed.

**Data flow**: It receives a raw email string, trims spaces, lowercases it, and checks that it has a simple valid shape with one domain after the `@`. If the shape is bad, it raises `WorkEmailError`; otherwise it returns the cleaned full address and the domain part.

**Call relations**: The work-email policy calls this before applying its denylist. By separating cleanup from policy, the policy can work with a predictable lowercase domain.

*Call graph*: called by 1 (validate); 1 external calls (__init__).


##### `WorkEmailPolicy.validate`  (lines 135–139)

```
def validate(self, email: str) -> str
```

**Purpose**: This method decides whether an email address belongs to an acceptable work domain. It rejects personal and throwaway email domains so a workspace is tied to a real organization.

**Data flow**: It receives an email string, asks `normalize_email` for the domain, then checks that domain against the denylist stored on the policy. If the domain is denied, it raises `WorkEmailError`; if it is allowed, it returns the domain.

**Call relations**: This is the main entry point for email eligibility checks in this file. It relies on `normalize_email` for format checking, then adds the project-specific rule about work email domains.

*Call graph*: calls 1 internal fn (normalize_email); 1 external calls (__init__).


##### `public_apex_host`  (lines 142–150)

```
def public_apex_host() -> str
```

**Purpose**: This function finds the public website host used in user-facing instructions. It turns a configured base URL into just the host name.

**Data flow**: It reads `UFO_PUBLIC_BASE_URL` from the environment, or uses the default public URL if none is set. It strips away the scheme and path, returning only the host; if no host can be found, it raises a runtime error explaining the expected format.

**Call relations**: This helper is used when something needs the public front-door host, such as invite text that includes an install command. It depends on standard URL parsing rather than hand-splitting the string.

*Call graph*: 1 external calls (urlsplit).


##### `verification_email`  (lines 153–159)

```
def verification_email(code: str, expires_at: datetime, ttl: timedelta) -> tuple[str, str]
```

**Purpose**: This function builds the subject and plain-text body for a verification-code email. It keeps the wording consistent no matter which sender is used.

**Data flow**: It receives the code, the expiration time, and the time-to-live duration. It formats the expiration time and duration into a short message, then returns the subject and body as text.

**Call relations**: Both the real SES sender and the console sender call this before delivering or logging a code. That way local development shows the same message a real user would receive.

*Call graph*: called by 2 (send, send); 2 external calls (strftime, total_seconds).


##### `invite_email`  (lines 162–172)

```
def invite_email(object_number: int, code: str, expires_at: datetime, apex_host: str) -> tuple[str, str]
```

**Purpose**: This function builds the subject and plain-text body for an invitation code. The invite text includes the object number, code, expiration time, and install command host.

**Data flow**: It receives an object number, a code, an expiration time, and the public host name. It converts the expiration time to UTC, formats all values into the invite template, and returns the subject and body.

**Call relations**: This is separate from normal verification email because invite codes have different wording and are intended for an operator to send manually. It does not send anything itself; it only prepares the text.

*Call graph*: 1 external calls (astimezone).


##### `EmailSender.send`  (lines 176–176)

```
async def send(self, email: str, code: str, expires_at: datetime, ttl: timedelta) -> None
```

**Purpose**: This is the shared promise that any email sender must fulfill: given an address and code details, send or otherwise deliver the code. It lets the rest of the system use email delivery without knowing whether it is real SES delivery or console logging.

**Data flow**: It defines the expected inputs: recipient email, code, expiration time, and time-to-live. The protocol itself produces no result and changes nothing; concrete senders provide the actual behavior.

**Call relations**: Both `SesEmailSender.send` and `ConsoleEmailSender.send` match this shape. `email_sender_from_env` returns an object that follows this protocol, so callers can treat both modes the same way.


##### `SesEmailSender.send`  (lines 199–221)

```
async def send(self, email: str, code: str, expires_at: datetime, ttl: timedelta) -> None
```

**Purpose**: This method sends a verification code through Amazon SES, Amazon’s email-sending service. It is the production delivery path.

**Data flow**: It receives the recipient email and code timing details. It builds the email text, obtains temporary AWS credentials, creates the JSON request body SES expects, signs the request so AWS can trust it, and posts it over asynchronous HTTP. If SES returns an error response, it raises a runtime error with the status and part of the response body.

**Call relations**: This is called when the environment selects SES mode. It calls `verification_email` for the message, `_assume_role` for temporary credentials, and `_sigv4_headers` to create the required AWS authentication headers before sending the HTTP request.

*Call graph*: calls 3 internal fn (_assume_role, _sigv4_headers, verification_email); 3 external calls (now, AsyncClient, dumps).


##### `SesEmailSender._assume_role`  (lines 223–242)

```
async def _assume_role(self) -> SesCredentials
```

**Purpose**: This method exchanges the pod’s web identity token for short-lived AWS credentials. Those credentials are needed before the sender can call SES.

**Data flow**: It reads the token file configured on the sender, sends that token plus the role ARN to AWS STS, and waits for the XML response. If STS reports an error, it raises a runtime error; otherwise it parses the response into access key, secret key, and session token values.

**Call relations**: `SesEmailSender.send` calls this right before each email send. After STS replies, this method hands the raw XML to `_parse_assume_role_credentials` so the rest of the sender can work with a simple credentials object.

*Call graph*: calls 1 internal fn (_parse_assume_role_credentials); called by 1 (send); 1 external calls (AsyncClient).


##### `_parse_assume_role_credentials`  (lines 245–258)

```
def _parse_assume_role_credentials(payload: str) -> SesCredentials
```

**Purpose**: This helper reads the AWS STS XML response and extracts the temporary credentials. It turns a bulky service response into the small credential object the sender needs.

**Data flow**: It receives the XML response text from STS, parses it, and looks for the access key ID, secret access key, and session token. If any required value is missing, it raises a runtime error; otherwise it returns a `SesCredentials` value containing the three pieces.

**Call relations**: `SesEmailSender._assume_role` calls this after a successful STS response. The returned credentials are later used by `_sigv4_headers` to sign the SES request.

*Call graph*: called by 1 (_assume_role); 2 external calls (__init__, fromstring).


##### `_parse_assume_role_credentials.credential`  (lines 248–252)

```
def credential(name: str) -> str
```

**Purpose**: This nested helper fetches one named credential field from the parsed STS XML. It prevents missing AWS credential fields from being treated as empty strings.

**Data flow**: It receives the name of one credential field, searches the parsed XML tree for that field, and returns its text value. If the value is absent, it raises a runtime error naming the missing field.

**Call relations**: It is used inside `_parse_assume_role_credentials` three times, once for each required AWS credential field. This keeps the XML lookup and error checking consistent.


##### `_sigv4_headers`  (lines 261–294)

```
def _sigv4_headers(host: str, body: bytes, region: str, credentials: SesCredentials, now: datetime) -> dict[str, str]
```

**Purpose**: This helper creates the authentication headers AWS requires for an SES request. AWS Signature Version 4 is a signing process that proves the request was made by someone holding valid AWS credentials.

**Data flow**: It receives the destination host, request body bytes, AWS region, temporary credentials, and current time. It hashes the body, builds AWS’s canonical request text, derives a signing key, computes the signature, and returns HTTP headers including the authorization value and session token.

**Call relations**: `SesEmailSender.send` calls this after it has built the SES request body and obtained credentials. `_sigv4_headers` calls `_signing_key` for the cryptographic key material, then hands signed headers back to the sender for the actual HTTP POST.

*Call graph*: calls 1 internal fn (_signing_key); called by 1 (send); 3 external calls (strftime, sha256, new).


##### `_signing_key`  (lines 297–301)

```
def _signing_key(secret_key: str, date_stamp: str, region: str) -> bytes
```

**Purpose**: This helper derives the special key used to sign an AWS SES request. It follows AWS’s required step-by-step HMAC process, where HMAC is a hash-based way to prove knowledge of a secret without sending the secret itself.

**Data flow**: It receives the AWS secret key, date stamp, and region. It repeatedly mixes those values with the SES service name and the final AWS marker, producing a byte string signing key.

**Call relations**: _sigv4_headers calls this while preparing the AWS authorization header. The result is not sent directly; it is used to create the final request signature.

*Call graph*: called by 1 (_sigv4_headers); 1 external calls (new).


##### `ConsoleEmailSender.send`  (lines 311–313)

```
async def send(self, email: str, code: str, expires_at: datetime, ttl: timedelta) -> None
```

**Purpose**: This method pretends to send a verification email by writing it to the application log. It is useful for local development where a developer needs the code but does not have or want an SES account.

**Data flow**: It receives the same email and code details as the real sender. It builds the normal verification subject and body, then logs the recipient, subject, and text; it does not contact any outside service.

**Call relations**: This method is used when `email_sender_from_env` selects console mode. It calls `verification_email` so the logged message matches the production email content.

*Call graph*: calls 1 internal fn (verification_email).


##### `email_sender_from_env`  (lines 316–330)

```
def email_sender_from_env() -> EmailSender
```

**Purpose**: This function chooses the email delivery method based on environment variables. It is the factory that turns deployment configuration into a usable sender object.

**Data flow**: It reads `UFO_CONTROL_EMAIL_MODE`. If the mode is `console`, it returns a `ConsoleEmailSender`. If the mode is `ses`, it reads required SES and AWS identity settings, builds a `SesEmailSender`, and returns it. If the mode is unknown or required settings are missing, it raises a clear runtime error.

**Call relations**: Startup or setup code can call this once to get an `EmailSender`. It relies on `_require_env` for mandatory settings and creates either the local logging sender or the production SES sender.

*Call graph*: calls 1 internal fn (_require_env); 3 external calls (__init__, __init__, Path).


##### `_require_env`  (lines 333–337)

```
def _require_env(name: str) -> str
```

**Purpose**: This helper reads an environment variable that must be present. It gives a clear error when required email configuration is missing.

**Data flow**: It receives an environment variable name, looks up its value, and returns the value if it is non-empty. If the variable is absent or empty, it raises a runtime error explaining that the email sender requires it.

**Call relations**: `email_sender_from_env` calls this when building the SES sender. It keeps configuration checks consistent and makes deployment mistakes fail loudly instead of causing confusing email failures later.

*Call graph*: called by 1 (email_sender_from_env).


### `control/src/ufo_control/gateway_invite.py`

`domain_logic` · `startup and workspace-creation request handling`

This file is the gatekeeper for a sensitive action: letting someone create a new workspace when they do not already belong to an existing workspace by email domain. Without it, the system could accidentally allow unlimited workspace creation, reuse old invite links, or let two people race to use the same code.

The file stores invite codes in a database table, but it never stores the readable code itself. Instead, it stores a hash, which is like a one-way fingerprint: the system can later recognize the code, but someone reading the database cannot easily recover it.

An invite is tied to an object number from the waitlist. The system refuses to mint a new live code for an object that already has one, and it also refuses if that object has already been identified through a consumed invite. Expired unused codes can be cleared away and replaced.

Redeeming is careful because two signup attempts could happen at nearly the same time. The code row is locked in the database while it is checked and marked as consumed. This is like putting a “do not touch” sign on a library book while one librarian checks it out. The same transaction also records the invite on the claim record, so the system does not end up with a used invite that is not connected to the signup claim.

#### Function details

##### `mint_code`  (lines 47–51)

```
def mint_code() -> str
```

**Purpose**: Creates a new human-readable invite code. The code uses a limited alphabet chosen to avoid confusing characters, and it is grouped with dashes so it is easier to read and type.

**Data flow**: It takes no input. It randomly chooses characters from the allowed invite alphabet, groups them into short chunks, joins the chunks with dashes, and returns the finished code string.

**Call relations**: When InviteCodes.mint needs a fresh invite, it calls this function first. The returned readable code is shown to the operator or user once, while only its hashed version is saved later.

*Call graph*: called by 1 (mint); 1 external calls (choice).


##### `hash_invite`  (lines 54–55)

```
def hash_invite(code: str) -> str
```

**Purpose**: Turns an invite code into a secure fingerprint for storage and lookup. This lets the database recognize a code later without keeping the original readable code.

**Data flow**: It receives a code string, trims extra spaces, lowercases it so typing case does not matter, converts it to bytes, and runs it through SHA-256, a standard one-way hashing method. It returns the resulting hash text.

**Call relations**: InviteCodes.mint uses this before saving a new invite, and InviteCodes.redeem uses it to find the matching saved invite when someone enters a code. This keeps minting and redeeming consistent.

*Call graph*: called by 2 (mint, redeem); 1 external calls (sha256).


##### `InviteCodes.ensure_table`  (lines 92–110)

```
async def ensure_table(self) -> None
```

**Purpose**: Prepares the database table that stores invite-code records. It also protects startup or migration work so only one process changes this table at a time.

**Data flow**: It uses the database connection pool stored on the InviteCodes object. It opens a database transaction, takes an advisory lock, checks whether an older incompatible invite table exists, drops that old table if needed, and then runs the table and index creation statements. It returns nothing, but leaves the database ready for invite minting and redemption.

**Call relations**: This is called during setup before invite codes are used. It does not call the minting or redeeming flow; instead, it creates the safe storage those flows depend on.


##### `InviteCodes.mint`  (lines 112–151)

```
async def mint(self, object_number: int) -> MintedInvite
```

**Purpose**: Creates a new one-time invite for a specific waitlist object number. It refuses to create a duplicate live invite or a new invite for an object that has already used one.

**Data flow**: It receives an object number. It generates a readable code, calculates its expiry time, opens a database transaction, and checks existing invite rows for that object. If the object was already identified, or already has an unexpired unused code, it raises InviteError. Otherwise it removes expired unused rows for that object, stores a new row containing the code hash, object number, and expiry time, and returns a MintedInvite containing the readable code and its details.

**Call relations**: This is the main path used by the admin invite flow. It calls mint_code to make the visible code, hash_invite to store only a fingerprint, and uuid4 to give the invite row a unique identity. If another process creates a live code for the same object at the same time, the database uniqueness rule catches that race and this function reports it as an InviteError.

*Call graph*: calls 2 internal fn (hash_invite, mint_code); 4 external calls (__init__, __init__, now, uuid4).


##### `InviteCodes.redeem`  (lines 153–182)

```
async def redeem(self, code: str, claim_id: UUID) -> InviteUnknown | InviteExpired | InviteConsumed | InviteAccepted
```

**Purpose**: Checks an entered invite code and, if valid, consumes it for a specific signup claim. It returns a clear result explaining whether the code was unknown, expired, already used, or accepted.

**Data flow**: It receives the entered code and the claim ID that is trying to use it. It hashes the code, opens a transaction, and looks up the invite row while locking it so no other redemption can change it at the same time. If there is no row, it returns InviteUnknown. If the row is already consumed, it returns InviteConsumed. If it has expired, it returns InviteExpired. Otherwise it stamps the invite with a consumed time, writes the invite ID onto the claim record, and returns InviteAccepted with the invite ID, object number, and consumption time.

**Call relations**: This is called during the workspace-creation or claim-resolution flow when a user supplies an invite code. It relies on hash_invite to find the stored fingerprint and then returns one of the invite result objects so the caller can decide what message or next step to show. Its database row lock is the key piece that prevents two simultaneous signup attempts from both using the same invite.

*Call graph*: calls 1 internal fn (hash_invite); 5 external calls (__init__, __init__, __init__, __init__, now).


### Client and browser transports
These files present the onboarding flow to terminal and browser clients using safe command bytes and web-friendly messages.

### `control/src/ufo_control/gateway_directives.py`

`io_transport` · `request handling`

The UFO terminal client receives server instructions as short lines of text. This file is the place that builds those instruction lines in a consistent format. You can think of it like writing labels for a conveyor belt: each label starts with an action word, followed by optional fields, separated in a predictable way so the client can read it back correctly.

The main helper, `directive`, takes a command word and any extra pieces of text, escapes characters that could confuse the line format, joins everything with tab characters, adds a newline, and returns bytes ready to send. This matters because terminal output and network responses are byte streams, not Python strings.

`render` is a tiny combiner. It joins already-built byte chunks into one response.

The file also has one special startup behavior: `first_run_install`. When someone first runs the tool through a fresh `curl | sh` flow, the server may need to tell the shell to install the UFO binary locally. It checks an HTTP-style header called `x-ufo-installed`. If that header is not set to `1`, it returns an `install` directive. Once the local binary exists, the client reports `x-ufo-installed=1`, so the install instruction stops being sent.

#### Function details

##### `directive`  (lines 8–13)

```
def directive(verb: str, *fields: str) -> bytes
```

**Purpose**: Builds one server-to-client instruction line. It makes sure any text fields are escaped so tabs, newlines, and backslashes inside the data do not accidentally look like separators or new commands.

**Data flow**: It receives a command word and zero or more text fields. It cleans each field by escaping backslashes, tabs, and newlines, removes carriage returns, joins the command and fields with tabs, adds a final newline, and turns the result into bytes. The output is ready to be written into a response stream.

**Call relations**: This is the basic building block for directives in this file. `first_run_install` calls it when it decides the client needs an `install` instruction, relying on `directive` to format that instruction safely.

*Call graph*: called by 1 (first_run_install).


##### `render`  (lines 16–17)

```
def render(*lines: bytes) -> bytes
```

**Purpose**: Combines several already-built byte chunks into one byte response. It is useful when a screen or reply is assembled from multiple directives or pieces.

**Data flow**: It receives any number of byte strings. It joins them in the same order with nothing added between them. The result is one continuous byte string suitable for sending onward.

**Call relations**: This function stands as a simple final assembly step for directive output. No caller is shown in the provided graph, but its role is to gather prepared byte lines into one response body.


##### `header_value`  (lines 20–25)

```
def header_value(headers: Mapping[str, str], name: str) -> str | None
```

**Purpose**: Looks up a header value without caring about letter case. This matters because HTTP-style header names are meant to be case-insensitive, so `X-UFO-Installed` and `x-ufo-installed` should mean the same thing.

**Data flow**: It receives a mapping of header names to values and the header name to find. It compares each available header name in lowercase form against the requested name in lowercase form. It returns the matching value if found, or `None` if the header is absent.

**Call relations**: `first_run_install` calls this before deciding whether to send an install directive. It supplies the small but important rule that header capitalization should not affect behavior.

*Call graph*: called by 1 (first_run_install).


##### `first_run_install`  (lines 28–35)

```
def first_run_install(headers: Mapping[str, str]) -> bytes
```

**Purpose**: Decides whether a new client session should be told to install the UFO binary. It prevents repeat install prompts by checking whether the client already says it is installed.

**Data flow**: It receives request headers. It asks `header_value` for the `x-ufo-installed` header. If the value is exactly `1`, it returns empty bytes, meaning no extra instruction is needed. Otherwise, it uses `directive` to create and return an `install` instruction.

**Call relations**: This function is the small decision point for first-run setup. During response building, it can be called with the incoming headers; it then uses `header_value` to read the client’s installation status and hands off to `directive` only when an install command must be sent.

*Call graph*: calls 2 internal fn (directive, header_value).


### `control/src/ufo_control/gateway_web.py`

`io_transport` · `request handling`

This file exists so a person can sign in through a web browser without creating a separate sign-in system. The important idea is that the browser is only another display for the same onboarding machine used elsewhere. Like two different front doors leading to the same reception desk, the web page and terminal both talk to the same underlying process.

The file defines a web channel name, a helper that converts onboarding output into JSON-shaped data, and a large self-contained HTML page called LOGIN_PAGE. That page asks for the user's work email, shows messages from the onboarding flow, sends answers back to the server, and eventually shows a signed-in card with the user's email, workspace URL, and terminal install command.

The onboarding machine speaks in simple directive lines such as “say this”, “ask this”, “here is a token”, or “here is the workspace”. Those lines are separated with tabs and newlines, so this file includes code to safely undo escaping. That matters because a message field might itself contain a tab or a newline, and the browser should receive the real text, not the encoded version.

A notable safety detail is the debugger link. If a special operator-only debugger directive appears, the page submits the token in a form POST body instead of putting it in the URL. That keeps the bearer token out of browser history and link logs.

#### Function details

##### `parse_directives`  (lines 15–24)

```
def parse_directives(payload: bytes) -> list[dict[str, object]]
```

**Purpose**: This function turns the onboarding machine's plain text directive stream into a list of dictionaries that can be returned as JSON to the browser. It preserves the real contents of each field, including tabs and newlines that were escaped for safe line-based transport.

**Data flow**: It receives raw bytes from the onboarding flow. It decodes them into text, splits the text into separate directive lines, ignores empty lines, separates each line into a verb and its fields, and runs each field through the unescaping helper. It returns a list like “verb plus fields”, ready for the web page's JavaScript to read.

**Call relations**: When the web endpoint needs to send onboarding output to the browser, this function is the translator between the terminal-style directive format and browser-friendly JSON. For each field it finds, it asks _unescape to restore any protected tab, newline, or backslash characters before handing the cleaned directives onward.

*Call graph*: calls 1 internal fn (_unescape).


##### `_unescape`  (lines 27–40)

```
def _unescape(field: str) -> str
```

**Purpose**: This helper restores special characters that were encoded inside a directive field. It is what lets a field contain a real tab, newline, or backslash without confusing the line-and-tab format used to frame directives.

**Data flow**: It receives one escaped text field. It walks through the characters from left to right; when it sees a recognized backslash escape, it replaces it with the intended character, and otherwise keeps the character as-is. It returns the restored string.

**Call relations**: This function sits underneath parse_directives as the careful text cleanup step. parse_directives uses it on every directive field so the browser receives the original human-readable message or value rather than the transport-safe encoded form.

*Call graph*: called by 1 (parse_directives).


### Workspace provisioning
These modules create or find the shared workspace, add members, prepare default agents, and run first-workspace setup.

### `control/src/ufo_control/gateway_shared.py`

`domain_logic` · `hosted onboarding`

This file solves a hosted onboarding problem: when someone signs up with a verified domain, the system needs to put them in the shared workspace for that domain instead of creating random duplicates. Think of the domain as the building address, and the workspace as the office suite everyone from that building should share.

The file first defines how to find the special database connection string used for these onboarding writes. That connection is important because workspace creation is done as the service role that is allowed to write the needed rows.

The main piece is the SharedWorkspaces class. It uses a database pool to check whether a domain already has a workspace. It looks in two ways: by a deterministic workspace ID made from the domain name, and by checking the first member email in existing workspaces. This helps support both the intended stable ID scheme and older or pre-existing data.

If no workspace exists, ensure creates one using a repeatable UUID based on the domain, so the same domain always points to the same workspace ID. It then adds the user as a member and creates a default agent if one is not already present. The database inserts are written to do nothing on conflicts, so repeated onboarding attempts do not create duplicate workspaces or duplicate default agents. If the lookup finds more than one workspace for the same domain, the file treats that as a serious data problem and raises an error.

#### Function details

##### `serve_dsn`  (lines 20–27)

```
def serve_dsn() -> str
```

**Purpose**: This function reads the database connection string used by the hosted onboarding path. It fails loudly if the required environment variable is missing, because onboarding cannot safely create workspace rows without that configured service connection.

**Data flow**: It reads the UFO_CONTROL_SERVE_DSN environment variable from the process environment. If a value is present, it returns that string. If no value is present, it raises an error explaining that the hosted onboarding database role is not configured.

**Call relations**: This is a small setup helper for code that needs the service database connection. It does not call other project functions; it simply protects later onboarding work from running with a missing or wrong database address.


##### `SharedWorkspaces.exists`  (lines 37–38)

```
async def exists(self, domain: str) -> bool
```

**Purpose**: This function answers a simple question: does this verified domain already have a shared workspace? It is useful when the caller only needs to check availability, not create or join anything.

**Data flow**: It receives a domain name, passes it to _existing, and waits for the database lookup. If _existing returns a workspace ID, this function returns true. If _existing returns nothing, it returns false.

**Call relations**: This is the lightweight lookup path. It delegates the real domain-to-workspace search to SharedWorkspaces._existing, so it uses the same rules as the creation path in SharedWorkspaces.ensure.

*Call graph*: calls 1 internal fn (_existing).


##### `SharedWorkspaces.ensure`  (lines 40–66)

```
async def ensure(self, domain: str, email: str) -> str
```

**Purpose**: This function makes sure a shared workspace exists for a domain and that the given email address is a member of it. It also makes sure the workspace has the default agent, so a newly onboarded user lands in a usable workspace.

**Data flow**: It receives a domain and an email address. First it asks _existing whether the domain already maps to a workspace. If not, it creates a stable workspace ID from the lowercased domain. It lowercases and trims the email, enters the workspace context, opens a database transaction, inserts the workspace row if missing, adds the user as a member, and inserts the default agent if that agent is not already there. It returns the workspace ID as text.

**Call relations**: This is the main onboarding action in the file. It starts by calling SharedWorkspaces._existing to avoid making duplicates. Then it uses the workspace context and transaction helpers to write safely to the database, calls create_member to add the user, and uses PostgreSQL insert-on-conflict behavior so repeated calls are safe.

*Call graph*: calls 1 internal fn (_existing); 6 external calls (insert, workspace_tx, create_member, ws, uuid4, uuid5).


##### `SharedWorkspaces._existing`  (lines 68–86)

```
async def _existing(self, domain: str) -> UUID | None
```

**Purpose**: This private helper finds the one workspace that belongs to a domain, if there is one. It also protects the system from a dangerous situation where the same domain appears to point to multiple workspaces.

**Data flow**: It receives a domain, lowercases it, and builds the deterministic UUID that should belong to that domain. It then borrows a database connection from the asyncpg pool and queries for matching workspace IDs in two ways: a direct match on the deterministic ID, and a match based on the domain part of the first member email in each workspace. If no rows are found, it returns nothing. If one row is found, it returns that workspace ID. If more than one row is found, it raises an error because the domain mapping is ambiguous.

**Call relations**: This is the shared lookup engine used by both SharedWorkspaces.exists and SharedWorkspaces.ensure. The check-only path uses its answer to return true or false, while the ensure path uses its answer to decide whether to reuse an existing workspace or create the deterministic one.

*Call graph*: called by 2 (ensure, exists); 1 external calls (uuid5).


### `core/src/ufo/onboarding.py`

`orchestration` · `first-run init`

This file is the “move-in checklist” for a brand-new UFO installation. Without it, a fresh system would not have a workspace to store data in, an owner who can use it, or a default agent ready for the first conversation.

The flow is careful to create the core pieces only once. Before touching the database, it checks that the chosen AI model has its required environment variable set, if the system can know that requirement ahead of time. It also checks whether extension onboarding steps need a credential store key. This avoids leaving behind a half-created workspace if setup cannot safely continue.

The main `Onboarding` class then creates the durable core records in one database transaction: a workspace, the first member, and a default agent with a simple prompt and the selected model. If the database already has an owner, it raises `AlreadyInitialized` instead of creating duplicates.

After the core workspace exists, the file runs onboarding steps supplied by installed extensions. Each extension receives its own scoped context, like giving each add-on its own labeled toolbox. If one extension step fails, the error is logged and the next step continues, so a broken add-on does not block the usable workspace.

#### Function details

##### `run_onboarding_steps`  (lines 46–74)

```
async def run_onboarding_steps(manifests: tuple[Manifest, ...], workspace_id: UUID, credentials: CredentialStore | None) -> None
```

**Purpose**: Runs the setup steps provided by installed extensions for a newly created workspace. It gives each extension the limited context it is allowed to use, and it keeps going even if one extension fails.

**Data flow**: It receives the installed extension manifests, the new workspace ID, and an optional credential store. It enters that workspace’s context, looks through each manifest, skips extensions with no onboarding steps, and skips step execution if credentials are required but unavailable. For each step it builds an extension-specific context, calls the step, and logs failures instead of raising them. Nothing is returned; the effect is that extension setup code gets a chance to initialize its own data or secrets.

**Call relations**: This is called by `Onboarding.run_steps` after the core workspace and owner already exist. It calls `ws` to mark which workspace the setup belongs to, `context_for` to build the extension’s scoped handle, and `log` when steps are skipped or fail.

*Call graph*: called by 1 (run_steps); 3 external calls (context_for, log, ws).


##### `Onboarding.run`  (lines 88–91)

```
async def run(self) -> Onboarded
```

**Purpose**: Runs the whole onboarding sequence from start to finish. This is the simple high-level method a caller can use when it wants a fresh workspace initialized.

**Data flow**: It starts with the configuration, email, model choice, credentials, and installed extension manifests stored on the `Onboarding` object. It first creates the core workspace setup, then runs extension setup steps for that workspace, and finally returns the created workspace and member IDs.

**Call relations**: This method is the top-level flow inside this file. It calls `Onboarding.create` to make the core records, then passes the result to `Onboarding.run_steps` so extensions can do their own setup afterward.

*Call graph*: calls 2 internal fn (create, run_steps).


##### `Onboarding.create`  (lines 93–99)

```
async def create(self) -> Onboarded
```

**Purpose**: Creates the core, durable part of onboarding: the workspace, owner member, and default agent. It performs safety checks first so setup fails early before writing partial data.

**Data flow**: It uses the onboarding object’s chosen model, configuration, credentials, manifests, and email. It checks that the model key is present when needed, checks that extension steps have the credential support they need, then writes the workspace records. It returns an `Onboarded` value containing the new workspace ID and member ID.

**Call relations**: This is called by `Onboarding.run` before extension setup begins. It delegates the checks to `_require_model_key` and `_require_credentials_for_steps`, then delegates the actual database creation to `_create_workspace`.

*Call graph*: calls 3 internal fn (_create_workspace, _require_credentials_for_steps, _require_model_key); called by 1 (run).


##### `Onboarding.run_steps`  (lines 101–102)

```
async def run_steps(self, onboarded: Onboarded) -> None
```

**Purpose**: Runs extension onboarding for a workspace that has already been created. It separates add-on setup from the core workspace creation step.

**Data flow**: It receives an `Onboarded` result containing the workspace ID. It passes that workspace ID, along with the installed manifests and credential store from the `Onboarding` object, into the shared extension-step runner. It returns nothing; the side effects come from whatever extension steps successfully do.

**Call relations**: This is called by `Onboarding.run` after `Onboarding.create` succeeds. Its only job is to hand off to `run_onboarding_steps`, which performs the per-extension loop and error logging.

*Call graph*: calls 1 internal fn (run_onboarding_steps); called by 1 (run).


##### `Onboarding._require_credentials_for_steps`  (lines 104–115)

```
def _require_credentials_for_steps(self) -> None
```

**Purpose**: Stops onboarding early if installed extensions have setup steps but the credential store is not available. This prevents creating a workspace that cannot safely finish its required add-on setup.

**Data flow**: It reads the onboarding object’s credential store and extension manifests. If credentials are present, it allows setup to continue. If credentials are missing and any extension has onboarding steps, it raises an error explaining which environment variable needs to be set. It returns nothing when the check passes.

**Call relations**: This is called by `Onboarding.create` before any database records are created. It acts as a gatekeeper so `_create_workspace` only runs when the surrounding setup has the needed secret-storage support.

*Call graph*: called by 1 (create).


##### `Onboarding._require_model_key`  (lines 117–124)

```
def _require_model_key(self) -> None
```

**Purpose**: Checks that the selected AI model has the environment variable it needs before the first workspace is created. This helps ensure the default agent will be usable for its first turn.

**Data flow**: It asks `_model_key_env` which environment variable, if any, is required for the selected model. If no known variable is required, it passes. If a variable is required but missing from the process environment, it raises an error. It returns nothing when the check passes.

**Call relations**: This is called by `Onboarding.create` as one of the early safety checks. It relies on `_model_key_env` to learn the correct environment variable name before deciding whether setup can continue.

*Call graph*: calls 1 internal fn (_model_key_env); called by 1 (create).


##### `Onboarding._model_key_env`  (lines 126–129)

```
def _model_key_env(self) -> str | None
```

**Purpose**: Finds the name of the environment variable that should contain the API key for the selected model, when the core system knows it. For extension-provided model providers, it may return nothing because those providers resolve their keys later.

**Data flow**: It reads the configuration, installed manifests, and selected model from the `Onboarding` object. It builds or queries the model registry, asks it for the required key environment variable, and returns either that variable name or `None`.

**Call relations**: This is called only by `_require_model_key`. It delegates model-provider knowledge to `model_registry`, keeping the onboarding code from hard-coding every possible model’s secret name.

*Call graph*: called by 1 (_require_model_key); 1 external calls (model_registry).


##### `Onboarding._create_workspace`  (lines 131–154)

```
async def _create_workspace(self) -> Onboarded
```

**Purpose**: Writes the first workspace, owner member, and default agent into the database. It also protects against running first-time setup twice.

**Data flow**: It opens a workspace database transaction, checks whether any member email already exists, and raises `AlreadyInitialized` if so. If the database is still fresh, it creates new unique IDs, inserts a workspace row, creates the owner member using the provided email, inserts the default agent using the selected model and default prompt, then returns the new workspace and member IDs as an `Onboarded` value.

**Call relations**: This is called by `Onboarding.create` after the prerequisite checks pass. It uses `workspace_tx` for the database boundary, SQLAlchemy helpers to read and insert rows, `create_member` to create the owner, `uuid4` for new IDs, and `Onboarded` to package the result for the rest of the onboarding flow.

*Call graph*: called by 1 (create); 7 external calls (__init__, __init__, insert, select, workspace_tx, create_member, uuid4).

## 📊 State Registers Touched

- `reg-workspace-context` — The current workspace and member context that keeps every request acting inside the right tenant boundary.
- `reg-workspaces-members-agents` — The durable records for workspaces, their members, and the agents that can act for them.
- `reg-identity-and-session-tokens` — The identities, bearer tokens, gateway tokens, operator sessions, and other passes that prove who is allowed in.
- `reg-onboarding-claims-invites` — The temporary signup codes, email claims, and invite records used before or during workspace creation.
- `reg-seat-billing-state` — The workspace seat limits, granted seats, included seats, and external billing integration state.
- `reg-secret-keyring` — Loaded signing and encryption key material used to mint/verify tokens and seal/unseal protected secrets across trusted paths.
- `reg-hosted-domain-workspace-map` — Durable hosted onboarding mapping from verified email domains to the shared workspace used for automatic member provisioning.
