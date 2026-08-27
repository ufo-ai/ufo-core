# Slack and iMessage Connector Flows  `stage-13.7`

This stage is the bridge between the main system and two outside messaging worlds: Slack and iMessage. It is mostly setup and behind-the-scenes support, with some tools the agent can use during a conversation. Its job is to make sure people can connect the right accounts, choose the right message destinations, and receive clear next steps instead of dealing with raw provider setup details.

The iMessage tool lets a signed-in workspace member attach a phone number. It checks that the person is allowed to do this, reserves the number so it is not claimed twice, prepares the opt-in text they must send, and gives instructions with a QR code.

The Slack hooks run around larger system events. They adjust outgoing connector messages so the correct Slack bot is mentioned, and they update the “connect account” button after a user finishes linking an account.

The Slack tools are the user-facing controls for Slack setup and lookup. They help connect a workspace, generate a Slack app setup manifest, and find channels or direct messages, turning Slack’s multi-step setup into guided states.

## Files in this stage

### iMessage Phone Opt-In
Workspace members connect an iMessage phone number through permission checks, number reservation, opt-in messaging, and QR-code instructions.

### `extensions/imessage/ufo_ext_imessage/tools.py`

`domain_logic` · `request handling`

This file solves the safety problem of connecting a real phone number without accidentally claiming someone else’s number. Think of it like a front desk issuing a temporary claim ticket: the member asks to connect a number, the system reserves it for a short time, and the member proves they control the phone by sending a specific text from it.

The main tool is `ImessageConnect.run`. It first makes sure the request comes from a signed-in workspace member. It then checks whether this deployment has iMessage provider credentials and whether the workspace is already linked to that provider. If the provider is not connected yet, only an admin may connect it.

After that, it tries to reserve the requested phone number for the current member. If another member already owns it, the tool refuses. If the number is already linked to this member, it returns a connected result. Otherwise, it creates or reuses a pending claim with a randomly generated opt-in code, assigns an iMessage line, stores the pending claim, and shares a QR code image. The final response tells the member exactly what text to send and to which assigned number.

The helper functions keep the user-facing pieces tidy: they normalize phone numbers, format them nicely, build SMS links, generate QR images, and package tool responses as small JSON messages.

#### Function details

##### `opt_in_link`  (lines 37–41)

```
def opt_in_link(assigned_phone_number: str, opt_in_code: str) -> str
```

**Purpose**: Builds a tappable SMS link that opens the phone’s Messages app with the opt-in text already filled in. This reduces typing mistakes when the member needs to prove they control the phone.

**Data flow**: It receives the assigned iMessage phone number and the opt-in code. It combines the standard opt-in text with the code, safely encodes it for use inside a link, and returns an `sms:` link that can prefill a message body.

**Call relations**: `_opt_in_result` calls this when preparing the pending connection response. The link is one of the convenient ways the member can send the required proof text.

*Call graph*: called by 1 (_opt_in_result); 1 external calls (quote).


##### `opt_in_qr`  (lines 44–52)

```
def opt_in_qr(assigned_phone_number: str, opt_in_code: str) -> bytes
```

**Purpose**: Creates a QR code image for the same opt-in message. This is useful when the member is reading the instructions on a computer and wants to scan with their phone instead of retyping anything.

**Data flow**: It receives the assigned phone number and opt-in code. It builds a QR payload that phone cameras commonly recognize as an SMS message, renders it as a PNG image in memory, and returns the raw image bytes.

**Call relations**: `ImessageConnect.run` calls this after creating or finding a pending claim. The generated image is then handed to `ToolContext.share_artifact` so the user can see or download the QR code.

*Call graph*: called by 1 (run); 2 external calls (BytesIO, make).


##### `_display_phone`  (lines 55–59)

```
def _display_phone(phone_number: str) -> str
```

**Purpose**: Turns a US phone number in machine-friendly form into a friendlier display form. For example, it can show `+14155550123` as `(415) 555-0123`.

**Data flow**: It receives a phone number string. If it matches the expected US `+1` format, it rearranges the digits into a readable format; otherwise, it returns the original string unchanged.

**Call relations**: `ImessageConnect.run` and `_opt_in_result` use this when writing instructions for a person. It keeps user messages readable while leaving the underlying stored phone number unchanged.

*Call graph*: called by 2 (run, _opt_in_result).


##### `ImessageConnectInput._e164`  (lines 69–81)

```
def _e164(cls, value: str) -> str
```

**Purpose**: Checks and normalizes the phone number supplied to the iMessage connection tool. It accepts common US formatting, but always converts a valid number into a standard `+1...` form.

**Data flow**: It receives the raw phone number text from the tool input. It trims spaces, rejects letters, removes punctuation, handles an optional US country code, verifies that the number is a valid 10-digit US number, and returns it as `+1` followed by ten digits. If the number is invalid, it raises a clear validation error.

**Call relations**: This validator is run by the input model when `ImessageConnectInput` is built. By the time `ImessageConnect.run` uses `args.phone_number`, the value has already been cleaned and checked.


##### `_result`  (lines 84–90)

```
def _result(state: str, instruction: str, **extra: object) -> ToolResult
```

**Purpose**: Builds the standard response shape returned by this tool. It packages a connection state, a human instruction, and any extra details into a JSON text result.

**Data flow**: It receives a state such as `pending`, `connected`, or `not_connected`, plus an instruction message and optional extra fields. It turns those values into JSON, wraps that JSON as text content, marks the result as untrusted, and returns a `ToolResult`.

**Call relations**: `ImessageConnect.run` uses this for all direct success and failure messages. `_opt_in_result` also uses it so pending opt-in responses follow the same format.

*Call graph*: called by 2 (run, _opt_in_result); 3 external calls (__init__, __init__, dumps).


##### `_opt_in_result`  (lines 93–102)

```
def _opt_in_result(assigned_phone_number: str, opt_in_code: str) -> ToolResult
```

**Purpose**: Creates the response shown when a phone number is reserved but still needs proof by text message. It tells the member exactly what to send and includes a prefilled SMS link.

**Data flow**: It receives the assigned iMessage line and the opt-in code. It builds the exact opt-in text, formats the assigned phone number for readability, creates an SMS link, and returns a pending-state tool result with those details.

**Call relations**: `ImessageConnect.run` calls this after it has reserved the phone number, stored or reused the pending claim, and shared the QR code. This function focuses on the final user-facing instruction for completing the claim.

*Call graph*: calls 3 internal fn (_display_phone, _result, opt_in_link); called by 1 (run).


##### `ImessageConnect.run`  (lines 109–164)

```
async def run(self, ctx: ToolContext, args: ImessageConnectInput) -> ToolResult
```

**Purpose**: Runs the full iMessage phone connection flow for one workspace member. It verifies the requester, connects the workspace to the provider if allowed, reserves the phone number, and starts or completes the claim process.

**Data flow**: It receives the tool context and the validated phone number input. It reads the current member, provider configuration, workspace installation state, existing phone ownership, and any stored pending claim. Depending on what it finds, it returns a not-connected message, binds the provider for an admin, reports an already connected number, or creates/reuses a pending claim with an assigned line and random opt-in code. In the pending case it also shares a QR code artifact before returning instructions.

**Call relations**: This is the central flow that calls the helper functions in this file. It uses `_result` for standard responses, `_display_phone` for readable instructions, `opt_in_qr` to make the QR image, and `_opt_in_result` to finish the pending opt-in response. It also calls out to the tool context, installation service, storage, and iMessage provider because those outside pieces hold the workspace state, saved claims, and provider line assignment.

*Call graph*: calls 6 internal fn (share_artifact, speaker_is_admin, _display_phone, _opt_in_result, _result, opt_in_qr); 6 external calls (__init__, now, choice, claim_key, read_claim, uuid4).


### Slack Connector Workflows
Slack hooks and tools manage bot mentions, account-connection updates, workspace setup, app manifests, and conversation lookup.

### `extensions/slack/ufo_ext_slack/hooks.py`

`orchestration` · `during tool use and after connection recording`

This file is a small bridge between the general connector system and Slack-specific details. The connector tool can send a Slack message, but it does not know which Slack bot user belongs to this workspace. Without this file, Slack messages would either have only a generic attribution footer or stale connection buttons would remain in chat after a user already connected an account.

There are two main jobs here. First, before a connector sends a Slack message, the hook checks whether the tool call is really a Slack send. If it is, it looks in the extension’s private store for the bot user ID that the Slack surface saved earlier. If the ID is present and looks valid, it rewrites the message arguments so the footer mentions the actual bot user. This is intentionally cautious: if reading the store is slow or fails, the hook quietly does nothing. That matters because this hook runs in a “gate” position, where an error could block the user’s message from being sent.

Second, after an external account connection is recorded, the file looks for a Slack message that had been saved as the place where a “connect” button was shown. If found, it edits that Slack message so the button no longer invites the user to do something already completed. In short, this file keeps Slack messages accurate without letting cosmetic Slack updates endanger the core user action.

#### Function details

##### `attribute_connector_send`  (lines 38–52)

```
async def attribute_connector_send(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook adds a Slack bot mention footer to an outgoing connector Slack message, when the workspace’s bot user ID is known. It leaves all other tool calls, and Slack sends without a known bot user, unchanged.

**Data flow**: It receives a hook context containing the event payload. If the payload is a pre-tool-use event for the external connector tool, and the target tool looks like a Slack send, it asks `_mirrored_self_user_id` for the stored Slack bot user ID. When that ID is available, it rewrites the tool input’s message arguments with `mention_attributed` and returns a `ModifyInput` result; otherwise it returns `None`, meaning “do not change anything.”

**Call relations**: The hook system calls this before the external tool is allowed to run. It relies on `is_slack_send` to avoid touching unrelated connector calls, uses `_mirrored_self_user_id` to safely read the saved bot identity, then hands the rewritten arguments to `ModifyInput` so the connector tool sends the adjusted message.

*Call graph*: calls 1 internal fn (_mirrored_self_user_id); 3 external calls (__init__, is_slack_send, mention_attributed).


##### `_mirrored_self_user_id`  (lines 55–69)

```
async def _mirrored_self_user_id(ctx: HookContext) -> str | None
```

**Purpose**: This helper safely reads the Slack bot user ID that was previously saved for this workspace. It is deliberately fail-soft: if the read is slow, fails, or returns something that does not look like a Slack bot user ID, it returns `None` instead of raising an error.

**Data flow**: It receives the hook context, then reads the extension’s scoped store using the known key for the bot user ID. The read is wrapped in a short timeout, like checking a note on a clipboard but refusing to hold up the whole line if the clipboard is missing. If the stored value is a string matching the expected Slack bot user ID pattern, that string comes out; otherwise the result is `None`. On failures, it writes a small log message and also returns `None`.

**Call relations**: `attribute_connector_send` calls this when it is considering whether to add a bot mention to a Slack send. This helper keeps the higher-level hook safe by containing timeouts, store errors, and bad stored data before they can block the connector message.

*Call graph*: called by 1 (attribute_connector_send); 3 external calls (timeout, match, log).


##### `settle_connect_button`  (lines 72–101)

```
async def settle_connect_button(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook updates a Slack “connect this account” button after the account connection has actually been recorded. Its goal is to keep the Slack thread from showing an outdated button that invites the user to repeat completed work.

**Data flow**: It receives a hook context and expects the payload to be a completed connection record, including the provider, account information, and owning member. It uses the owner and provider to look up a saved Slack connect message in the extension store. If no saved message exists, it does nothing. If one exists, it fetches the Slack bot token, validates the saved message shape, asks Slack to settle or update the message with the connected account label, and then deletes the saved store entry. If this hook is fired with the wrong kind of payload, it raises an error because that would mean the hook was wired incorrectly.

**Call relations**: The system calls this after a connection has already been recorded, so the user’s account connection is not at risk if the Slack update fails. It uses `connect_message_key` to find the held Slack message, `ConnectMessage.model_validate` to turn stored data back into a usable message object, and `settle_connect_message` to perform the Slack-side update.

*Call graph*: 3 external calls (model_validate, connect_message_key, settle_connect_message).


### `extensions/slack/ufo_ext_slack/tools.py`

`orchestration` · `Slack setup and Slack tool use`

Slack setup has several moving parts: app credentials, a bot token, a signing secret, a public web address, and proof that Slack can actually reach this UFO deployment. This file packages that work as chat tools so an admin can connect Slack while talking to the agent instead of editing hidden system state by hand.

There are two setup paths. The preferred path is OAuth, which is the familiar “Add to Slack” button. If this deployment already has its own Slack app configured, the tool creates a short-lived install link for an admin. The alternate “manifest” path is for people who bring their own Slack app. In that case, this file can print the exact Slack app manifest to paste into Slack, then checks privately stored credentials and asks Slack who the bot belongs to.

Both paths end in the same shared status flow. The tool reports whether Slack is not configured, not installed, pending, or connected. “Pending” means the bot identity is known, but Slack has not yet sent a signed request to this deployment. “Connected” means Slack has reached the deployment and the signing secret matched, like checking both the visitor’s badge and the front-door log.

The file also includes a runtime search tool for Slack conversations. Once connected, it uses the bot token to list channels, group chats, and direct messages so the agent can find a place by name or people involved, not only by a raw Slack ID.

#### Function details

##### `_events_url`  (lines 127–128)

```
def _events_url(public_base_url: str) -> str
```

**Purpose**: Builds the public web address Slack should send events to. It makes sure the base URL is shaped consistently before adding the Slack route.

**Data flow**: It receives the deployment’s public base URL, removes any trailing slash, then appends `/surface/slack`. The result is a single URL string that Slack can call when messages or app events happen.

**Call relations**: The connection flow uses this when reporting where Slack should reach the deployment. The manifest tool also uses it so the generated Slack app manifest contains the right request URL.

*Call graph*: called by 2 (slack_connect_handler, slack_manifest_handler).


##### `_state`  (lines 131–133)

```
def _state(state: str, hint: str, events_url: str | None, **extra: object) -> ToolResult
```

**Purpose**: Creates a standard tool response describing the current Slack connection state. This keeps all setup replies in the same simple JSON shape.

**Data flow**: It receives a state name, a human-readable hint, the Slack events URL if known, and any extra details such as a team ID or install link. It turns those into JSON text and wraps that text in the tool result object returned to the agent.

**Call relations**: The main connection handler and its helper paths call this whenever they need to tell the user what to do next. It hands back a ready-made response instead of each caller building its own format.

*Call graph*: called by 3 (_derive_manifest_identity, _oauth_link, slack_connect_handler); 3 external calls (__init__, __init__, dumps).


##### `slack_connect_handler`  (lines 136–177)

```
async def slack_connect_handler(ctx: ToolContext, args: SlackConnectInput) -> ToolResult
```

**Purpose**: Runs the main Slack connection check and setup flow. Someone can call it before, during, or after setup, and it will report the current state instead of blindly repeating work.

**Data flow**: It reads the public URL, any stored Slack bot token, and any saved Slack identity. If no identity exists, it follows the requested setup method: OAuth creates an install link, while manifest setup checks stored secrets and derives the identity from Slack. Once identity exists, it tries to bind this Slack team to the current UFO workspace, then checks whether Slack has successfully reached the deployment. It returns a JSON status such as `not_configured`, `not_installed`, `pending`, or `connected`.

**Call relations**: This is the handler behind the `slack_connect` tool. It calls `_oauth_link` for one-click install, `_derive_manifest_identity` for bring-your-own-app setup, `_verified` to see whether Slack has called back successfully, and `_state` to explain the result.

*Call graph*: calls 5 internal fn (_derive_manifest_identity, _events_url, _oauth_link, _state, _verified); 2 external calls (read_identity, slack_installation_id).


##### `_oauth_link`  (lines 180–210)

```
async def _oauth_link(ctx: ToolContext, events_url: str | None) -> ToolResult
```

**Purpose**: Creates the “Add to Slack” link for the OAuth setup path. OAuth means Slack’s standard permission-granting flow, where an admin approves the app through Slack’s website.

**Data flow**: It checks whether this deployment has Slack app client credentials in its environment. If not, it returns a message telling the user to use the manifest path. If credentials exist, it checks that the speaker is an admin and that the deployment has a public URL. It then creates a sealed, short-lived authorization handoff and builds the Slack install URL. The output is a tool result containing that link and a next-step hint.

**Call relations**: The main connection handler calls this when no Slack identity is stored and the requested method is OAuth. It hands off to the tool context to begin credential authorization, then uses Slack URL helpers to produce the final install link.

*Call graph*: calls 3 internal fn (begin_credential_authorization, speaker_is_admin, _state); called by 1 (slack_connect_handler); 3 external calls (slack_authorize_url, slack_client_id, slack_oauth_redirect_uri).


##### `_derive_manifest_identity`  (lines 213–247)

```
async def _derive_manifest_identity(ctx: ToolContext, events_url: str | None) -> SlackIdentity | ToolResult
```

**Purpose**: Completes the bring-your-own Slack app setup path once the user has privately supplied the bot token and signing secret. It proves the bot token by asking Slack for the bot’s team and user identity.

**Data flow**: It checks the private credential slots for the bot token and signing secret. If either is missing, it returns a `not_configured` response naming what still needs to be collected. If both are present, it checks that the speaker is an admin, then uses the bot token to resolve and save the Slack identity. If Slack rejects the token or returns unusable identity data, it returns a plain-language diagnosis.

**Call relations**: The main connection handler calls this when setup is using the manifest path and no identity has been saved yet. It uses `_state` for user-facing status messages and `_token_diagnosis` to translate Slack token errors into clearer instructions.

*Call graph*: calls 3 internal fn (speaker_is_admin, _state, _token_diagnosis); called by 1 (slack_connect_handler); 1 external calls (__init__).


##### `_verified`  (lines 250–269)

```
async def _verified(ctx: ToolContext) -> bool
```

**Purpose**: Checks whether Slack has actually reached this deployment using the current signing secret. This prevents the system from saying “connected” just because credentials were entered.

**Data flow**: It looks for a saved verification marker in blob storage. If the marker is missing or malformed, it returns false. It then reads the stored Slack signing secret, fingerprints it, and compares that fingerprint with the marker. It returns true only when the marker matches the current secret.

**Call relations**: The main connection handler calls this after identity is known. If it returns true, the user sees `connected`; if not, they see `pending` and are told to message or mention the bot so Slack will send the first verified request.

*Call graph*: called by 1 (slack_connect_handler); 2 external calls (loads, signing_secret_fingerprint).


##### `slack_manifest_handler`  (lines 272–287)

```
async def slack_manifest_handler(ctx: ToolContext, args: SlackManifestInput) -> ToolResult
```

**Purpose**: Produces a ready-to-paste Slack app manifest for the manifest setup path. A manifest is Slack’s text blueprint for creating an app with the right permissions and event subscriptions.

**Data flow**: It receives the desired bot display name, checks that the name uses allowed plain characters and length, then reads the deployment’s public base URL. It builds the Slack events and interactivity URLs and inserts them, along with the bot name, into the manifest template. The output is the manifest text as a tool result.

**Call relations**: This is the handler behind the `slack_app_manifest` tool. It uses `_events_url` so the generated app points back to this deployment’s Slack endpoint.

*Call graph*: calls 1 internal fn (_events_url); 3 external calls (__init__, __init__, match).


##### `slack_channels_handler`  (lines 290–313)

```
async def slack_channels_handler(ctx: ToolContext, args: SlackChannelsInput) -> ToolResult
```

**Purpose**: Lists and searches Slack conversations that the connected bot can see. This helps the agent find a channel or direct message by human clues, like a name or participant, instead of requiring a Slack ID.

**Data flow**: It reads the stored bot token and saved Slack identity. If Slack is not connected or identity is missing, it raises a clear setup error. Otherwise it runs a Slack conversation search using the bot token, bot user ID, and the user’s query. It returns JSON containing matching conversations and a flag saying whether the search was cut short because there were more results than one scan covers. The result is marked untrusted because names, topics, and purposes come from Slack users.

**Call relations**: This is the handler behind the `slack_channels` runtime tool. It relies on the Slack identity saved during `slack_connect`, then hands the actual Slack paging and matching work to `SlackConversationSearch`.

*Call graph*: 5 external calls (__init__, __init__, __init__, dumps, read_identity).


##### `_token_diagnosis`  (lines 316–322)

```
def _token_diagnosis(error: str) -> str
```

**Purpose**: Turns Slack authentication error codes into clearer setup advice. It helps users understand whether they likely copied the wrong token or hit another Slack error.

**Data flow**: It receives a Slack error string. If the error is one of the common token rejection cases, it returns a message telling the user to re-copy the Bot User OAuth Token. Otherwise it returns a more general `auth.test` failure message with the error included.

**Call relations**: The manifest identity setup helper calls this when Slack rejects or cannot validate the bot token. Its message is placed into the connection state response shown to the user.

*Call graph*: called by 1 (_derive_manifest_identity).
