# Slack surface  `stage-5.2`

The Slack surface is the system’s Slack-facing doorway. It is part of the main communication loop: Slack sends events in, ufo turns them into conversations, and replies go back to Slack. The package marker, __init__.py, simply makes this extension importable by the rest of the code.

surface.py is the front desk. It checks that incoming Slack requests are genuine, receives messages and button clicks, starts or continues the right ufo conversation, and posts replies, progress notes, and files back to Slack. tools.py adds guided chat tools for connecting a UFO workspace to Slack and for searching Slack conversations later, so setup and search can happen through the agent.

mentions.py translates Slack’s special encoded text, such as user and channel tags, into readable form and extracts who or what was mentioned. attribution.py manages the small footer added to connector-sent Slack messages, including the bot mention, while making sure that footer does not look like a real user message. hooks.py adds the proper Slack bot mention to outgoing connector messages when it can do so quickly and safely.

## Files in this stage

### Extension setup tools
Package scaffolding and guided tools connect UFO workspaces to Slack and expose Slack search in conversation.

### `extensions/slack/ufo_ext_slack/__init__.py`

`other` · `import time`

This is an empty Python package file. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools in other files, and this label lets the rest of the program find them by name.

For this Slack extension, the file makes `ufo_ext_slack` available as a package. Other parts of the system can then import modules inside `extensions/slack/ufo_ext_slack`. Because the file is empty, it does not set up Slack, load settings, connect to any service, or expose helper functions. Its importance is structural: without it, some Python environments or tooling may not recognize the directory as a package, which could make imports fail.


### `extensions/slack/ufo_ext_slack/tools.py`

`orchestration` · `setup and request handling`

This file is the Slack setup control panel for the agent. It supports two ways to connect Slack. The easier path is OAuth, which means an admin gets an “Add to Slack” link and Slack sends the app back the needed bot token after approval. The fallback path is a Slack app manifest, which is a ready-made recipe the user pastes into Slack to create their own app, then privately supplies the bot token and signing secret.

The file keeps both paths aiming at the same end state: a proven Slack identity for the workspace, plus a later proof that Slack can actually reach this UFO deployment. Think of it like setting up a doorbell: first the system confirms which house the doorbell belongs to, then it waits for someone to press it once to prove the wiring works.

The main tool, `slack_connect`, checks saved credentials, proves the bot identity, binds the Slack team to the current UFO workspace, and reports a simple state such as `not_configured`, `not_installed`, `pending`, or `connected`. Another tool, `slack_app_manifest`, prints the exact Slack app configuration needed for the manual setup path. The runtime tool, `slack_channels`, uses the connected bot token to list and search Slack channels and direct messages so the agent can find a conversation by name or people, not only by a raw Slack ID.

#### Function details

##### `_events_url`  (lines 139–140)

```
def _events_url(public_base_url: str) -> str
```

**Purpose**: Builds the public web address Slack should call when it sends events to this deployment. It makes sure the base URL and the Slack route join cleanly even if the base URL has a trailing slash.

**Data flow**: It receives the deployment’s public base URL as text. It trims any trailing slash, adds `/surface/slack`, and returns the finished Slack events URL.

**Call relations**: The setup flow calls this when it needs to tell Slack where to send messages and verification requests. `slack_connect_handler` uses it while reporting setup status, and `slack_manifest_handler` uses it when creating the Slack app manifest.

*Call graph*: called by 2 (slack_connect_handler, slack_manifest_handler).


##### `_state`  (lines 143–145)

```
def _state(state: str, hint: str, events_url: str | None, **extra: object) -> ToolResult
```

**Purpose**: Packages a human-readable setup status into the standard tool result format. This keeps every Slack setup response shaped the same way, with a state, hint, events URL, and optional extra details.

**Data flow**: It receives a state name, a helpful hint, an optional Slack events URL, and any extra fields. It turns those into a JSON text payload, wraps that text in tool content, and returns it as a tool result.

**Call relations**: This is the shared response builder for the install state machine. `slack_connect_handler`, `_oauth_link`, and `_derive_manifest_identity` call it whenever they need to tell the agent or user what stage Slack setup is in.

*Call graph*: called by 3 (_derive_manifest_identity, _oauth_link, slack_connect_handler); 3 external calls (__init__, __init__, dumps).


##### `slack_connect_handler`  (lines 148–193)

```
async def slack_connect_handler(ctx: ToolContext, args: SlackConnectInput) -> ToolResult
```

**Purpose**: Runs one pass through Slack connection setup and reports what still needs to happen. It is safe to call repeatedly: before setup, during setup, and after Slack is connected.

**Data flow**: It reads the current workspace, public URL, saved Slack credentials, stored Slack identity, and whether Slack has already reached the deployment. If there is no identity yet, it either creates an OAuth install link or tries to prove the manual manifest credentials. Once identity exists, it binds the Slack team to this UFO workspace and returns a JSON status such as `pending` or `connected`.

**Call relations**: This is the main handler behind the `slack_connect` tool. It asks `_events_url` for the callback address, uses `_oauth_link` for the one-click install path, uses `_derive_manifest_identity` for the manual app path, checks `_verified` to see whether Slack has successfully called back, and uses `_state` to explain the result.

*Call graph*: calls 5 internal fn (_derive_manifest_identity, _events_url, _oauth_link, _state, _verified); 2 external calls (read_identity, slack_installation_id).


##### `_oauth_link`  (lines 196–226)

```
async def _oauth_link(ctx: ToolContext, events_url: str | None) -> ToolResult
```

**Purpose**: Creates the “Add to Slack” link for deployments that have their own Slack app configured. It also gives clear fallback messages when one-click install is unavailable or the speaker is not allowed to install it.

**Data flow**: It checks environment variables for the Slack app’s client ID and secret, checks whether the current speaker is an admin, and checks that the deployment has a public URL. If all checks pass, it starts a sealed credential authorization handoff, builds Slack’s authorization URL, and returns it in a setup-state response.

**Call relations**: `slack_connect_handler` calls this when no Slack identity exists and the requested setup method is OAuth. This function hands off to the tool context to begin credential authorization, then uses Slack URL helpers to produce the install link, and finally returns the result through `_state`.

*Call graph*: calls 3 internal fn (begin_credential_authorization, speaker_is_admin, _state); called by 1 (slack_connect_handler); 3 external calls (slack_authorize_url, slack_client_id, slack_oauth_redirect_uri).


##### `_derive_manifest_identity`  (lines 229–263)

```
async def _derive_manifest_identity(ctx: ToolContext, events_url: str | None) -> SlackIdentity | ToolResult
```

**Purpose**: Completes the manual “bring your own Slack app” setup path. It waits until both private secrets are present, then asks Slack who the bot belongs to and saves that identity.

**Data flow**: It checks the bot token and signing secret credential slots. If any are missing, it returns a `not_configured` response explaining what to collect. If both exist, it checks that the speaker is an admin, uses the bot token to resolve the Slack team and bot identity, and returns that identity; if Slack rejects the token, it returns a helpful diagnosis instead.

**Call relations**: `slack_connect_handler` calls this when setup is using the manifest path and no identity has been proven yet. It uses `_state` for user-facing status messages and `_token_diagnosis` to turn Slack token errors into clearer instructions.

*Call graph*: calls 3 internal fn (speaker_is_admin, _state, _token_diagnosis); called by 1 (slack_connect_handler); 1 external calls (__init__).


##### `_verified`  (lines 266–285)

```
async def _verified(ctx: ToolContext) -> bool
```

**Purpose**: Checks whether Slack has successfully reached this deployment using the current signing secret. This is the final proof that the Slack app is not only installed, but also correctly pointed at this server.

**Data flow**: It looks for a verification marker in blob storage for the current workspace. If the marker exists and can be read as JSON, it reads the current Slack signing secret, fingerprints it, and compares that fingerprint with the marker. It returns `true` only when they match.

**Call relations**: `slack_connect_handler` calls this after identity is proven and the Slack team is bound. If this returns false, setup remains `pending`; if it returns true, setup is reported as `connected`.

*Call graph*: called by 1 (slack_connect_handler); 3 external calls (loads, signing_secret_fingerprint, url_verified_blob_key).


##### `slack_manifest_handler`  (lines 288–303)

```
async def slack_manifest_handler(ctx: ToolContext, args: SlackManifestInput) -> ToolResult
```

**Purpose**: Produces the ready-to-paste Slack app manifest for the manual setup path. This saves users from manually choosing Slack permissions, event subscriptions, and request URLs.

**Data flow**: It receives the desired bot display name and checks that it fits Slack’s simple name rules. It reads the deployment’s public base URL, builds the Slack event and interactivity URLs, fills those values into the manifest template, and returns the manifest as plain text tool output.

**Call relations**: This is the handler behind the `slack_app_manifest` tool. It uses `_events_url` to make the correct Slack request URL and returns the finished manifest directly to the agent or user.

*Call graph*: calls 1 internal fn (_events_url); 3 external calls (__init__, __init__, match).


##### `slack_channels_handler`  (lines 306–329)

```
async def slack_channels_handler(ctx: ToolContext, args: SlackChannelsInput) -> ToolResult
```

**Purpose**: Searches the connected Slack workspace for conversations the agent may need to use. It helps find channels, group direct messages, and direct messages by names, topics, purposes, or people.

**Data flow**: It reads the saved Slack bot token and the stored Slack identity for the current workspace. It then runs a Slack conversation search using the bot token, bot user ID, and the user’s query. It returns JSON containing matching conversations and whether the search was cut short because there were more results than one scan covered.

**Call relations**: This is the handler behind the `slack_channels` tool. Before it can search, `slack_connect` must already have stored a usable token and identity. It delegates the actual Slack paging and matching work to `SlackConversationSearch`, then wraps the found conversations in a tool result.

*Call graph*: 5 external calls (__init__, __init__, __init__, dumps, read_identity).


##### `_token_diagnosis`  (lines 332–338)

```
def _token_diagnosis(error: str) -> str
```

**Purpose**: Turns Slack authentication error codes into plain instructions. It helps users understand whether they likely copied the wrong token or Slack reported a different failure.

**Data flow**: It receives a Slack error string. If the error is one of the common token rejection cases, it returns a message telling the user to re-copy the Bot User OAuth Token; otherwise it returns a more general `auth.test` failure message with the error included.

**Call relations**: _derive_manifest_identity calls this when Slack rejects the manual setup token. Its message is then placed into a `not_configured` state response so the setup flow can continue with useful guidance.

*Call graph*: called by 1 (_derive_manifest_identity).


### Slack message semantics
Slack-specific helpers normalize mentions and add safe connector attribution without confusing bot-directed messages.

### `extensions/slack/ufo_ext_slack/attribution.py`

`domain_logic` · `Slack message send and receive handling`

When this system sends a Slack message through a connector, the message is not rendered by the usual Slack surface code. So this file supplies the missing “sent by this product” footer in the right Slack-specific form: a mention of the deployed bot user. That lets a person who sees the message click through to the bot.

The file also solves the opposite problem. Slack may report a message as mentioning the bot if the only mention is inside this footer. Without a guard, the system could treat its own attribution line as a user trying to talk to the agent. That would be like mistaking a signature at the bottom of a letter for the letter’s main message.

The pieces work together in two directions. On the outgoing side, the code checks whether a connector call is really a Slack message send, then appends the bot-mention footer unless an attribution footer is already present. On the incoming side, it gathers all text Slack might hide in the message structure, strips out known attribution text, and only then checks whether the bot was truly mentioned. This keeps sent messages identifiable without creating false “the user addressed me” signals.

#### Function details

##### `is_slack_send`  (lines 36–45)

```
def is_slack_send(provider: str, slug: str) -> bool
```

**Purpose**: This function decides whether a connector call is the kind of call that publishes a Slack message. It uses the same test as the connector attribution code, so the Slack extension and connector tool agree about which sends need a footer.

**Data flow**: It receives a connector provider name and a slug, which is a short machine-readable action name. It lowercases the slug, checks that the provider is Slack, that the slug refers to a message, and that it includes one of the known send verbs. It returns true when all of those are true, otherwise false.

**Call relations**: This is the gatekeeper for the outgoing attribution path. Other code can call it before deciding whether to run the footer-adding logic, so non-Slack calls or non-send actions are left alone.


##### `mention_attributed`  (lines 48–55)

```
def mention_attributed(arguments: dict[str, JsonValue], bot_user_id: str) -> dict[str, JsonValue]
```

**Purpose**: This function returns Slack send arguments with the attribution footer added, using a mention of the bot user as the footer subject. It leaves the arguments unchanged if they already contain an attribution footer, so repeated processing does not stack duplicate footers.

**Data flow**: It receives the connector send arguments and the Slack bot user ID. It formats the standard attribution subject with that bot ID, then passes the arguments and subject to the shared connector helper that adds the footer in the expected shape. The output is a new or unchanged arguments dictionary ready to be sent.

**Call relations**: This function relies on the connector library for the shared footer format instead of rebuilding it locally. It first formats the bot mention through `UFO_ATTRIBUTION_MENTION_SUBJECT.format`, then hands the real footer insertion to `ufo_ext_connectors.tools.attributed_arguments`, keeping Slack-specific wording and generic connector behavior joined together.

*Call graph*: 2 external calls (format, attributed_arguments).


##### `addressing_mention`  (lines 58–68)

```
def addressing_mention(text: str, bot_user_id: str) -> bool
```

**Purpose**: This function checks whether some Slack text truly mentions the bot, ignoring mentions that appear only inside this system's own attribution footer. It helps tell the difference between a user calling on the agent and a message merely signed by the agent.

**Data flow**: It receives a text string and the bot user ID. It first removes any known attribution footer text from the string. Then it searches the remaining text for Slack's mention form for that bot, such as `<@BOTID>`. It returns true only if the mention remains after attribution is stripped.

**Call relations**: This is used on the incoming side of the Slack flow, where the system decides whether a message is addressed to the agent. It delegates footer removal to `ufo_ext_connectors.tools.attribution_stripped`, then performs the bot-mention test on the cleaned text.

*Call graph*: 1 external calls (attribution_stripped).


##### `message_bodies`  (lines 71–77)

```
def message_bodies(event: Mapping[str, object]) -> tuple[str, ...]
```

**Purpose**: This function collects every piece of text in a Slack message event where a bot mention might appear. It looks beyond the top-level message text because Slack messages can store text inside nested blocks and rich text elements.

**Data flow**: It receives a Slack event represented like a dictionary. It reads the normal `text` field, then reads the `blocks` field and asks `_nested_strings` to pull out every string buried inside it. It returns all of those strings as a tuple, with missing top-level text represented as an empty string.

**Call relations**: This prepares incoming Slack messages for mention checks such as `addressing_mention`. Since it calls `_nested_strings`, it can see mentions in structured Slack message content, including the footer this system appends in a context block.

*Call graph*: calls 1 internal fn (_nested_strings).


##### `_nested_strings`  (lines 80–89)

```
def _nested_strings(value: object) -> Iterator[str]
```

**Purpose**: This helper walks through a nested Slack data structure and yields every string it finds. It is useful because Slack blocks can be dictionaries inside lists inside dictionaries, rather than one simple text field.

**Data flow**: It receives any value. If the value is a string, it yields that string. If it is a mapping, meaning a dictionary-like object, it searches each value inside it. If it is a list, it searches each item. Values of other kinds are ignored. The result is a stream of strings found anywhere underneath the original value.

**Call relations**: This is the recursive text collector used by `message_bodies`. `message_bodies` gives it the Slack `blocks` content, and `_nested_strings` hands back the hidden text pieces so later logic can decide whether the bot was genuinely mentioned.

*Call graph*: called by 1 (message_bodies).


### `extensions/slack/ufo_ext_slack/hooks.py`

`domain_logic` · `before external tool use / Slack message send`

This file is a small safety-and-attribution hook for Slack sends. The connector tool can send a Slack message, but it is generic: it does not know which Slack bot user belongs to this workspace. The Slack extension does know that, because the Slack setup path stores the bot user ID after proving the install. So this hook steps in just before the external tool runs and rewrites the outgoing message arguments to include a footer that mentions the correct bot user.

The important detail is that this hook runs at a gate: if it raises an error or takes too long, the whole tool call can be denied and the Slack message will not be sent. Since the footer is only cosmetic, the code is deliberately cautious. It gives the store lookup its own short timeout, catches every failure, logs the problem, and then returns “no change.” In that fallback case, the normal connector send can continue and the connector’s generic attribution can be used instead.

Think of it like a receptionist adding a name badge to an outgoing package. If the badge printer is slow or broken, the package should still leave the building.

#### Function details

##### `attribute_connector_send`  (lines 30–44)

```
async def attribute_connector_send(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook checks whether an upcoming external tool call is really a Slack send. If it is, and the extension can find the workspace’s Slack bot user ID, it rewrites the outgoing message so the footer mentions that bot.

**Data flow**: It receives a hook context containing the pending tool call. If the payload is a connector tool call for Slack, it asks for the mirrored bot user ID. When a valid ID is found, it passes the original message arguments and that ID into the attribution helper, then returns a modified tool input. If the call is not a Slack send, or the ID is missing, it returns nothing, meaning the tool input stays unchanged.

**Call relations**: During the pre-tool-use moment, this function is the decision point. It relies on is_slack_send to recognize the relevant connector call, asks _mirrored_self_user_id for the bot identity, uses mention_attributed to add the footer, and wraps the changed call in ModifyInput so the tool runner receives the edited arguments.

*Call graph*: calls 1 internal fn (_mirrored_self_user_id); 3 external calls (__init__, is_slack_send, mention_attributed).


##### `_mirrored_self_user_id`  (lines 47–61)

```
async def _mirrored_self_user_id(ctx: HookContext) -> str | None
```

**Purpose**: This helper tries to read the Slack bot user ID that the Slack surface previously stored for this workspace. It is intentionally fail-safe: if the read is slow, broken, missing, or invalid, it returns no ID instead of risking a blocked send.

**Data flow**: It receives the hook context and reads the extension’s scoped store at the known self-user-ID key. The read must finish within a short timeout. If the read fails, it logs the error type and returns None. If the value is a string matching the expected Slack bot-user-ID pattern, it returns that string; otherwise it returns None.

**Call relations**: attribute_connector_send calls this helper only after it has recognized a Slack send. This helper uses asyncio.timeout to keep the store read from holding up the gated hook, re.match to confirm the stored value looks like a real bot user ID, and log to record failed reads without stopping the send path.

*Call graph*: called by 1 (attribute_connector_send); 3 external calls (timeout, match, log).


### `extensions/slack/ufo_ext_slack/mentions.py`

`domain_logic` · `message intake and text preparation`

Slack does not send messages exactly as people see them. A user mention may arrive as something like `<@U0BG8632NDS>`, a channel as `<#C0271QK6M|ufo-eng>`, and a link as `<https://example.com|the docs>`. Those forms are useful for Slack’s computers, but they are poor reading material for people and for an AI model trying to understand a conversation.

This file is the translator between Slack’s wire format and readable text. It can scan a message for mentioned user IDs or channel IDs, so the caller can ask Slack for their names. Then `render_markup` rewrites the encoded parts into plain forms like `@Jane Doe`, `#ufo-eng`, `@here`, or `the docs (https://example.com)`.

A key safety choice is that this file does not automatically turn Slack’s escaped `&lt;`, `&gt;`, and `&amp;` back into `<`, `>`, and `&` during normal markup rendering. That matters because other people’s messages may later be placed inside a larger prompt or document. Keeping those escapes is like keeping quotation marks around someone else’s words: it prevents their text from accidentally breaking out of where it belongs. The separate `unescape` function exists only for cases where it is safe to restore the original characters.

#### Function details

##### `mentioned_users`  (lines 34–37)

```
def mentioned_users(text: str) -> frozenset[str]
```

**Purpose**: Finds the Slack user IDs mentioned in a piece of message text. A caller can use these IDs to ask Slack for the users’ real display names before rendering the message.

**Data flow**: It receives raw Slack message text. It asks the shared mention-finding helper to look only for user-style mentions, marked with `@`. It returns a frozen set, meaning an unchangeable collection, of the user IDs it found.

**Call relations**: When code needs to know which user names must be resolved, it calls this function first. This function delegates the actual scanning work to `_mentioned`, giving it the user marker so the shared scanner knows what to collect.

*Call graph*: calls 1 internal fn (_mentioned).


##### `mentioned_channels`  (lines 40–42)

```
def mentioned_channels(text: str) -> frozenset[str]
```

**Purpose**: Finds the Slack channel IDs mentioned in a piece of message text. A caller can then ask Slack for channel details and use those names when showing or storing the message.

**Data flow**: It receives raw Slack message text. It asks the shared mention-finding helper to look only for channel-style mentions, marked with `#`. It returns an unchangeable set of the channel IDs it found.

**Call relations**: This is the channel counterpart to `mentioned_users`. It calls `_mentioned` with the channel marker, so the same scanning logic can be reused without mixing up users and channels.

*Call graph*: calls 1 internal fn (_mentioned).


##### `_mentioned`  (lines 45–50)

```
def _mentioned(text: str, kind: str) -> frozenset[str]
```

**Purpose**: Does the common work of scanning Slack text for encoded entities of one requested kind, such as users or channels. It exists so user and channel mention detection stay consistent.

**Data flow**: It receives message text and a marker telling it what kind of entity to look for. It walks through every Slack-shaped encoded item in the text, keeps only the ones whose kind matches the marker and that have an ID, and returns those IDs as an unchangeable set.

**Call relations**: `mentioned_users` and `mentioned_channels` both call this helper. They choose the kind of mention they care about, and `_mentioned` performs the shared pattern matching behind the scenes.

*Call graph*: called by 2 (mentioned_channels, mentioned_users).


##### `render_markup`  (lines 53–63)

```
def render_markup(text: str, names: Mapping[str, str]) -> str
```

**Purpose**: Rewrites Slack’s encoded mentions, channels, broadcasts, and links into readable text. This is the main function used when a Slack message is admitted into the system so later readers see the same understandable version.

**Data flow**: It receives raw Slack message text and a mapping from Slack IDs to human-readable names. It looks for Slack entity patterns, replaces each one with a readable version when possible, and leaves unknown or unsafe-looking text unchanged. It returns the rewritten message string.

**Call relations**: This function is the main public translator in the file. For each encoded entity it finds, it uses `_entity` to decide the exact readable replacement, such as turning a known user ID into `@Real Name` or a labeled link into `label (url)`.


##### `unescape`  (lines 66–75)

```
def unescape(text: str) -> str
```

**Purpose**: Turns Slack’s escaped text sequences back into the characters a person originally typed. It is intentionally separate from `render_markup` because unescaping someone else’s quoted words can be unsafe in later prompt or document construction.

**Data flow**: It receives text that may contain Slack escape sequences like `&amp;`, `&lt;`, and `&gt;`. It replaces those sequences with `&`, `<`, and `>` in a fixed order. It returns the restored text.

**Call relations**: This function stands apart from the normal entity-rendering flow. Other code should call it only when it is working with the current speaker’s own words, not with bystanders’ messages carried into a larger context.


##### `_entity`  (lines 78–94)

```
def _entity(match: re.Match[str], names: Mapping[str, str]) -> str
```

**Purpose**: Decides how one matched Slack entity should appear in readable text. It covers user mentions, channel mentions, broadcast mentions like `@here`, and links.

**Data flow**: It receives one matched Slack entity and the ID-to-name mapping. For user and channel entities, it prefers a resolved name from the mapping, then Slack’s label, and otherwise leaves the original encoded text alone. For broadcasts, it returns a readable broadcast name when recognized. For links, it returns either the URL alone or `label (url)` when the label gives useful extra wording.

**Call relations**: `render_markup` relies on this helper for each encoded piece it finds. `_entity` is the small decision table that turns one Slack code at a time into the text that humans, transcripts, titles, and models should read.


### Slack interaction surface
The Slack surface verifies requests, ingests events and interactions, drives UFO conversations, and sends replies back to Slack.

### `extensions/slack/ufo_ext_slack/surface.py`

`io_transport` · `request handling, install, live turn feedback, reply delivery`

This file is the bridge between Slack and the core ufo system. Without it, Slack users could not install the app, mention the agent, send files, answer agent questions with buttons, or receive replies in the right Slack thread. It also protects the system: every incoming Slack request is checked with Slack’s signing secret before it is trusted, like checking a wax seal before opening a letter.

The file first figures out which workspace a request belongs to. It supports two install paths: Slack OAuth, where Slack gives this deploy a bot token, and a bring-your-own Slack app path, where the workspace owner supplies credentials. Once installed, incoming message events are reduced into a clean internal “inbound” shape: who spoke, which Slack thread they spoke in, whether they addressed the bot, what files they attached, and what conversation audience should see the answer.

When a message should become a ufo turn, the file fetches useful Slack context, downloads attached files into the workspace, resolves the speaker to a member when possible, and admits the turn to core. While the turn runs, background follower tasks update Slack’s native “Thinking…” status and, for very long runs, post occasional progress messages. When the turn finishes, this file formats the answer for Slack, splits long text safely, renders question buttons, uploads shared files, and records enough delivery progress to avoid duplicate replies after retries.

#### Function details

##### `_env_signing_secret`  (lines 201–205)

```
def _env_signing_secret() -> str | None
```

**Purpose**: Reads the deploy-wide Slack signing secret from environment variables. This is the fallback secret used when a workspace does not store its own Slack app secret.

**Data flow**: It reads one environment variable, treats an empty value as missing, and returns either the secret text or null.

**Call relations**: _ctx_signing_secret and _auth_signing_secret call this when a workspace-specific secret is not available.

*Call graph*: called by 2 (_auth_signing_secret, _ctx_signing_secret).


##### `_ctx_signing_secret`  (lines 208–215)

```
async def _ctx_signing_secret(ctx: SurfaceContext) -> str | None
```

**Purpose**: Finds the signing secret that should verify a request after a workspace is already known. It prefers the workspace’s stored secret and falls back to the deploy secret.

**Data flow**: It asks the surface context for the Slack signing-secret credential; if that slot is unset, it reads the environment fallback and returns whichever secret exists.

**Call relations**: ingest and interactive call this before trusting Slack event or button-click requests.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 2 (ingest, interactive).


##### `_auth_signing_secret`  (lines 218–226)

```
async def _auth_signing_secret(auth: SurfaceAuth, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the signing secret during early request routing, before the main surface context is bound. It lets the system verify a Slack request before deciding which workspace owns it.

**Data flow**: It asks the shared auth helper for the workspace’s secret; if missing, it uses the deploy fallback; if the workspace is unknown, it returns null.

**Call relations**: resolve_workspace uses this while mapping an incoming Slack team id to a trusted workspace.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 1 (resolve_workspace).


##### `slack_client_id`  (lines 229–233)

```
def slack_client_id() -> str
```

**Purpose**: Returns the Slack OAuth client id configured for this deployment. It fails loudly if OAuth install cannot work because the value is missing.

**Data flow**: It reads an environment variable and returns its text; if absent, it raises an error instead of building a broken OAuth request.

**Call relations**: slack_oauth_exchange uses this when exchanging Slack’s temporary OAuth code for a bot token.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_client_secret`  (lines 236–240)

```
def slack_client_secret() -> str
```

**Purpose**: Returns the Slack OAuth client secret configured for this deployment. This secret proves the app’s identity to Slack during installation.

**Data flow**: It reads the environment variable and returns it, or raises an error when it is not configured.

**Call relations**: slack_oauth_exchange uses it alongside the client id during the OAuth token exchange.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_oauth_redirect_uri`  (lines 243–245)

```
def slack_oauth_redirect_uri(public_base_url: str) -> str
```

**Purpose**: Builds the callback URL Slack should redirect to after an OAuth install. This must match the URL configured in Slack.

**Data flow**: It takes the public base URL, removes any trailing slash, and appends the Slack surface OAuth path.

**Call relations**: oauth_callback uses the same value during code exchange, matching the authorize link Slack saw earlier.

*Call graph*: called by 1 (oauth_callback).


##### `slack_authorize_url`  (lines 248–260)

```
def slack_authorize_url(client_id: str, redirect_uri: str, state: str) -> str
```

**Purpose**: Builds the “Add to Slack” link for installing the app. The link includes the permissions the bot needs and a sealed state value tying the install to one workspace.

**Data flow**: It takes a client id, redirect URL, and state string, URL-encodes them with the requested Slack scopes, and returns Slack’s authorization URL.

**Call relations**: This is the outward-facing partner to oauth_callback: it starts the OAuth install that the callback completes.

*Call graph*: 1 external calls (urlencode).


##### `SlackIdentityError.__init__`  (lines 264–266)

```
def __init__(self, error: str)
```

**Purpose**: Creates an error that carries Slack identity failure text. It is used when Slack identity proof or OAuth responses are missing, malformed, or rejected.

**Data flow**: It stores the error string on the object and passes the same text to the normal runtime error base class.

**Call relations**: SlackIdentityResolver._prove and slack_oauth_exchange raise this when Slack cannot prove a usable team and bot user identity.

*Call graph*: called by 2 (_prove, slack_oauth_exchange).


##### `identity_blob_key`  (lines 280–281)

```
def identity_blob_key(workspace_id: UUID) -> str
```

**Purpose**: Builds the storage key for a workspace’s Slack identity record. This keeps each workspace’s Slack team and bot identity separate.

**Data flow**: It takes a workspace id and returns a predictable blob-store path under that workspace.

**Call relations**: read_identity, SlackIdentityResolver.resolve, and oauth_callback use this key to read or write the identity record.

*Call graph*: called by 3 (resolve, oauth_callback, read_identity).


##### `bot_token_fingerprint`  (lines 284–285)

```
def bot_token_fingerprint(bot_token: str) -> str
```

**Purpose**: Creates a safe fingerprint of a Slack bot token. The fingerprint lets the code tell whether a stored identity belongs to the current token without storing the token in the identity record.

**Data flow**: It takes the token text, hashes it with SHA-256, and returns the hex digest.

**Call relations**: read_identity compares this fingerprint; OAuth and manifest identity proof write it when recording identity.

*Call graph*: called by 3 (_prove, oauth_callback, read_identity); 1 external calls (sha256).


##### `read_identity`  (lines 288–302)

```
async def read_identity(blob: BlobStore, workspace_id: UUID, bot_token: str) -> SlackIdentity | None
```

**Purpose**: Reads the stored Slack identity for a workspace, but only if it matches the current bot token. This prevents an old identity from being reused after reinstalling the Slack app.

**Data flow**: It checks whether the blob exists, parses it, compares its token fingerprint with the supplied token, and returns the identity or null.

**Call relations**: Identity resolution, request ingest, and self-user lookup all call this before trusting Slack team or bot user ids.

*Call graph*: calls 4 internal fn (exists, get, bot_token_fingerprint, identity_blob_key); called by 3 (resolve, _identity, resolve_self_user_id).


##### `resolve_self_user_id`  (lines 305–311)

```
async def resolve_self_user_id(ctx: SurfaceIdentityContext) -> str | None
```

**Purpose**: Returns the bot user id for this Slack installation when it is known. Other parts of the extension use this to recognize the app’s own Slack account.

**Data flow**: It reads the bot token credential, loads the matching identity record, and returns the bot user id or null.

**Call relations**: This is a small identity lookup helper for the surface identity seam.

*Call graph*: calls 1 internal fn (read_identity); 1 external calls (credential).


##### `_identity`  (lines 314–322)

```
async def _identity(ctx: SurfaceContext) -> SlackIdentity | None
```

**Purpose**: Loads the current workspace’s Slack identity during request handling. If found, it also mirrors the bot user id into the extension store for hook-time code.

**Data flow**: It reads the bot token, reads the matching identity blob, optionally writes the bot user id mirror, and returns the identity or null.

**Call relations**: ingest and interactive call this after signature verification and before processing Slack payloads.

*Call graph*: calls 3 internal fn (credential, _mirror_self_user_id, read_identity); called by 2 (ingest, interactive).


##### `_mirror_self_user_id`  (lines 328–344)

```
async def _mirror_self_user_id(workspace_id: UUID, bot_user_id: str) -> None
```

**Purpose**: Copies the proven Slack bot user id into a simpler extension store. Hooks can read that store even when they cannot access the blob store.

**Data flow**: It checks an in-process cache, writes the bot id to the scoped store if needed, logs failures, and updates the cache on success.

**Call relations**: _identity and oauth_callback call this so later hook code can identify the bot’s own Slack account.

*Call graph*: called by 2 (_identity, oauth_callback); 1 external calls (__init__).


##### `SlackIdentityResolver.resolve`  (lines 358–366)

```
async def resolve(self) -> SlackIdentity
```

**Purpose**: Gets or proves the Slack identity for a bring-your-own-app install. It avoids repeating Slack calls if the identity is already stored and current.

**Data flow**: It tries to read an existing identity; if none matches, it proves the token with Slack, writes the result to blob storage, and returns it.

**Call relations**: _run_identity_proof calls this in the background when an inbound request finds credentials but no identity yet.

*Call graph*: calls 3 internal fn (_prove, identity_blob_key, read_identity).


##### `SlackIdentityResolver._prove`  (lines 368–393)

```
async def _prove(self) -> SlackIdentity
```

**Purpose**: Asks Slack’s auth.test endpoint what team and bot user a pasted bot token belongs to. This confirms that the token is real and identifies the workspace app.

**Data flow**: It sends the token to Slack, parses the JSON response, validates the team and bot user id shapes, and returns a SlackIdentity.

**Call relations**: SlackIdentityResolver.resolve calls this only when no usable stored identity exists.

*Call graph*: calls 2 internal fn (__init__, bot_token_fingerprint); called by 1 (resolve); 3 external calls (__init__, AsyncClient, match).


##### `_prove_identity_in_background`  (lines 399–412)

```
def _prove_identity_in_background(ctx: SurfaceContext) -> None
```

**Purpose**: Starts a background identity proof for a workspace that has credentials but no stored Slack identity yet. This lets the current request fail quickly while a retry may succeed.

**Data flow**: It checks whether a proof task is already running for the workspace; if not, it creates one and registers cleanup when it finishes.

**Call relations**: ingest and interactive call this when identity is missing but may be derivable from stored credentials.

*Call graph*: calls 1 internal fn (_run_identity_proof); called by 2 (ingest, interactive); 1 external calls (create_task).


##### `_prove_identity_in_background._untrack`  (lines 408–410)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Removes a finished background identity-proof task from the in-process task map. This prevents old task references from piling up.

**Data flow**: It receives the completed task and deletes it from the workspace task map if it is still the tracked task.

**Call relations**: It is attached as the completion callback for tasks created by _prove_identity_in_background.


##### `_run_identity_proof`  (lines 415–422)

```
async def _run_identity_proof(ctx: SurfaceContext) -> None
```

**Purpose**: Performs the actual background Slack identity proof. It logs failures instead of letting a background exception disappear silently.

**Data flow**: It reads the bot token, constructs a SlackIdentityResolver, asks it to resolve identity, and logs any identity or unexpected errors.

**Call relations**: _prove_identity_in_background schedules this task.

*Call graph*: calls 1 internal fn (credential); called by 1 (_prove_identity_in_background); 1 external calls (__init__).


##### `url_verified_blob_key`  (lines 425–431)

```
def url_verified_blob_key(workspace_id: UUID) -> str
```

**Purpose**: Builds the storage key for the marker saying Slack successfully reached this deploy. That marker helps the setup flow know the request URL and secret are working.

**Data flow**: It takes a workspace id and returns the blob path for the Slack URL-verification marker.

**Call relations**: _mark_url_verified uses this key when recording a verified inbound Slack request.

*Call graph*: called by 1 (_mark_url_verified).


##### `signing_secret_fingerprint`  (lines 434–437)

```
def signing_secret_fingerprint(signing_secret: str) -> str
```

**Purpose**: Creates a safe fingerprint of a Slack signing secret. This lets setup detect secret rotation without storing the raw secret in the marker.

**Data flow**: It hashes the secret text with SHA-256 and returns the digest.

**Call relations**: _mark_url_verified stores this fingerprint with the verification marker.

*Call graph*: called by 1 (_mark_url_verified); 1 external calls (sha256).


##### `slack_oauth_exchange`  (lines 451–478)

```
async def slack_oauth_exchange(code: str, redirect_uri: str) -> SlackInstall
```

**Purpose**: Trades Slack’s temporary OAuth code for a real bot token and app identity. This is the central step of the standard “Add to Slack” install.

**Data flow**: It sends the code, client id, client secret, and redirect URL to Slack, checks Slack’s response, validates the returned ids, and returns a SlackInstall.

**Call relations**: oauth_callback calls this after validating the sealed install state.

*Call graph*: calls 4 internal fn (__init__, _slack_ok, slack_client_id, slack_client_secret); called by 1 (oauth_callback); 3 external calls (__init__, AsyncClient, match).


##### `SlackConversationSearch.run`  (lines 562–576)

```
async def run(self) -> SlackConversationMatches
```

**Purpose**: Searches the Slack workspace for conversations matching a user’s query. It covers channels, private channels, group DMs, and DMs within strict page limits.

**Data flow**: It lists conversations, resolves people for DM-style conversations, builds normalized conversation records, filters them by query text, and returns matches plus a truncation flag.

**Call relations**: It coordinates the helper methods on SlackConversationSearch to do bounded Slack API search.

*Call graph*: calls 3 internal fn (_conversation, _list, _people); 2 external calls (__init__, AsyncClient).


##### `SlackConversationSearch._list`  (lines 578–597)

```
async def _list(self, client: httpx.AsyncClient) -> tuple[list[object], bool]
```

**Purpose**: Fetches pages of Slack conversations up to the configured limit. It reports whether Slack still had more pages after the limit.

**Data flow**: It repeatedly calls Slack conversations.list with cursor parameters, accumulates returned channel objects, and returns them with a paged-out boolean.

**Call relations**: run calls this before resolving people or filtering results.

*Call graph*: calls 3 internal fn (_next_cursor, _params, _slack_ok); called by 1 (run); 1 external calls (get).


##### `SlackConversationSearch._params`  (lines 599–607)

```
def _params(self, cursor: str) -> dict[str, str]
```

**Purpose**: Builds the query parameters for one conversations.list request. It keeps archived conversations out and requests all Slack conversation types this feature supports.

**Data flow**: It starts with type, archive, and page-size parameters, adds a cursor if supplied, and returns the parameter dictionary.

**Call relations**: _list calls this for each Slack page request.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._next_cursor`  (lines 609–612)

```
def _next_cursor(self, payload: dict[str, object]) -> str
```

**Purpose**: Extracts Slack’s next-page cursor from a response. Missing or malformed cursor data means there is no next page.

**Data flow**: It looks inside response_metadata.next_cursor and returns that string or an empty string.

**Call relations**: _list uses this after each page to decide whether to continue.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._people`  (lines 614–642)

```
async def _people(self, client: httpx.AsyncClient, listed: list[object]) -> tuple[dict[str, tuple[str, ...]], bool]
```

**Purpose**: Resolves the people in DMs and group DMs so they can be searched by name or email. It skips the bot itself.

**Data flow**: It gathers member ids for bounded DM conversations, looks up each distinct user once, converts users to labels, and returns labels per conversation plus a capped flag.

**Call relations**: run calls this after listing conversations; it uses _members, _kind, _label, and _slack_user.

*Call graph*: calls 4 internal fn (_kind, _label, _members, _slack_user); called by 1 (run).


##### `SlackConversationSearch._kind`  (lines 644–651)

```
def _kind(self, raw: dict[str, object]) -> SlackConversationKind
```

**Purpose**: Classifies a raw Slack conversation as a public channel, private channel, group DM, or one-to-one DM.

**Data flow**: It reads Slack boolean flags from the raw object and returns the matching kind string.

**Call relations**: _people, _members, and _conversation use this classification to decide how to interpret the raw Slack data.

*Call graph*: called by 3 (_conversation, _members, _people).


##### `SlackConversationSearch._members`  (lines 653–667)

```
async def _members(self, client: httpx.AsyncClient, raw: dict[str, object], convo_id: str) -> tuple[str, ...]
```

**Purpose**: Returns the member ids for a DM or group DM. One-to-one DMs carry the user directly; group DMs require a Slack members call.

**Data flow**: It checks the conversation kind, reads the embedded user for an IM, or calls Slack conversations.members for an MPIM and returns valid member id strings.

**Call relations**: _people calls this while building searchable people labels.

*Call graph*: calls 2 internal fn (_kind, _slack_ok); called by 1 (_people); 1 external calls (get).


##### `SlackConversationSearch._label`  (lines 669–674)

```
def _label(self, user: SlackUser | None, user_id: str) -> str
```

**Purpose**: Turns a Slack user lookup into a readable search label. It prefers name plus email when both are available.

**Data flow**: It receives a SlackUser or null and a fallback id, then returns name, email, both, or the raw id.

**Call relations**: _people uses this for the people text stored on SlackConversation records.

*Call graph*: called by 1 (_people).


##### `SlackConversationSearch._conversation`  (lines 676–693)

```
def _conversation(self, raw: object, people: dict[str, tuple[str, ...]]) -> SlackConversation | None
```

**Purpose**: Converts a raw Slack conversation object into the smaller safe model used by search results. Badly shaped objects are skipped.

**Data flow**: It validates the id, extracts name, kind, people labels, purpose, topic, and membership flag, and returns a SlackConversation or null.

**Call relations**: run calls this for each listed raw conversation before filtering.

*Call graph*: calls 2 internal fn (_kind, _nested_value); called by 1 (run); 1 external calls (__init__).


##### `SlackConversationSearch._nested_value`  (lines 695–697)

```
def _nested_value(self, field: object) -> str
```

**Purpose**: Safely reads Slack’s nested purpose or topic text. Slack wraps those fields in objects rather than plain strings.

**Data flow**: It accepts a field object, returns field.value if it is a string, otherwise returns an empty string.

**Call relations**: _conversation uses this for the purpose and topic fields.

*Call graph*: called by 1 (_conversation); 1 external calls (get).


##### `verify_slack_signature`  (lines 850–865)

```
def verify_slack_signature(headers: Mapping[str, str], body: bytes, signing_secret: str, now: float | None=None) -> None
```

**Purpose**: Checks that a Slack request really came from Slack and is recent. This protects the system from forged or replayed requests.

**Data flow**: It reads Slack timestamp and signature headers, rejects missing or stale data, recomputes the HMAC signature from the raw body and secret, and raises on mismatch.

**Call relations**: resolve_workspace, ingest, and interactive call this before trusting request contents.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 4 external calls (__init__, compare_digest, new, time).


##### `_slack_request_body`  (lines 872–891)

```
async def _slack_request_body(request: Request) -> bytes
```

**Purpose**: Reads and caches the raw Slack request body with a size limit. Signature verification must use the exact original bytes, so this prevents accidental re-reading changes.

**Data flow**: It returns a cached body if present, otherwise streams chunks from the request, stops if the size limit is exceeded, stores the body or overflow marker, and returns bytes.

**Call relations**: resolve_workspace, ingest, and interactive use this before parsing or verifying Slack requests.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 1 external calls (stream).


##### `url_verification_challenge`  (lines 894–903)

```
def url_verification_challenge(body: bytes) -> str | None
```

**Purpose**: Recognizes Slack’s URL verification handshake and extracts the challenge response. This lets Slack confirm the endpoint during setup.

**Data flow**: It parses the raw body as JSON, checks for type url_verification, and returns the challenge string or null.

**Call relations**: resolve_workspace and ingest call this before treating a request as a normal event.

*Call graph*: called by 2 (ingest, resolve_workspace); 1 external calls (loads).


##### `slack_team_hint`  (lines 906–923)

```
def slack_team_hint(body: bytes) -> str | None
```

**Purpose**: Extracts a Slack team id from an untrusted request body. It is only a hint until the request signature is verified.

**Data flow**: It tries JSON first and form-encoded interactive payloads second, then validates that the team id has Slack’s expected shape.

**Call relations**: resolve_workspace uses this hint to find the candidate workspace whose secret can verify the request.

*Call graph*: called by 1 (resolve_workspace); 3 external calls (loads, fullmatch, parse_qs).


##### `slack_installation_id`  (lines 926–927)

```
def slack_installation_id(team_id: str) -> str
```

**Purpose**: Builds the stable installation binding id for a Slack team. This is how one Slack workspace is tied to one ufo workspace.

**Data flow**: It prefixes the Slack team id with a marker and returns the binding string.

**Call relations**: resolve_workspace looks up this binding; oauth_callback writes it during install.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `resolve_workspace`  (lines 930–968)

```
async def resolve_workspace(request: Request, auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Figures out which ufo workspace an incoming Slack route belongs to. It handles OAuth callbacks, Slack URL verification, and signed Slack events.

**Data flow**: It reads the request shape, opens sealed OAuth state for GET callbacks, echoes URL-verification challenges when possible, or uses a signed team id to find and verify a workspace.

**Call relations**: This is the pre-routing helper that runs before ingest, interactive, or oauth_callback can safely use a workspace-bound context.

*Call graph*: calls 9 internal fn (open_credential_authorization, workspace, _auth_signing_secret, _is_install_state, _slack_request_body, slack_installation_id, slack_team_hint, url_verification_challenge, verify_slack_signature); 1 external calls (JSONResponse).


##### `_is_install_state`  (lines 971–974)

```
def _is_install_state(claims: CredentialRequestState) -> bool
```

**Purpose**: Checks whether a sealed credential request state is specifically for Slack OAuth install. This stops unrelated sealed states from being accepted on the Slack callback.

**Data flow**: It inspects the payload marker and requested credential slots and returns true only for the Slack bot-token install request.

**Call relations**: resolve_workspace and oauth_callback use this when trusting OAuth state.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `slack_thread_key`  (lines 977–982)

```
def slack_thread_key(channel: str, root_ts: str, is_dm: bool) -> str
```

**Purpose**: Creates the ufo conversation key for a Slack message. DMs are keyed by channel, while channel messages are keyed by channel plus thread root.

**Data flow**: It receives channel, root timestamp, and DM flag, then returns either the channel id or channel:root_ts.

**Call relations**: _to_inbound uses this so every Slack thread maps to one ufo conversation queue.

*Call graph*: called by 1 (_to_inbound).


##### `slack_message_addressed`  (lines 985–1003)

```
def slack_message_addressed(event: Mapping[str, object], bot_user_id: str, is_dm: bool) -> bool
```

**Purpose**: Decides whether a Slack message is directly addressing the agent. In DMs every message counts; in channels it looks for a real mention of the bot.

**Data flow**: It reads all message bodies, checks for an addressing mention, ignores footer-only mentions, and returns true or false.

**Call relations**: _to_inbound uses this to decide whether to admit immediately or treat the message as ambient thread chatter.

*Call graph*: called by 1 (_to_inbound); 2 external calls (addressing_mention, message_bodies).


##### `_link_count`  (lines 1006–1009)

```
def _link_count(text: str) -> int
```

**Purpose**: Counts links in reply text so Slack link previews can be controlled. Too many previews can bury the actual answer.

**Data flow**: It counts Markdown links, removes them, counts remaining bare URLs, and returns the total.

**Call relations**: slack_reply_body uses this to disable unfurling when a message contains more than one link.

*Call graph*: called by 1 (slack_reply_body); 2 external calls (findall, sub).


##### `slack_reply_parts`  (lines 1012–1089)

```
def slack_reply_parts(text: str, limit: int=SLACK_MARKDOWN_TEXT_LIMIT) -> list[str]
```

**Purpose**: Splits long Slack replies into safe-sized pieces. It tries to cut at natural Markdown boundaries and avoid splitting code blocks or tables when possible.

**Data flow**: It receives text and a limit, identifies protected spans, chooses cut points by paragraph, line, sentence, or space, and returns ordered parts.

**Call relations**: post uses it for outgoing replies; slack_reply_body uses it when building blocks.

*Call graph*: called by 2 (post, slack_reply_body); 2 external calls (finditer, match).


##### `slack_reply_body`  (lines 1092–1159)

```
def slack_reply_body(channel: str, thread_ts: str | None, text: str, metadata: str | None, delivery_id: str | None=None, blocks: bool=True, actions: list[dict[str, object]] | None=None, sections: bool
```

**Purpose**: Builds the JSON body for a Slack chat.postMessage call. It can include Markdown blocks, question buttons, connection buttons, metadata, and footer text.

**Data flow**: It validates text sizes, sets channel and thread fields, optionally adds delivery metadata, builds blocks if possible, falls back to plain text if too large, and returns encoded JSON bytes.

**Call relations**: post uses it for final replies, and ThreadProgress._post uses it for interim progress messages.

*Call graph*: calls 2 internal fn (_link_count, slack_reply_parts); called by 2 (_post, post); 2 external calls (dumps, sub).


##### `_mrkdwn_section`  (lines 1162–1163)

```
def _mrkdwn_section(text: str) -> dict[str, object]
```

**Purpose**: Creates one Slack Block Kit section containing Markdown text. It also trims text to Slack’s section limit.

**Data flow**: It takes text, slices it to the allowed length, and returns a section block dictionary.

**Call relations**: slack_ask_blocks uses this to render question text and option descriptions.

*Call graph*: called by 1 (slack_ask_blocks).


##### `slack_ask_blocks`  (lines 1166–1218)

```
def slack_ask_blocks(question: AskUserInput | None) -> list[dict[str, object]] | None
```

**Purpose**: Renders an agent question as Slack blocks. Simple single-choice questions become buttons; richer questions are shown as text for the user to answer in the thread.

**Data flow**: It receives an AskUserInput, builds title and question sections, adds button rows when safe, and returns block objects or null.

**Call relations**: post attaches these blocks to the final reply when a turn ends by asking the user something.

*Call graph*: calls 1 internal fn (_mrkdwn_section); called by 1 (post).


##### `slack_connect_blocks`  (lines 1221–1242)

```
def slack_connect_blocks(request: ConnectRequest | None, turn_id: UUID) -> list[dict[str, object]] | None
```

**Purpose**: Renders a private connection request as a Slack button. The button later gives the clicking user a private authorization link.

**Data flow**: It receives a ConnectRequest and turn id, and returns an action block containing a button with the turn id as its value, or null.

**Call relations**: post includes these blocks when a turn ends needing an external account connection.

*Call graph*: called by 1 (post).


##### `_string_field`  (lines 1245–1249)

```
def _string_field(event: Mapping[str, object], field: str) -> str
```

**Purpose**: Safely reads a required string field from a Slack payload. It turns missing or empty fields into clear errors.

**Data flow**: It looks up a named field, checks that it is a non-empty string, and returns it or raises ValueError.

**Call relations**: _to_inbound and _to_click use it when reducing Slack data to internal objects.

*Call graph*: called by 2 (_to_click, _to_inbound).


##### `_inbound_files`  (lines 1252–1264)

```
def _inbound_files(event: Mapping[str, object]) -> tuple[InboundFile, ...]
```

**Purpose**: Extracts downloadable file references from a Slack message event. It skips hidden or tombstoned files and caps how many files one message can bring.

**Data flow**: It reads the event’s files list, keeps valid names and private download URLs, creates InboundFile objects, and returns them as a tuple.

**Call relations**: _to_inbound uses it directly, and _declared_files uses it after fetching a message from Slack.

*Call graph*: called by 2 (_declared_files, _to_inbound); 1 external calls (__init__).


##### `_declared_files`  (lines 1267–1298)

```
async def _declared_files(bot_token: str, channel: str, ts: str, root_ts: str | None) -> tuple[InboundFile, ...]
```

**Purpose**: Fetches file attachments for Slack app_mention events when Slack did not include them in the event body. This recovers files that would otherwise be missed.

**Data flow**: It calls conversations.replies for the exact message timestamp, finds that message in the returned page, extracts its files, and returns them.

**Call relations**: _to_inbound calls this for app_mention events that need an extra attachment lookup.

*Call graph*: calls 2 internal fn (_inbound_files, _slack_ok); called by 1 (_to_inbound); 1 external calls (AsyncClient).


##### `oauth_callback`  (lines 1301–1346)

```
async def oauth_callback(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Completes the standard Slack OAuth install. It validates the sealed install state, exchanges the code for a bot token, binds the Slack team, stores credentials, and records identity.

**Data flow**: It reads query parameters, handles Slack cancellation, verifies state, exchanges the code, binds the team id, stores the bot token and identity blob, mirrors the bot id, and returns an HTML result page.

**Call relations**: Slack redirects browsers here after users click the authorize URL.

*Call graph*: calls 11 internal fn (bind_installation, fulfill_credential_request, open_credential_authorization, _install_page, _is_install_state, _mirror_self_user_id, bot_token_fingerprint, identity_blob_key, slack_installation_id, slack_oauth_exchange (+1 more)); 1 external calls (__init__).


##### `_install_page`  (lines 1349–1356)

```
def _install_page(message: str, status: int) -> Response
```

**Purpose**: Builds a small HTML page explaining whether Slack install succeeded or failed. It escapes the message before inserting it into HTML.

**Data flow**: It receives display text and an HTTP status, escapes the text, wraps it in a simple page, and returns a Response.

**Call relations**: oauth_callback uses this for every user-visible outcome.

*Call graph*: called by 1 (oauth_callback); 2 external calls (escape, Response).


##### `_mark_url_verified`  (lines 1362–1376)

```
async def _mark_url_verified(ctx: SurfaceContext, signing_secret: str) -> None
```

**Purpose**: Records that Slack successfully reached this deploy with the current signing secret. Setup can use this as proof that events are configured correctly.

**Data flow**: It fingerprints the secret, skips duplicate writes in this process, stores a timestamped marker in blob storage, and logs write failures.

**Call relations**: ingest and interactive call this after a request has passed signature verification.

*Call graph*: calls 2 internal fn (signing_secret_fingerprint, url_verified_blob_key); called by 2 (ingest, interactive); 2 external calls (dumps, time).


##### `ingest`  (lines 1379–1424)

```
async def ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Receives Slack Events API requests. It verifies them, turns real member messages into ufo turns, or starts a background decision for unaddressed thread replies.

**Data flow**: It reads the raw body, finds the signing secret, verifies the signature, handles URL verification, loads identity, converts the event to Inbound, and either admits it or schedules ambient decision work.

**Call relations**: This is the main Slack message entry route; it hands admitted messages to _admit_inbound.

*Call graph*: calls 11 internal fn (credential, _admit_inbound, _ctx_signing_secret, _decide_ambient_in_background, _identity, _mark_url_verified, _prove_identity_in_background, _slack_request_body, _to_inbound, url_verification_challenge (+1 more)); 3 external calls (loads, JSONResponse, Response).


##### `_admit_inbound`  (lines 1427–1465)

```
async def _admit_inbound(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Turns one accepted Slack message into a ufo conversation turn. It gathers sender context, ambient Slack context, files, and thread mirror data before admission.

**Data flow**: It resolves names, permalink, member, audience, conversation id, downloads files, formats the prompt body, admits it with an idempotency key, and arms followers if a run opened.

**Call relations**: ingest calls it for addressed messages and files; _run_ambient_decision calls it when the model decides an ambient reply should be answered.

*Call graph*: calls 11 internal fn (admit, conversation_for, _ambient_context, _arm_followers, _download_files, _mirror_thread, _resolve_member, _slack_permalink, _slack_user, _turn_context (+1 more)); called by 2 (_run_ambient_decision, ingest); 9 external calls (__init__, __init__, __init__, gather, conversation_audience, fence_member_message, mint_marker, render_markup, unescape).


##### `_decide_ambient_in_background`  (lines 1471–1496)

```
def _decide_ambient_in_background(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Starts a background task to decide whether an unaddressed thread reply deserves an agent response. This keeps Slack’s required quick acknowledgement safe.

**Data flow**: It uses the inbound message id as a task key, skips duplicates, creates the decision task, and tracks it until completion.

**Call relations**: ingest calls this after acknowledging an unaddressed reply in an active Slack thread.

*Call graph*: calls 1 internal fn (_run_ambient_decision); called by 1 (ingest); 1 external calls (create_task).


##### `_decide_ambient_in_background._untrack`  (lines 1492–1494)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Removes a finished ambient-decision task from the task map. This keeps the deduplication map from retaining completed tasks.

**Data flow**: It receives a completed task and deletes it only if it is still the task recorded for that message id.

**Call relations**: It is attached to tasks created by _decide_ambient_in_background.


##### `_run_ambient_decision`  (lines 1499–1514)

```
async def _run_ambient_decision(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Runs the model-backed decision for an unaddressed Slack thread reply and admits the message if wanted. It logs failures because Slack has already been acknowledged.

**Data flow**: It asks _ambient_reply_wanted; if true, it calls _admit_inbound; if anything fails, it logs the dropped message details.

**Call relations**: _decide_ambient_in_background schedules this after ingest returns success to Slack.

*Call graph*: calls 2 internal fn (_admit_inbound, _ambient_reply_wanted); called by 1 (_decide_ambient_in_background); 1 external calls (log).


##### `_author_is_foreign`  (lines 1517–1524)

```
def _author_is_foreign(event: Mapping[str, object], team_id: str) -> bool
```

**Purpose**: Detects messages from outside the bound Slack workspace in shared Slack Connect channels. Those users are skipped because the app cannot reliably serve or identify them.

**Data flow**: It compares the event’s author team fields with the installed team id and returns true when they differ.

**Call relations**: _to_inbound uses this before admitting any member message.

*Call graph*: called by 1 (_to_inbound).


##### `_channel_origin`  (lines 1540–1576)

```
async def _channel_origin(ctx: SurfaceContext, payload: Mapping[str, object], event: Mapping[str, object], channel: str, audience_known: bool) -> ChannelOrigin
```

**Purpose**: Determines the audience and label for the Slack place where a message arrived. Public channels, private rooms, DMs, and externally shared channels have different disclosure rules.

**Data flow**: It reads event hints and, when needed, Slack channel info, then returns a ChannelOrigin containing an Audience and optional human label.

**Call relations**: _to_inbound calls this while building the internal Inbound record.

*Call graph*: calls 2 internal fn (credential, _channel_info); called by 1 (_to_inbound); 4 external calls (__init__, conversation_audience, foreign_room_audience, room_audience).


##### `_to_inbound`  (lines 1579–1628)

```
async def _to_inbound(ctx: SurfaceContext, payload: Mapping[str, object], identity: SlackIdentity) -> Inbound | None
```

**Purpose**: Reduces a raw Slack event payload into the internal Inbound message shape, or rejects it. This is where bot messages, unsupported subtypes, foreign authors, and irrelevant channel chatter are filtered out.

**Data flow**: It validates the event, checks addressing, builds the queue key, finds participating conversations, resolves channel origin and files, and returns an Inbound object or null.

**Call relations**: ingest calls this after verifying the request and loading Slack identity.

*Call graph*: calls 9 internal fn (credential, _author_is_foreign, _channel_origin, _declared_files, _inbound_files, _participating_conversation, _string_field, slack_message_addressed, slack_thread_key); called by 1 (ingest); 2 external calls (__init__, gather).


##### `_participating_conversation`  (lines 1631–1642)

```
async def _participating_conversation(ctx: SurfaceContext, queue_key: str) -> UUID | None
```

**Purpose**: Checks whether a Slack thread is already a real ufo conversation with at least one admitted turn. This prevents stray replies from starting conversations accidentally.

**Data flow**: It finds a conversation by queue key, checks whether it has a latest turn, and returns the conversation id only if both exist.

**Call relations**: _to_inbound uses this to decide if an unaddressed thread reply may be considered.

*Call graph*: calls 2 internal fn (find_conversation, latest_turn); called by 1 (_to_inbound).


##### `_slack_user`  (lines 1645–1675)

```
async def _slack_user(bot_token: str, slack_user_id: str) -> SlackUser | None
```

**Purpose**: Looks up a Slack user’s name, confirmed email, and timezone. It is best-effort so a slow Slack lookup does not block message handling too long.

**Data flow**: It calls users.info, validates the response, accepts email only when Slack says it is confirmed, and returns a SlackUser or null.

**Call relations**: _admit_inbound, interactive, SlackNames._name, and SlackConversationSearch._people use it for sender and display-name resolution.

*Call graph*: calls 1 internal fn (_slack_ok); called by 4 (_people, _name, _admit_inbound, interactive); 2 external calls (__init__, AsyncClient).


##### `SlackNames.of`  (lines 1695–1710)

```
async def of(self, texts: Sequence[str], users: Sequence[str]=()) -> dict[str, str]
```

**Purpose**: Resolves Slack user and channel ids mentioned in text into readable names. It uses a short-lived cache so common mentions do not cost repeated API calls.

**Data flow**: It scans texts and explicit users for ids, reads cached names, fetches missing names up to a limit, stores new names, and returns an id-to-name map.

**Call relations**: _admit_inbound and digest helpers use this before rendering Slack markup into text the agent can read.

*Call graph*: calls 3 internal fn (_name, _remember, _remembered); 3 external calls (gather, mentioned_channels, mentioned_users).


##### `SlackNames._remembered`  (lines 1712–1731)

```
async def _remembered(self, ids: Sequence[str]) -> dict[str, str]
```

**Purpose**: Reads cached Slack names from the extension store. It only returns names that are still fresh.

**Data flow**: It asks the scoped store for name rows, checks each row’s timestamp against the cache lifetime, and returns valid cached names.

**Call relations**: SlackNames.of calls this before making Slack API requests.

*Call graph*: called by 1 (of); 2 external calls (__init__, now).


##### `SlackNames._name`  (lines 1733–1747)

```
async def _name(self, id_: str, url: str) -> str | None
```

**Purpose**: Fetches and cleans the readable name for one Slack user or channel id. It keeps the result short and removes characters that would interfere with mention-like text.

**Data flow**: It calls either _slack_user or _channel_info, extracts a name, strips forbidden characters and extra whitespace, truncates it, and returns it or null.

**Call relations**: SlackNames.of calls this for missing cached ids.

*Call graph*: calls 2 internal fn (_channel_info, _slack_user); called by 1 (of).


##### `SlackNames._remember`  (lines 1749–1756)

```
async def _remember(self, names: Mapping[str, str]) -> None
```

**Purpose**: Writes newly resolved Slack names into the extension cache. Failures are logged but do not stop message admission.

**Data flow**: It timestamps each name and writes it under a name-cache key in the scoped store.

**Call relations**: SlackNames.of calls this after successful Slack lookups.

*Call graph*: called by 1 (of); 2 external calls (__init__, now).


##### `_slack_permalink`  (lines 1759–1779)

```
async def _slack_permalink(bot_token: str, channel: str, ts: str) -> str | None
```

**Purpose**: Fetches Slack’s own permanent link for a message. This gives the ufo turn a source link back to the exact Slack message.

**Data flow**: It calls chat.getPermalink with channel and timestamp, returns the permalink string, or logs and returns null on failure.

**Call relations**: _admit_inbound uses it for member messages; interactive uses it for button-answer source context.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_admit_inbound, interactive); 1 external calls (AsyncClient).


##### `_turn_context`  (lines 1782–1797)

```
def _turn_context(sender: SlackUser | None, source: str | None) -> TurnContext
```

**Purpose**: Builds the context object attached to an admitted turn. It includes sender name/email, timezone when valid, and the Slack source link.

**Data flow**: It receives a SlackUser and source URL, formats a sender line, tries to create TurnContext with timezone, and drops invalid timezones if needed.

**Call relations**: _admit_inbound passes its result into core admission.

*Call graph*: called by 1 (_admit_inbound); 1 external calls (__init__).


##### `_resolve_member`  (lines 1800–1818)

```
async def _resolve_member(ctx: SurfaceContext, slack_user_id: str, is_dm: bool, sender: SlackUser | None) -> UUID | None
```

**Purpose**: Maps a Slack user to a ufo member when possible. It links existing identities or joins same-domain teammates by confirmed Slack email.

**Data flow**: It first checks linked_member, then uses SlackUser email if available to join_member, and may raise for DMs when user lookup is unavailable.

**Call relations**: _admit_inbound and interactive use this to set the speaker member for turns.

*Call graph*: calls 2 internal fn (join_member, linked_member); called by 2 (_admit_inbound, interactive); 1 external calls (__init__).


##### `_ambient_reply_wanted`  (lines 1821–1846)

```
async def _ambient_reply_wanted(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> bool
```

**Purpose**: Asks whether an unaddressed reply in an active Slack thread should get an agent answer. This avoids the bot interrupting human side conversations.

**Data flow**: It fetches recent thread history, defaults to answering if no trusted history is available, sends the current message and history to core’s decision method, and logs no-reply decisions.

**Call relations**: _run_ambient_decision calls this before deciding whether to admit the inbound message.

*Call graph*: calls 2 internal fn (ambient_reply_wanted, _ambient_history); called by 1 (_run_ambient_decision); 2 external calls (__init__, log).


##### `_ambient_history`  (lines 1849–1871)

```
async def _ambient_history(bot_token: str, inbound: Inbound, identity: SlackIdentity) -> tuple[AmbientMessage, ...]
```

**Purpose**: Builds recent Slack thread history for the ambient reply decision. It reads from Slack rather than ufo turns so skipped human chatter is visible.

**Data flow**: It fetches the thread tail, converts usable entries into AmbientMessage objects, sorts them, and returns the newest bounded set.

**Call relations**: _ambient_reply_wanted uses this as the model’s context.

*Call graph*: calls 2 internal fn (_ambient_entry, _thread_tail); called by 1 (_ambient_reply_wanted).


##### `_thread_tail`  (lines 1874–1920)

```
async def _thread_tail(bot_token: str, channel: str, root_ts: str, latest: str) -> tuple[object, ...] | None
```

**Purpose**: Fetches messages in a Slack thread before a given message. It walks pages so callers get the end of a thread rather than only the opening messages.

**Data flow**: It calls conversations.replies with latest bounds and cursor paging, accumulates messages up to a limit, and returns null if the read cannot be trusted.

**Call relations**: _ambient_history and _unseen_tail use this when they need recent Slack thread messages.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_ambient_history, _unseen_tail); 1 external calls (AsyncClient).


##### `_ambient_entry`  (lines 1923–1943)

```
def _ambient_entry(item: object, inbound: Inbound, identity: SlackIdentity) -> tuple[float, AmbientMessage] | None
```

**Purpose**: Converts one fetched Slack message into a decision-history entry. It keeps member messages and the agent’s own messages, while dropping other bots and bad data.

**Data flow**: It validates user, timestamp, and text, marks whether the speaker is the bot, rejects messages at or after the inbound timestamp, and returns a sortable pair or null.

**Call relations**: _ambient_history calls this for each Slack message fetched by _thread_tail.

*Call graph*: called by 1 (_ambient_history); 1 external calls (__init__).


##### `_ambient_context`  (lines 1946–2001)

```
async def _ambient_context(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Builds background context to include inside an admitted turn. It fills the gap between what Slack users can see and what the ufo transcript already contains.

**Data flow**: It decides which Slack history to fetch, calls Slack when needed, resolves names, renders a bounded digest, or returns an empty string on failure.

**Call relations**: _admit_inbound gathers this alongside sender and permalink data before admitting a turn.

*Call graph*: calls 4 internal fn (_digest_names, _slack_ok, _unseen_tail, ambient_digest); called by 1 (_admit_inbound); 1 external calls (AsyncClient).


##### `_digest_names`  (lines 2004–2013)

```
async def _digest_names(bot_token: str, messages: Sequence[object]) -> dict[str, str]
```

**Purpose**: Resolves author and mention names for a set of Slack messages. This makes ambient context readable instead of showing raw Slack ids.

**Data flow**: It extracts message texts and user ids, then asks SlackNames to resolve them into a name map.

**Call relations**: _ambient_context and _unseen_tail call this before rendering ambient_digest.

*Call graph*: called by 2 (_ambient_context, _unseen_tail); 1 external calls (__init__).


##### `_unseen_tail`  (lines 2016–2062)

```
async def _unseen_tail(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Builds a digest of recent thread messages that did not found turns of their own. This keeps the agent aware of human chatter it previously chose not to answer.

**Data flow**: It fetches recent thread messages, walks backward until it finds a message already admitted, collects newer unseen messages, resolves names, and renders a digest.

**Call relations**: _ambient_context calls this when the Slack thread already has a ufo conversation.

*Call graph*: calls 4 internal fn (admitted_body, _digest_names, _thread_tail, ambient_digest); called by 1 (_ambient_context).


##### `ambient_digest`  (lines 2065–2133)

```
def ambient_digest(messages: list[object], bot_user_id: str, note: str, marker: str, names: Mapping[str, str]) -> str
```

**Purpose**: Renders Slack messages as safe, bounded background text for the model. It keeps bystander messages from escaping their context and impersonating other prompt elements.

**Data flow**: It filters member messages, drops bot posts and direct mentions, formats time, speaker, and rendered text, trims overlong digests, and wraps the result in a marked context element.

**Call relations**: _ambient_context and _unseen_tail use this after fetching Slack messages and resolving names.

*Call graph*: called by 2 (_ambient_context, _unseen_tail); 3 external calls (fromtimestamp, addressing_mention, render_markup).


##### `_slack_download_host_ok`  (lines 2136–2138)

```
def _slack_download_host_ok(url: str) -> bool
```

**Purpose**: Checks whether a file download URL belongs to Slack. This prevents the bot token from being sent to an attacker-controlled host.

**Data flow**: It parses the URL hostname and returns true only for slack.com or a Slack subdomain.

**Call relations**: _stream_download calls this before making an authenticated file request.

*Call graph*: called by 1 (_stream_download); 1 external calls (urlparse).


##### `_stream_download`  (lines 2141–2159)

```
async def _stream_download(bot_token: str, url: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a private Slack file download into the workspace without buffering the whole file. It enforces host and size safety.

**Data flow**: It validates the host, opens an authenticated streaming GET request, yields chunks, and raises if the file grows beyond the inbound limit.

**Call relations**: _download_files passes this stream to the workspace file writer.

*Call graph*: calls 1 internal fn (_slack_download_host_ok); called by 1 (_download_files); 2 external calls (__init__, AsyncClient).


##### `_download_files`  (lines 2171–2187)

```
async def _download_files(ctx: SurfaceContext, conversation_id: UUID, bot_token: str, files: tuple[InboundFile, ...]) -> DownloadedFiles
```

**Purpose**: Downloads Slack files attached to an inbound message into the ufo workspace. Oversized files are skipped and reported instead of partially saved.

**Data flow**: It gives each file a safe inbox name, streams it to workspace storage, records delivered and skipped names, and returns a DownloadedFiles summary.

**Call relations**: _admit_inbound calls this before admitting the turn so the prompt can mention saved attachments.

*Call graph*: calls 2 internal fn (write_workspace_file, _stream_download); called by 1 (_admit_inbound); 2 external calls (__init__, inbox_name).


##### `files_note`  (lines 2190–2199)

```
def files_note(downloaded: DownloadedFiles) -> str
```

**Purpose**: Turns downloaded-file results into plain text for the agent. The note tells the model which files were saved and which were too large.

**Data flow**: It receives delivered and skipped file names, builds human-readable clauses, and joins them with newlines.

**Call relations**: _admit_inbound appends this note to the fenced member message.

*Call graph*: called by 1 (_admit_inbound).


##### `MirroredThread.read`  (lines 2211–2216)

```
def read(cls, row: JsonValue) -> 'MirroredThread'
```

**Purpose**: Reads a stored Slack thread mirror into a MirroredThread model. It supports both old simple string rows and newer structured rows.

**Data flow**: It receives stored JSON-like data, treats a plain string as a queue key, otherwise validates it as a MirroredThread.

**Call relations**: follow_turn uses this after reading the thread mirror from the extension store.


##### `_thread_mirror_key`  (lines 2219–2220)

```
def _thread_mirror_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the store key that maps a ufo conversation to its Slack thread. Followers need this later when only the turn and conversation are known.

**Data flow**: It takes a conversation id and returns a scoped-store key under the Slack thread prefix.

**Call relations**: _mirror_thread writes this key; follow_turn reads it.

*Call graph*: called by 2 (_mirror_thread, follow_turn).


##### `_mirror_thread`  (lines 2223–2231)

```
async def _mirror_thread(conversation_id: UUID, thread: MirroredThread) -> None
```

**Purpose**: Records which Slack thread belongs to a ufo conversation. This lets later hook-time follower tasks post status and progress in the right Slack place.

**Data flow**: It serializes the MirroredThread and writes it into the Slack scoped store under the conversation’s mirror key.

**Call relations**: _admit_inbound and interactive write the mirror before admitting turns or answers.

*Call graph*: calls 1 internal fn (_thread_mirror_key); called by 2 (_admit_inbound, interactive); 2 external calls (__init__, model_dump).


##### `FollowerContext.workspace_id`  (lines 2242–2242)

```
def workspace_id(self) -> UUID
```

**Purpose**: Declares that follower code needs to know the workspace id. This id is used to key status ownership and footer links.

**Data flow**: A concrete context returns the current workspace UUID.

**Call relations**: ThreadStatus, ThreadProgress, and footer helpers rely on implementations of this protocol property.


##### `FollowerContext.public_base_url`  (lines 2245–2245)

```
def public_base_url(self) -> str | None
```

**Purpose**: Declares that follower code may need the deploy’s public URL. This lets progress and reply footers link back to the web app when possible.

**Data flow**: A concrete context returns the base URL string or null.

**Call relations**: _slack_footer reads this through either SurfaceContext or _HookFollowerContext.


##### `FollowerContext.credential`  (lines 2247–2247)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Declares how follower code reads credentials such as the Slack bot token. Followers use credentials to call Slack.

**Data flow**: A concrete context receives a slot name and returns the secret value.

**Call relations**: ThreadStatus, ThreadProgress, and posting helpers call this through the protocol.


##### `FollowerContext.tail`  (lines 2249–2251)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Declares how follower code reads live turn frames. A tail is a stream of events such as tool calls, text deltas, and terminal states.

**Data flow**: A concrete context receives a turn id and optional cursor and returns an async stream context manager.

**Call relations**: ThreadStatus and ThreadProgress use it to show live Slack feedback while a turn runs.


##### `FollowerContext.turn_is_terminal`  (lines 2253–2253)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Declares how progress reporting checks whether a turn has already ended. This avoids posting progress after the final answer is available.

**Data flow**: A concrete context receives a turn id and returns true or false.

**Call relations**: ThreadProgress._follow calls this at checkpoints when no live frame arrived.


##### `FollowerContext.conversation_agent`  (lines 2255–2255)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Declares how follower code finds the agent attached to a conversation. This is needed for footer links.

**Data flow**: A concrete context receives a conversation id and returns an agent id or null.

**Call relations**: ThreadProgress._footer uses it before calling _slack_footer.


##### `FollowerContext.is_operator_workspace`  (lines 2257–2257)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Declares how follower code checks whether the workspace is the operator workspace. Operator workspaces may show extra accounting and debug links.

**Data flow**: A concrete context returns a boolean.

**Call relations**: _slack_footer calls this to decide how much footer detail to expose.

*Call graph*: called by 1 (_slack_footer).


##### `ThreadStatus.thread`  (lines 2311–2312)

```
def thread(self) -> tuple[UUID, str, str]
```

**Purpose**: Returns the unique key for the Slack thread whose live status is being written. It includes workspace, channel, and thread timestamp.

**Data flow**: It combines fields from the status object into a tuple.

**Call relations**: _track_status, _run_status, and _restamp_thread_status use this to coordinate one visible status writer per thread.


##### `ThreadStatus.run`  (lines 2314–2333)

```
async def run(self) -> None
```

**Purpose**: Runs the Slack native thread-status follower for one turn. It starts with “Thinking…”, follows live frames, and clears the status when appropriate.

**Data flow**: It reads the bot token, creates a Slack client, writes the initial status, follows frames, handles cancellation and errors, and clears the line at the end.

**Call relations**: _run_status calls this inside a task created by _track_status.

*Call graph*: calls 3 internal fn (_clear, _follow, _set); called by 1 (_run_status); 2 external calls (AsyncClient, log).


##### `ThreadStatus._set`  (lines 2335–2375)

```
async def _set(self, client: httpx.AsyncClient, bot_token: str, status: str) -> bool
```

**Purpose**: Writes one status line to Slack’s assistant thread status API. It only writes if this turn is still the current writer for the thread.

**Data flow**: It builds the Slack API body, adds loading_messages for visible custom text, posts to Slack, logs success or failure, and returns whether Slack accepted it.

**Call relations**: ThreadStatus.run, _follow, and _clear use this for every status update.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_clear, _follow, run); 3 external calls (post, dumps, log).


##### `ThreadStatus._clear`  (lines 2377–2386)

```
async def _clear(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Clears the Slack thread status when this turn ends, but only if no sibling turn in the same thread is still running. This prevents one turn from erasing another turn’s status.

**Data flow**: It checks live statuses for the same thread; if none remain except this turn, it writes an empty status.

**Call relations**: ThreadStatus.run calls this after following ends or after certain errors.

*Call graph*: calls 1 internal fn (_set); called by 1 (run).


##### `ThreadStatus._follow`  (lines 2388–2441)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str, shown: str) -> None
```

**Purpose**: Translates live turn frames into short Slack status text. It also refreshes the current line so Slack does not expire it during quiet stretches.

**Data flow**: It tails frames, waits for new frames or restamp events, maps tool calls, skill loads, absorbed messages, and text deltas to status strings, rate-limits writes, and exits on terminal frames.

**Call relations**: ThreadStatus.run calls this after the initial status write.

*Call graph*: calls 1 internal fn (_set); called by 1 (run); 4 external calls (ensure_future, gather, wait, monotonic).


##### `_restamp_thread_status`  (lines 2451–2458)

```
def _restamp_thread_status(workspace_id: UUID, channel: str, thread_ts: str) -> None
```

**Purpose**: Wakes status followers for a thread after an in-thread progress post blanks Slack’s native status. This restores the live status quickly.

**Data flow**: It scans live ThreadStatus objects for the workspace/channel/thread and sets their blanked event.

**Call relations**: ThreadProgress._post calls this after successfully posting progress into a channel thread.

*Call graph*: called by 1 (_post).


##### `_track_status`  (lines 2461–2482)

```
def _track_status(ctx: FollowerContext, turn_id: UUID, thread: MirroredThread) -> None
```

**Purpose**: Starts a per-turn Slack status task if one is not already running in this process. It also makes the newest turn the active writer for that Slack thread.

**Data flow**: It derives the Slack status anchor, creates a ThreadStatus, records writer and task maps, and schedules _run_status.

**Call relations**: _arm_followers calls this for admitted turns and hook-armed turns.

*Call graph*: calls 1 internal fn (_run_status); called by 1 (_arm_followers); 3 external calls (__init__, create_task, log).


##### `_run_status`  (lines 2485–2513)

```
async def _run_status(status: ThreadStatus) -> None
```

**Purpose**: Wraps a ThreadStatus task so failures are logged and thread-writer ownership is cleaned up. It can hand writer ownership back to an older live turn.

**Data flow**: It awaits status.run, logs errors, removes task/status records, and updates or deletes the thread writer claim.

**Call relations**: _track_status schedules this background task.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_status); 1 external calls (log).


##### `ProgressCadence.__post_init__`  (lines 2525–2529)

```
def __post_init__(self) -> None
```

**Purpose**: Validates the long-running progress schedule. It ensures waits are positive and the maximum interval is not smaller than the first interval.

**Data flow**: It checks base_seconds and cap_seconds and raises ValueError for invalid values.

**Call relations**: _track_progress creates ProgressCadence before starting a ThreadProgress reporter.


##### `ProgressCadence.intervals`  (lines 2531–2542)

```
def intervals(self) -> Iterator[float]
```

**Purpose**: Generates the wait intervals between progress posts. The wait doubles with elapsed time until it reaches a cap.

**Data flow**: It yields the base wait first, then repeatedly adds elapsed time and yields the next capped wait forever.

**Call relations**: checkpoints_after uses this to turn intervals into absolute elapsed checkpoints.

*Call graph*: called by 1 (checkpoints_after).


##### `ProgressCadence.checkpoints_after`  (lines 2544–2553)

```
def checkpoints_after(self, elapsed_seconds: float) -> Iterator[float]
```

**Purpose**: Finds future progress-post checkpoints for a turn that may already be running. This lets resumed reporters pick up the real elapsed schedule.

**Data flow**: It walks intervals, accumulates elapsed checkpoint times, skips those already passed, and yields upcoming checkpoint times.

**Call relations**: ThreadProgress._follow uses this when deciding when to post progress.

*Call graph*: calls 1 internal fn (intervals).


##### `TurnActivity.tool`  (lines 2568–2573)

```
def tool(self, tool: str, description: str) -> None
```

**Purpose**: Records that the turn is currently doing a tool call. It prefers the model’s user-facing description, falling back to a humanized tool name.

**Data flow**: It clears streaming text, normalizes description or tool slug, truncates it, and stores it as the current activity.

**Call relations**: ThreadProgress._follow calls this when it sees ToolCall frames.


##### `TurnActivity.skill`  (lines 2575–2577)

```
def skill(self, skill: str) -> None
```

**Purpose**: Records that the turn is loading a skill. This gives long-running progress posts something readable to say.

**Data flow**: It clears streaming text and stores a short “loading skill” activity string.

**Call relations**: ThreadProgress._follow calls this when it sees SkillLoad frames.


##### `TurnActivity.stream`  (lines 2579–2580)

```
def stream(self, text: str) -> None
```

**Purpose**: Records that answer text is streaming. Progress messages do not reveal partial answer text; they just say the response is being prepared.

**Data flow**: It appends the streamed text fragment to the streaming list.

**Call relations**: ThreadProgress._follow calls this when it sees TextDelta frames.


##### `TurnActivity.current_step`  (lines 2582–2586)

```
def current_step(self) -> str
```

**Purpose**: Returns the best current progress label. Streaming answer text takes priority over the previous tool activity.

**Data flow**: It checks whether any text fragments are streaming and returns a response-preparation label or the stored activity.

**Call relations**: TurnActivity.report uses this to decide what a checkpoint should say.

*Call graph*: called by 1 (report).


##### `TurnActivity.report`  (lines 2588–2598)

```
def report(self, elapsed_seconds: float) -> str | None
```

**Purpose**: Builds one human-readable progress message for a checkpoint. If the turn has produced no visible activity, it skips the post.

**Data flow**: It gets the current step, formats elapsed minutes or hours, and returns a short line or null.

**Call relations**: ThreadProgress._post calls this before posting to Slack.

*Call graph*: calls 1 internal fn (current_step); called by 1 (_post).


##### `ThreadProgress.run`  (lines 2634–2637)

```
async def run(self) -> None
```

**Purpose**: Runs the long-running progress reporter for one turn. It opens a Slack client and delegates the live-follow loop.

**Data flow**: It reads the bot token, creates an HTTP client, and calls _follow.

**Call relations**: _run_progress calls this inside a background task.

*Call graph*: calls 1 internal fn (_follow); called by 1 (_run_progress); 1 external calls (AsyncClient).


##### `ThreadProgress._elapsed`  (lines 2639–2642)

```
def _elapsed(self) -> float
```

**Purpose**: Calculates how long the member has been waiting for this turn. It uses the durable turn start time, not when this process began reporting.

**Data flow**: It subtracts started_at from the current UTC time and returns seconds.

**Call relations**: ThreadProgress._follow uses this to schedule checkpoints and label progress messages.

*Call graph*: called by 1 (_follow); 1 external calls (now).


##### `ThreadProgress._follow`  (lines 2644–2685)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Tails live turn frames and posts progress at scheduled checkpoints. It stops when the turn ends or is parked.

**Data flow**: It sets the next checkpoint, updates TurnActivity and latest cost from frames, posts at timeouts if the turn is still running, and cleans up the pending frame task.

**Call relations**: ThreadProgress.run calls this as the reporter’s main loop.

*Call graph*: calls 2 internal fn (_elapsed, _post); called by 1 (run); 4 external calls (__init__, ensure_future, gather, wait).


##### `ThreadProgress._post`  (lines 2687–2746)

```
async def _post(self, client: httpx.AsyncClient, bot_token: str, activity: TurnActivity, elapsed_seconds: float, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Posts one interim progress message to Slack if there is real activity to report. The first successful post may include the standard footer.

**Data flow**: It asks TurnActivity for text, builds optional footer metadata, posts a Slack message, logs success or failure, restamps thread status when needed, and returns whether it landed.

**Call relations**: ThreadProgress._follow calls this at each checkpoint.

*Call graph*: calls 5 internal fn (_footer, report, _restamp_thread_status, _slack_ok, slack_reply_body); called by 1 (_follow); 2 external calls (post, log).


##### `ThreadProgress._footer`  (lines 2748–2769)

```
async def _footer(self, bot_token: str, channel: str, spend: CostTick | None) -> str | None
```

**Purpose**: Builds the footer for a progress post. Since the turn is still running, it includes only information already known, such as current cost if available.

**Data flow**: It finds the conversation’s agent, formats cost and tokens when present, and calls _slack_footer.

**Call relations**: ThreadProgress._post calls this for the first progress message.

*Call graph*: calls 1 internal fn (_slack_footer); called by 1 (_post).


##### `_track_progress`  (lines 2775–2801)

```
def _track_progress(ctx: FollowerContext, turn_id: UUID, conversation_id: UUID, queue_key: str, started_at: datetime) -> None
```

**Purpose**: Starts a per-turn long-run progress reporter in this process. It avoids duplicate reporters for the same turn id locally.

**Data flow**: It creates a ThreadProgress with the standard cadence and durable start time, schedules _run_progress, and stores the task.

**Call relations**: _arm_followers calls this only when the caller knows the turn’s durable started_at time.

*Call graph*: calls 1 internal fn (_run_progress); called by 1 (_arm_followers); 3 external calls (__init__, __init__, create_task).


##### `_run_progress`  (lines 2804–2819)

```
async def _run_progress(progress: ThreadProgress) -> None
```

**Purpose**: Wraps a ThreadProgress task so unexpected reporter failures are logged and task tracking is cleaned up.

**Data flow**: It awaits progress.run, logs abandonment on exception, and removes the turn from the progress task map.

**Call relations**: _track_progress schedules this background task.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_progress); 1 external calls (log).


##### `_arm_followers`  (lines 2833–2847)

```
def _arm_followers(ctx: FollowerContext, turn: FollowedTurn, thread: MirroredThread) -> None
```

**Purpose**: Starts all Slack live-feedback followers appropriate for a turn. Status can start at admission, while progress requires the turn’s durable start time.

**Data flow**: It always asks _track_status to start status feedback, and asks _track_progress only when started_at is present.

**Call relations**: _admit_inbound, interactive, and follow_turn all converge here so follower behavior stays consistent.

*Call graph*: calls 2 internal fn (_track_progress, _track_status); called by 3 (_admit_inbound, follow_turn, interactive).


##### `_HookFollowerContext.workspace_id`  (lines 2859–2860)

```
def workspace_id(self) -> UUID
```

**Purpose**: Exposes the hook context’s workspace id through the follower protocol. This lets hook-started followers behave like request-started followers.

**Data flow**: It returns ext.workspace_id from the wrapped ExtensionContext.

**Call relations**: follow_turn wraps hook context with _HookFollowerContext before calling _arm_followers.


##### `_HookFollowerContext.public_base_url`  (lines 2863–2864)

```
def public_base_url(self) -> str | None
```

**Purpose**: Exposes the hook context’s public base URL through the follower protocol. Footer builders can use it for web links.

**Data flow**: It returns ext.public_base_url.

**Call relations**: ThreadProgress and _slack_footer read this through the follower context created in follow_turn.


##### `_HookFollowerContext.credential`  (lines 2866–2867)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Lets hook-started followers read extension credentials such as the Slack bot token. Hook contexts store credentials under the extension interface.

**Data flow**: It receives a slot name and returns ext.credentials.get(slot).

**Call relations**: ThreadStatus and ThreadProgress use this when armed from follow_turn.


##### `_HookFollowerContext.tail`  (lines 2869–2872)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Lets hook-started followers read live turn frames. It adapts the hook extension context to the follower protocol.

**Data flow**: It passes the turn id and cursor to ext.tail and returns the async stream context manager.

**Call relations**: ThreadStatus and ThreadProgress use this stream after follow_turn arms them.


##### `_HookFollowerContext.turn_is_terminal`  (lines 2874–2875)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Lets hook-started progress reporters check whether a turn has already finished.

**Data flow**: It forwards the turn id to ext.turn_is_terminal and returns the boolean result.

**Call relations**: ThreadProgress._follow uses this through the follower protocol.


##### `_HookFollowerContext.conversation_agent`  (lines 2877–2878)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Lets hook-started progress reporters find the agent for a conversation. This supports footer links.

**Data flow**: It forwards the conversation id to ext.conversation_agent.

**Call relations**: ThreadProgress._footer calls this through the follower protocol.


##### `_HookFollowerContext.is_operator_workspace`  (lines 2880–2881)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Lets hook-started followers know whether extra operator-only footer details are allowed.

**Data flow**: It forwards the call to ext.is_operator_workspace.

**Call relations**: _slack_footer calls this through the follower context.


##### `follow_turn`  (lines 2884–2926)

```
async def follow_turn(ctx: HookContext) -> HookOutcome
```

**Purpose**: Hook handler that arms Slack status and progress followers from the turn execution itself. This is how resumed or claimed turns get live feedback even after process changes.

**Data flow**: It checks the hook turn, skips subagent turns, reads the stored Slack thread mirror with a short timeout, wraps the hook context, and calls _arm_followers.

**Call relations**: The manifest hook calls this on user_prompt_submit during turn execution.

*Call graph*: calls 2 internal fn (_arm_followers, _thread_mirror_key); 4 external calls (__init__, __init__, timeout, log).


##### `interactive`  (lines 2960–3046)

```
async def interactive(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Receives Slack Block Kit button clicks. It verifies the request, handles answer buttons and connect buttons, admits answers as turns, and schedules Slack message updates.

**Data flow**: It reads and verifies the raw form body, loads identity, parses the click, resolves the member, either posts a private connect link or admits an answer turn, then optionally rewrites the clicked message.

**Call relations**: This is the Slack interactivity route and uses _to_click, _resolve_member, _arm_followers, _rewrite_in_background, and _ephemeral_in_background.

*Call graph*: calls 21 internal fn (admit, admitted_body, connect_url, conversation_for, credential, find_conversation, linked_member, _arm_followers, _ctx_signing_secret, _ephemeral_in_background (+11 more)); 9 external calls (__init__, __init__, __init__, gather, conversation_audience, JSONResponse, Response, fence_member_message, mint_marker).


##### `_rewrite_in_background`  (lines 3052–3055)

```
def _rewrite_in_background(bot_token: str, click: AnswerClick) -> None
```

**Purpose**: Schedules the Slack message rewrite after a question button is accepted. It keeps the interactive acknowledgement fast.

**Data flow**: It creates a task for _run_rewrite, stores it in a set, and removes it when done.

**Call relations**: interactive calls this only for the winning answer click.

*Call graph*: calls 1 internal fn (_run_rewrite); called by 1 (interactive); 1 external calls (create_task).


##### `_run_rewrite`  (lines 3058–3062)

```
async def _run_rewrite(bot_token: str, click: AnswerClick) -> None
```

**Purpose**: Runs the answer-button rewrite and logs failures. A failed rewrite does not undo the admitted answer.

**Data flow**: It calls _replace_buttons_with_answer and catches any exception to log a warning.

**Call relations**: _rewrite_in_background schedules this task.

*Call graph*: calls 1 internal fn (_replace_buttons_with_answer); called by 1 (_rewrite_in_background).


##### `_ephemeral_in_background`  (lines 3065–3068)

```
def _ephemeral_in_background(ctx: SurfaceContext, click: ConnectClick, text: str) -> None
```

**Purpose**: Schedules a private Slack response for a connect button click. This keeps Slack’s interactivity response within its time limit.

**Data flow**: It creates a task for _post_ephemeral, stores it with other background rewrite tasks, and removes it when done.

**Call relations**: interactive calls this for ConnectClick results.

*Call graph*: calls 1 internal fn (_post_ephemeral); called by 1 (interactive); 1 external calls (create_task).


##### `_post_ephemeral`  (lines 3071–3096)

```
async def _post_ephemeral(ctx: SurfaceContext, click: ConnectClick, text: str) -> None
```

**Purpose**: Posts a Slack ephemeral message visible only to the member who clicked a connect button. It puts the private authorization link in the same thread.

**Data flow**: It reads the bot token, posts chat.postEphemeral with channel, user, text, and optional thread timestamp, and logs failures.

**Call relations**: _ephemeral_in_background schedules this after interactive handles a connect click.

*Call graph*: calls 2 internal fn (credential, _slack_ok); called by 1 (_ephemeral_in_background); 2 external calls (AsyncClient, dumps).


##### `_to_click`  (lines 3099–3154)

```
def _to_click(raw: bytes, identity: SlackIdentity) -> AnswerClick | ConnectClick | None
```

**Purpose**: Parses a verified Slack interactive payload into an AnswerClick or ConnectClick. Unrecognized actions are ignored.

**Data flow**: It decodes the form payload JSON, validates team and action fields, extracts user/channel/message details, parses button values, and returns the appropriate click object or null.

**Call relations**: interactive calls this after signature verification and identity loading.

*Call graph*: calls 2 internal fn (_dict_field, _string_field); called by 1 (interactive); 5 external calls (__init__, __init__, loads, parse_qs, UUID).


##### `_dict_field`  (lines 3157–3161)

```
def _dict_field(payload: Mapping[str, object], field: str) -> Mapping[str, object]
```

**Purpose**: Safely reads a required object field from a Slack payload. It gives clearer errors when Slack data is missing or malformed.

**Data flow**: It looks up the field, verifies it is a dictionary-like object, and returns it or raises ValueError.

**Call relations**: _to_click uses this for nested Slack user, channel, and message fields.

*Call graph*: called by 1 (_to_click).


##### `_replace_buttons_with_answer`  (lines 3164–3206)

```
async def _replace_buttons_with_answer(bot_token: str, click: AnswerClick) -> None
```

**Purpose**: Updates a Slack question message so the clicked button row becomes a chosen-answer line. Other blocks are echoed back unchanged.

**Data flow**: It builds an answered context block, copies delivered blocks or creates a fallback, replaces the clicked block when found, and calls chat.update.

**Call relations**: _run_rewrite calls this after interactive confirms that the click won admission.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (_run_rewrite); 2 external calls (AsyncClient, dumps).


##### `_reply_text`  (lines 3209–3219)

```
def _reply_text(writeback: Writeback) -> str
```

**Purpose**: Chooses the main text for a completed turn’s Slack reply. It gives clear fallback text for failed, cancelled, or empty replies.

**Data flow**: It inspects terminal status and text, returns failure text, cancellation reason or default, final answer text, or an empty-reply placeholder.

**Call relations**: _reply_with_oversize_links uses this as the base reply text before adding special notices.

*Call graph*: called by 1 (_reply_with_oversize_links).


##### `_reply_with_oversize_links`  (lines 3222–3251)

```
def _reply_with_oversize_links(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Adds Slack-specific extra text to the final reply. It points users to credentials when secrets are needed and links artifacts too large for Slack upload.

**Data flow**: It starts from _reply_text, appends a credentials instruction if needed, filters oversized artifacts, adds download-link lines, and returns the combined text.

**Call relations**: post calls this before splitting and sending the Slack reply.

*Call graph*: calls 3 internal fn (home_url, _oversize_link_line, _reply_text); called by 1 (post).


##### `_oversize_link_line`  (lines 3254–3257)

```
def _oversize_link_line(ctx: SurfaceContext, artifact: SharedArtifact) -> str
```

**Purpose**: Formats one too-large artifact as a Markdown list item. It uses a temporary portal link when available.

**Data flow**: It asks the context for an artifact link, wraps the filename in Markdown if there is a URL, adds byte size, and returns the line.

**Call relations**: _reply_with_oversize_links calls this for each artifact above Slack’s upload limit.

*Call graph*: calls 1 internal fn (artifact_link); called by 1 (_reply_with_oversize_links).


##### `_channel_info`  (lines 3260–3274)

```
async def _channel_info(bot_token: str, channel: str) -> Mapping[str, object] | None
```

**Purpose**: Fetches Slack metadata for a channel. The metadata is used to label channels and decide whether sharing is private, public, or external.

**Data flow**: It calls conversations.info, returns the channel object when valid, or logs and returns null on failure.

**Call relations**: _channel_origin, _channel_is_externally_shared, and SlackNames._name use this helper.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_name, _channel_is_externally_shared, _channel_origin); 1 external calls (AsyncClient).


##### `_channel_is_externally_shared`  (lines 3277–3290)

```
async def _channel_is_externally_shared(bot_token: str, channel: str) -> bool
```

**Purpose**: Checks whether a Slack channel crosses workspace boundaries. If the channel cannot be read, it fails closed and treats it as external.

**Data flow**: It fetches channel info and checks Slack’s shared-channel flags, returning true for shared or unreadable channels.

**Call relations**: _slack_footer uses this to avoid exposing operator-only details in shared Slack spaces.

*Call graph*: calls 1 internal fn (_channel_info); called by 1 (_slack_footer).


##### `_slack_footer`  (lines 3293–3328)

```
async def _slack_footer(ctx: FollowerContext, bot_token: str, channel: str, conversation_id: UUID, agent_id: UUID, turn_id: UUID, accounting: str | None) -> str | None
```

**Purpose**: Builds the small footer shown under Slack progress and reply messages. It can include web links, config links, accounting, and debug links depending on workspace and channel safety.

**Data flow**: It builds web links from the public URL, checks operator workspace and external sharing, then joins allowed footer elements within Slack’s text limit.

**Call relations**: post uses it for final replies; ThreadProgress._footer uses it for the first progress post.

*Call graph*: calls 2 internal fn (is_operator_workspace, _channel_is_externally_shared); called by 2 (_footer, post).


##### `_slack_reply_progress_key`  (lines 3342–3343)

```
def _slack_reply_progress_key(turn_id: UUID) -> str
```

**Purpose**: Builds the store key for tracking reply delivery progress for one turn. This helps retries avoid duplicate Slack messages.

**Data flow**: It prefixes the turn id with the Slack reply progress namespace and returns the key.

**Call relations**: post reads and writes this key; attach deletes it after core records delivery.

*Call graph*: called by 2 (attach, post).


##### `_slack_reply_progress`  (lines 3346–3359)

```
async def _slack_reply_progress(store: ScopedStore, key: str) -> tuple[_SlackReplyProgress, JsonValue]
```

**Purpose**: Loads or initializes the delivery checkpoint for a Slack reply. It uses compare-and-set storage so concurrent pollers do not blindly overwrite each other.

**Data flow**: It reads the store key, validates existing progress, or creates an empty progress row if none exists, then returns both model and stored JSON value.

**Call relations**: post calls this before attempting to send reply parts.

*Call graph*: calls 2 internal fn (get, put_if); called by 1 (post); 2 external calls (__init__, __init__).


##### `_checkpoint_slack_reply`  (lines 3362–3371)

```
async def _checkpoint_slack_reply(store: ScopedStore, key: str, expected: JsonValue, progress: _SlackReplyProgress) -> tuple[_SlackReplyProgress, JsonValue]
```

**Purpose**: Writes an updated Slack reply delivery checkpoint if the stored value has not changed. This protects delivery progress from races.

**Data flow**: It serializes the progress model, attempts a conditional store write with the expected old value, and returns the new pair or raises if changed.

**Call relations**: post and _deliver_slack_reply use this between delivery steps.

*Call graph*: calls 1 internal fn (put_if); called by 2 (_deliver_slack_reply, post); 2 external calls (__init__, model_dump).


##### `_slack_reply_delivery`  (lines 3374–3389)

```
def _slack_reply_delivery(message: object, delivery_id: str) -> str | None
```

**Purpose**: Checks whether a Slack message carries this file’s delivery metadata id. It is used to recognize a message that may have succeeded before a retry.

**Data flow**: It inspects a Slack message’s metadata event type, payload id, and timestamp, returning the timestamp when the id matches.

**Call relations**: _reconcile_slack_reply calls this while scanning recent Slack messages.

*Call graph*: called by 1 (_reconcile_slack_reply).


##### `_reconcile_slack_reply`  (lines 3392–3432)

```
async def _reconcile_slack_reply(client: httpx.AsyncClient, bot_token: str, channel: str, thread_ts: str | None, delivery_id: str) -> str | None
```

**Purpose**: Looks in Slack for a message that was possibly posted before a delivery retry lost the response. This prevents duplicate final replies.

**Data flow**: It scans recent channel history or thread replies with metadata included, checks each message for the pending delivery id, and returns its timestamp if found.

**Call relations**: post calls this when the checkpoint says a delivery was pending.

*Call graph*: calls 2 internal fn (_slack_ok, _slack_reply_delivery); called by 1 (post); 3 external calls (__init__, get, time).


##### `_deliver_slack_reply`  (lines 3435–3475)

```
async def _deliver_slack_reply(client: httpx.AsyncClient, bot_token: str, store: ScopedStore, key: str, progress: _SlackReplyProgress, expected: JsonValue, delivery_id: str, body: bytes) -> tuple[_Sla
```

**Purpose**: Sends one Slack reply part with checkpointing around the uncertain network call. It can return Slack’s invalid-blocks error without treating it as final failure.

**Data flow**: It skips already-delivered ids, marks the delivery pending, posts the body, handles invalid_blocks by clearing pending, records accepted timestamps, and returns updated progress and payload.

**Call relations**: post calls this for Markdown, section-block fallback, and plain-text fallback reply parts.

*Call graph*: calls 3 internal fn (_chat_post, _checkpoint_slack_reply, _posted_message_ts); called by 1 (post); 2 external calls (__init__, model_copy).


##### `post`  (lines 3478–3625)

```
async def post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Posts the completed agent reply to Slack and returns the first Slack message reference. It handles splitting, footers, question/connect buttons, Slack block fallback, and retry-safe delivery.

**Data flow**: It reads the bot token, builds reply text and actions, builds footer metadata, loads delivery progress, reconciles pending sends, posts each part with checkpoints and fallbacks, marks completion, and returns channel:timestamp.

**Call relations**: Core reply delivery calls this after a turn reaches terminal writeback.

*Call graph*: calls 13 internal fn (credential, _checkpoint_slack_reply, _deliver_slack_reply, _posted_message_ts, _reconcile_slack_reply, _reply_with_oversize_links, _slack_footer, _slack_reply_progress, _slack_reply_progress_key, slack_ask_blocks (+3 more)); 4 external calls (__init__, __init__, __init__, AsyncClient).


##### `_chat_post`  (lines 3628–3668)

```
async def _chat_post(client: httpx.AsyncClient, bot_token: str, body: bytes) -> Mapping[str, object]
```

**Purpose**: Sends one chat.postMessage request and returns Slack’s parsed response without immediately requiring ok:true. This lets callers handle recoverable Slack errors such as invalid blocks.

**Data flow**: It posts bytes to Slack, converts HTTP failures into SurfaceDeliveryError with retry-after information when available, and returns the JSON payload.

**Call relations**: _deliver_slack_reply uses this for final reply message sends.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_deliver_slack_reply); 1 external calls (post).


##### `_posted_message_ts`  (lines 3671–3677)

```
def _posted_message_ts(payload: Mapping[str, object]) -> str
```

**Purpose**: Extracts the timestamp from a successful Slack post response. The timestamp is Slack’s message id inside a channel.

**Data flow**: It checks ok:true, validates ts is a non-empty string, and returns it or raises SlackApiError.

**Call relations**: _deliver_slack_reply and post call this after Slack accepts a message.

*Call graph*: called by 2 (_deliver_slack_reply, post); 1 external calls (__init__).


##### `attach`  (lines 3680–3700)

```
async def attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Uploads shared artifacts from a completed turn to Slack after the text reply is posted. Oversized artifacts are not uploaded here because post already links them.

**Data flow**: It deletes the reply progress checkpoint, filters artifacts within Slack’s upload limit, reads the bot token, uploads all inline artifacts concurrently, and logs individual failures.

**Call relations**: Core calls this after post records the reply reference.

*Call graph*: calls 3 internal fn (credential, _slack_reply_progress_key, _upload_artifact); 2 external calls (__init__, gather).


##### `_upload_artifact`  (lines 3703–3749)

```
async def _upload_artifact(ctx: SurfaceContext, bot_token: str, channel: str, thread_ts: str | None, artifact: SharedArtifact) -> None
```

**Purpose**: Streams one ufo artifact into Slack using Slack’s external upload flow. It avoids buffering the whole file in memory.

**Data flow**: It reserves an upload URL, streams blob bytes to that URL, then completes the upload into the target channel or thread with a title.

**Call relations**: attach calls this concurrently for each uploadable shared artifact.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (attach); 4 external calls (__init__, AsyncClient, Timeout, dumps).


##### `_slack_ok`  (lines 3752–3761)

```
async def _slack_ok(request: Awaitable[httpx.Response]) -> dict[str, object]
```

**Purpose**: Common helper for Slack API calls that must return ok:true. It turns Slack API errors into SlackApiError with useful detail.

**Data flow**: It awaits an HTTP response, checks HTTP status, parses JSON, verifies ok:true, and returns the payload or raises an error with Slack’s error text.

**Call relations**: Most Slack API helpers use this so success checking is consistent across install, reads, writes, status, and uploads.

*Call graph*: called by 15 (_list, _members, _post, _set, _ambient_context, _channel_info, _declared_files, _post_ephemeral, _reconcile_slack_reply, _replace_buttons_with_answer (+5 more)); 1 external calls (__init__).
