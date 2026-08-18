# Slack, shell, debugger, and OAuth callback ingress  `stage-5.2`

This stage is the set of “front doors” where outside tools talk to the system during normal use and account setup. Slack’s surface receives Slack web requests, checks that they really came from Slack, turns messages and button clicks into conversation turns, and sends replies, progress updates, and files back into Slack threads. The Slack mention helpers translate Slack’s hidden user codes into readable names, then restore real mentions when replying. The attribution and hook files add a safe footer to connector-sent Slack messages so people can see which bot sent them, while making sure that footer is not mistaken for a new message to the bot.

The shell surface serves the command-line client. It converts server conversation events into simple text commands that a shell script can display or act on.

The debugger surface is a read-only window for operators. It shows stored conversations, turns, files, transcripts, and live activity for one workspace.

The OAuth callback finishes account-linking after an external provider sends the user back, records the grant, and redirects them to chat.

## Files in this stage

### OAuth callback completion
Completes external account-provider OAuth returns by validating the callback, recording the grant, and sending the user back to chat.

### `core/src/ufo/surfaces/cli.py`

`io_transport` · `request handling`

This file is the small front door for an OAuth callback. OAuth is the common “sign in or connect with another service” flow where a provider redirects the user back with a temporary code. In this project, a chat turn can start a request to connect an outside account. The provider then sends the browser to this endpoint with two important pieces: a sealed state value and a code.

The sealed state is like a tamper-proof claim ticket. It proves which member, agent, and conversation started the connection, without needing the browser to carry a normal login token. The code is the provider’s short-lived proof that the user approved access.

When the callback arrives, the file asks the grants system for the installed connect flow. If connecting accounts is not available, it returns a service error. If the browser did not send both state and code, it rejects the request as a bad callback. Otherwise it asks the flow to complete the connection: verify the state, exchange the code, and store the resulting account grant. Finally it returns a plain text message saying the account is connected and the user should go back to chat.

Without this file, provider redirects would have nowhere useful to land, so account connection flows started from chat could not be completed.

#### Function details

##### `connect_callback`  (lines 19–39)

```
async def connect_callback(state: str='', code: str='') -> PlainTextResponse
```

**Purpose**: This is the HTTP endpoint that finishes an account connection after an OAuth provider redirects the user back. It validates the returned state and code, completes the stored connection flow, and gives the user a simple success message.

**Data flow**: It receives two query values from the browser: state and code. It first gets the installed connection flow; if that system is unavailable, it turns that into a 503 web error. If either value is missing, it returns a 400 web error. It then gives the state and code to the flow, which verifies and records the connection. If the state is invalid, the result is a 400 error; if the provider is unknown, the result is a 404 error. On success, it returns plain text naming the connected provider and account.

**Call relations**: FastAPI calls this function when a browser requests the callback path. The function hands the real connection work to ufo.grants.installed_connect_flow and the flow object it returns. It uses FastAPI HTTPException responses when the callback cannot be completed, and PlainTextResponse when the connection succeeds.

*Call graph*: 3 external calls (HTTPException, PlainTextResponse, installed_connect_flow).


### Debugger inspection surface
Exposes read-only browser and JSON views over workspace conversations, turns, files, transcripts, and live activity.

### `extensions/debugger/ufo_ext_debugger/surface.py`

`io_transport` · `request handling`

This file is like a viewing window into a workspace. Operators use it to inspect what happened in sessions without changing the underlying data. The actual permission check happens before these handlers run: the request is tied to one workspace, and the SurfaceContext carries that workspace identity into every read. That means each API call naturally sees only the scoped workspace’s data.

At the top, the file loads a built React app from static/index.html. The browser gets that page from app_page, then the page asks the api/ routes for data. Most routes are deliberately thin: read one thing from SurfaceContext, turn it into JSON, and return 404 when the requested conversation, turn, file, or compaction cannot be found. This keeps the debugger honest: it shows raw stored records instead of reshaping them into a customer-facing experience.

The one more dynamic part is live streaming. The stream route opens a Server-Sent Events stream, which is a simple browser-friendly way for a server to keep sending updates over one HTTP connection. Live frames are converted into named events such as text, tool, cost, or reply, with a cursor so the browser can resume after a dropped connection. Without this file, the debugger frontend would have no safe, workspace-scoped way to fetch or watch session data.

#### Function details

##### `app_page`  (lines 51–56)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the debugger’s main browser page. If the frontend has not been built yet, it fails loudly so the operator knows the static app is missing rather than seeing a blank or misleading page.

**Data flow**: It receives the current surface context and HTTP request, checks the already-loaded APP_HTML text, and wraps that HTML in an HTML response. If APP_HTML is missing, it raises an error explaining how to build the frontend.

**Call relations**: The route table sends a GET request for the surface root to this function. Its only handoff is to HTMLResponse, which packages the page so the browser can load the debugger app.

*Call graph*: 1 external calls (HTMLResponse).


##### `workspace_meta`  (lines 59–66)

```
async def workspace_meta(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns basic information about the workspace currently being inspected. It also translates a stored Slack installation marker into the Slack team id shown by the debugger.

**Data flow**: It reads the workspace id from SurfaceContext and asks the context for the Slack installation value. If that value starts with the expected team prefix, it strips the prefix; then it returns JSON containing the workspace id and, when available, the Slack team id.

**Call relations**: The debugger frontend calls this through the workspace metadata API route. The function relies on SurfaceContext.installation for the scoped installation lookup and JSONResponse to send the result back to the browser.

*Call graph*: calls 1 internal fn (installation); 1 external calls (JSONResponse).


##### `conversations`  (lines 69–71)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the list of conversations visible in the current workspace. This gives the debugger frontend its starting list of sessions to browse.

**Data flow**: It asks SurfaceContext for the workspace’s conversations, converts each returned model into plain JSON data, and sends the list as a JSON response.

**Call relations**: The route table connects the conversations API endpoint to this function. It delegates the actual scoped read to SurfaceContext.list_conversations, then formats the result for the browser with JSONResponse.

*Call graph*: calls 1 internal fn (list_conversations); 1 external calls (JSONResponse).


##### `conversation_turns`  (lines 74–79)

```
async def conversation_turns(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the turns inside one conversation. A turn is one unit of interaction or work within a conversation, so this lets the debugger show the conversation’s timeline.

**Data flow**: It reads conversation_id from the URL path and uses _uuid_param to make sure it is a valid UUID, which is a standard unique identifier format. If the id is invalid, it returns a 404-style JSON error; otherwise it asks the context for the turns, converts them to JSON, and returns them.

**Call relations**: The route for a conversation’s turns calls this function. It uses _uuid_param as the shared URL-id checker, then hands the valid id to SurfaceContext.list_turns and packages the answer with JSONResponse.

*Call graph*: calls 2 internal fn (list_turns, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_transcript`  (lines 82–89)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the saved transcript for one conversation. This is the full readable record the debugger can display when an operator wants to inspect what was said or recorded.

**Data flow**: It takes conversation_id from the URL, validates it with _uuid_param, and returns a not-found JSON error if the id is bad. With a valid id, it asks SurfaceContext for the transcript; if none exists it returns a separate not-found error, otherwise it serializes the transcript to JSON.

**Call relations**: The transcript API route calls this function when the frontend asks for a conversation transcript. It depends on SurfaceContext.read_transcript for the workspace-scoped read and JSONResponse for both success and error replies.

*Call graph*: calls 2 internal fn (read_transcript, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_compactions`  (lines 92–96)

```
async def conversation_compactions(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the compaction records for a conversation. A compaction is where older conversation history is condensed into a shorter summary, so this helps operators see when and how context was shortened.

**Data flow**: It validates the conversation_id from the path. If the id is not a valid UUID, it returns a not-found JSON error; otherwise it asks SurfaceContext for the conversation’s compaction indexes or records and returns them as a JSON list.

**Call relations**: The compactions list route sends requests here. The function shares id parsing with _uuid_param, reads through SurfaceContext.list_compactions, and returns the browser-ready response with JSONResponse.

*Call graph*: calls 2 internal fn (list_compactions, _uuid_param); 1 external calls (JSONResponse).


##### `compaction_record`  (lines 99–114)

```
async def compaction_record(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the details of one specific compaction. This lets an operator compare what messages existed before compaction, what remained after, and what summary was produced.

**Data flow**: It reads conversation_id and index from the URL. The conversation id must be a valid UUID and the index must be digits; otherwise it returns a not-found JSON error. It then asks SurfaceContext for that compaction record, and if found returns JSON containing the index, before messages, after messages, and summary.

**Call relations**: The route for an individual compaction calls this function. It uses _uuid_param for the conversation id, SurfaceContext.read_compaction for the stored record, and JSONResponse to return either the detailed record or a not-found error.

*Call graph*: calls 2 internal fn (read_compaction, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_files`  (lines 117–122)

```
async def workspace_files(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists files associated with a conversation’s workspace area. This lets the debugger show what files were available or produced during that conversation.

**Data flow**: It validates the conversation_id from the URL. If invalid, it returns a not-found JSON error; if valid, it asks SurfaceContext for the file list, converts each file entry into JSON, and returns the list.

**Call relations**: The conversation files API route calls this function. It uses _uuid_param for safe id parsing, then delegates the scoped file listing to SurfaceContext.list_workspace_files before sending the JSONResponse.

*Call graph*: calls 2 internal fn (list_workspace_files, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_file`  (lines 125–135)

```
async def workspace_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams the contents of one workspace file to the browser. It is used when the debugger needs to download or preview a specific file tied to a conversation.

**Data flow**: It validates conversation_id from the path, then asks SurfaceContext to open the requested file path. If the id is invalid, the path is rejected, or no file exists, it returns a not-found JSON error. If a stream is returned, it sends that stream as raw bytes with an application/octet-stream media type.

**Call relations**: The file-content route calls this function. It reuses _uuid_param, relies on SurfaceContext.read_workspace_file to enforce safe workspace file access, and hands successful file streams to StreamingResponse so the file can be sent without loading it all into memory at once.

*Call graph*: calls 2 internal fn (read_workspace_file, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `turn`  (lines 138–145)

```
async def turn(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns detailed information about one turn. This gives the debugger a focused view of a single unit of work or interaction.

**Data flow**: It reads turn_id from the URL and validates it as a UUID. If the id is invalid or SurfaceContext cannot find the turn, it returns a not-found JSON error; otherwise it serializes the turn detail model into JSON.

**Call relations**: The turn-detail API route calls this function. It uses _uuid_param for URL validation, SurfaceContext.turn_detail for the workspace-scoped lookup, and JSONResponse for both the detail result and errors.

*Call graph*: calls 2 internal fn (turn_detail, _uuid_param); 1 external calls (JSONResponse).


##### `stream`  (lines 148–153)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Starts a live event stream for one turn. This lets the debugger watch a turn unfold in near real time, or resume from the last event the browser already received.

**Data flow**: It validates turn_id and checks that the turn exists. If not, it returns a not-found JSON error. If the turn exists, it reads the Last-Event-ID header, which tells the server where the browser left off, then returns a streaming response whose body is produced by _events.

**Call relations**: The live-stream API route calls this function. It checks the turn through SurfaceContext.turn_detail, then hands the live streaming work to _events and wraps it in StreamingResponse using the text/event-stream format used by Server-Sent Events.

*Call graph*: calls 3 internal fn (turn_detail, _events, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `_events`  (lines 156–159)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, since: str) -> AsyncIterator[bytes]
```

**Purpose**: Turns the context’s live frame tail into a stream of bytes the browser can consume. It is the bridge between the project’s internal live updates and the web protocol used by the debugger.

**Data flow**: It receives a SurfaceContext, a turn id, and a starting cursor. It opens ctx.tail, which yields live frames with cursors, then converts each cursor-and-frame pair into Server-Sent Event bytes by calling _sse. The output is an asynchronous sequence of byte chunks.

**Call relations**: stream calls this helper after it has validated the requested turn. _events depends on SurfaceContext.tail to follow live activity and passes each frame to _sse so the stream uses the exact event format expected by the browser.

*Call graph*: calls 2 internal fn (tail, _sse); called by 1 (stream).


##### `_sse`  (lines 162–188)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Formats one live frame as one Server-Sent Event message. It names the event by the kind of frame, such as text, tool, reply, or cost, and includes the frame’s raw JSON for debugging.

**Data flow**: It receives a cursor and a LiveFrame object. If the cursor is not empty, it writes it as the event id so the browser can resume later. It then matches the frame type, chooses an event name, serializes the frame to JSON, and returns the complete event as bytes.

**Call relations**: _events calls this for every live frame coming from SurfaceContext.tail. _sse uses the frame model’s JSON serialization and raises an error if a new or unknown live frame type has not been mapped, which protects the debugger from silently hiding unfamiliar events.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `_uuid_param`  (lines 191–195)

```
def _uuid_param(request: Request, name: str) -> UUID | None
```

**Purpose**: Safely reads a UUID-shaped path parameter from a request. It keeps route handlers from repeating the same validation code for conversation and turn ids.

**Data flow**: It looks up the named path parameter in the request and tries to turn the text into a UUID object. If the text is not a valid UUID, it returns None; otherwise it returns the parsed UUID.

**Call relations**: The conversation, compaction, file, turn, and stream handlers call this helper before asking SurfaceContext for data. By returning None for bad ids, it lets those handlers respond with clean not-found JSON errors instead of crashing on malformed URLs.

*Call graph*: called by 8 (compaction_record, conversation_compactions, conversation_transcript, conversation_turns, stream, turn, workspace_file, workspace_files); 1 external calls (UUID).


### Slack message semantics
Keeps Slack connector attribution, bot mention detection, and human-readable mention translation consistent before events reach the main Slack surface.

### `extensions/slack/ufo_ext_slack/attribution.py`

`domain_logic` · `Slack connector send and Slack event handling`

When this system sends a Slack message through a connector, the message is not rendered by the normal Slack-facing part of the app. That means the usual place to add a clear “sent by UFO” style footer is bypassed. This file adds that footer itself, but uses a real Slack mention of the bot user, so a reader can click or identify the agent from the message.

There is a second, equally important side to this. Slack may later report that same bot mention as if someone had addressed the bot. Without a guard, the system could mistake its own attribution footer for a user asking the agent something. This file prevents that by stripping known attribution text before checking whether a message really mentions the bot.

The pieces work together like a label maker and a label reader. `mention_attributed` adds the label to outgoing connector arguments. `addressing_mention` ignores that label when deciding whether incoming text is meant for the bot. `message_bodies` looks through all the places Slack can hide text, not just the main message text, because Slack block messages can carry text inside nested structures. `_nested_strings` does that nested search.

#### Function details

##### `is_slack_send`  (lines 36–45)

```
def is_slack_send(provider: str, slug: str) -> bool
```

**Purpose**: This function decides whether a connector call is the kind of call that publishes a Slack message. It uses the same basic test as the connector attribution code, so both sides agree about which sends need a footer.

**Data flow**: It receives a connector provider name and a connector action slug. It lowercases the slug, checks that the provider is Slack, checks that the slug refers to messages, and checks that it contains a send-like verb. It returns `true` only when all of those clues say this is a Slack message send.

**Call relations**: This is the gatekeeper for Slack attribution decisions. Other code can call it before changing connector arguments, so the footer is only added to real Slack message sends and not to unrelated connector actions.


##### `mention_attributed`  (lines 48–55)

```
def mention_attributed(arguments: dict[str, JsonValue], bot_user_id: str) -> dict[str, JsonValue]
```

**Purpose**: This function adds the bot-mention attribution footer to the arguments for a Slack send. It also avoids adding a second footer if the message is already attributed.

**Data flow**: It receives the connector arguments for a Slack message and the Slack bot user ID. It builds the footer subject by inserting that bot user ID into the shared attribution mention template, then passes the arguments and subject to the connector attribution helper. The result is a new or updated arguments dictionary with the footer included when appropriate, or the original shape left alone when a footer is already present.

**Call relations**: This function is the Slack-specific wrapper around the shared connector attribution behavior. It asks `UFO_ATTRIBUTION_MENTION_SUBJECT.format` to create the bot mention text, then hands the real footer work to `attributed_arguments`, so Slack follows the same footer format as the connector system while changing only who is named.

*Call graph*: 2 external calls (format, attributed_arguments).


##### `addressing_mention`  (lines 58–68)

```
def addressing_mention(text: str, bot_user_id: str) -> bool
```

**Purpose**: This function tells whether a piece of Slack text truly mentions the bot, while ignoring mentions that appear only inside this system’s own attribution footer. It prevents the system from treating its own sent messages as user requests.

**Data flow**: It receives some text and the bot user ID. First it removes any recognized attribution footer from the text. Then it looks for Slack’s mention form for that bot, such as `<@BOTID>`. It returns `true` if the bot is still mentioned after the footer is removed, and `false` otherwise.

**Call relations**: This function is used on the incoming-message side of the same agreement that `mention_attributed` uses on the outgoing side. It relies on `attribution_stripped` to remove the footer format produced by the connector attribution code, then performs the actual mention check.

*Call graph*: 1 external calls (attribution_stripped).


##### `message_bodies`  (lines 71–77)

```
def message_bodies(event: Mapping[str, object]) -> tuple[str, ...]
```

**Purpose**: This function collects every text fragment in a Slack message event where a bot mention might appear. It matters because Slack messages can store visible text not only in the top-level `text` field, but also inside nested block structures.

**Data flow**: It receives a Slack event represented as a mapping. It reads the event’s main text field, using an empty string if it is missing, then reads the event’s blocks field and extracts every nested string inside it. It returns all of those text pieces as a tuple.

**Call relations**: This function prepares incoming Slack event text for mention checks such as `addressing_mention`. It delegates the recursive search through Slack blocks to `_nested_strings`, so callers do not need to know how deeply Slack may nest text inside the event.

*Call graph*: calls 1 internal fn (_nested_strings).


##### `_nested_strings`  (lines 80–89)

```
def _nested_strings(value: object) -> Iterator[str]
```

**Purpose**: This helper walks through a nested value and yields every string it can find. It is used to search Slack block data, where text can be buried inside dictionaries and lists.

**Data flow**: It receives any value. If the value is a string, it yields that string. If it is a mapping, it searches each value inside it. If it is a list, it searches each item. Other kinds of values are ignored. The output is a stream of strings found anywhere in the nested structure.

**Call relations**: This is the small recursive worker behind `message_bodies`. `message_bodies` asks it to inspect the Slack blocks field, and `_nested_strings` returns the text fragments that the higher-level Slack mention logic needs.

*Call graph*: called by 1 (message_bodies).


### `extensions/slack/ufo_ext_slack/hooks.py`

`domain_logic` · `pre-tool-use request handling`

This file exists because the generic connector tool can send a Slack message, but it does not know which Slack bot user is speaking in a particular workspace. The Slack extension does know that, because the Slack surface records the bot user ID after installation. So this hook steps in just before the connector tool runs and rewrites the message text to include a footer that mentions the bot user.

The flow is deliberately cautious. The hook only acts when the upcoming tool call is really a Slack send. Then it tries to read the bot user ID from the extension's own scoped store, which is like a small private notebook for this extension and workspace. That read has a short timeout. If the store is slow, missing, or returns something that does not look like a Slack bot user ID, the hook gives up and returns nothing. In that case, the connector continues normally and adds its own generic attribution instead.

This matters because this hook runs in a gatekeeping moment before a tool is allowed to execute. If the hook crashed or took too long, the message could be denied and never sent. The code treats the footer as cosmetic, not mission-critical, so every failure becomes “do not modify” rather than “stop the send.”

#### Function details

##### `attribute_connector_send`  (lines 30–44)

```
async def attribute_connector_send(ctx: HookContext) -> HookOutcome
```

**Purpose**: This is the hook that runs before a tool call and decides whether to rewrite the outgoing message. It only changes connector calls that are Slack sends, and only when it can confirm the Slack bot user ID for this workspace.

**Data flow**: It receives a hook context containing the pending tool call. If the payload is a connector tool input and the source and tool name identify it as a Slack send, it asks for the mirrored bot user ID. If an ID is found, it passes the original message arguments through the attribution helper, then returns a modified version of the tool input. If the call is not a Slack send, or no valid bot user ID is available, it returns no change.

**Call relations**: This function is called by the hook system during the pre-tool-use phase, just before the external connector tool would run. It asks `_mirrored_self_user_id` for the workspace's bot ID, uses `is_slack_send` to avoid touching unrelated connector calls, uses `mention_attributed` to add the Slack mention footer, and returns `ModifyInput` so the tool receives the rewritten arguments.

*Call graph*: calls 1 internal fn (_mirrored_self_user_id); 3 external calls (__init__, is_slack_send, mention_attributed).


##### `_mirrored_self_user_id`  (lines 47–61)

```
async def _mirrored_self_user_id(ctx: HookContext) -> str | None
```

**Purpose**: This helper reads the Slack bot user ID that the Slack surface previously stored for this workspace. It is intentionally safe and quick: if anything goes wrong, it returns nothing instead of risking a blocked Slack send.

**Data flow**: It receives the hook context and uses it to read one known key from the extension's scoped store. The read must finish within a short timeout. If the value is a string and matches the expected Slack bot user ID pattern, that string comes out. If the read fails, times out, or returns an invalid value, the function logs the failure when appropriate and returns `None`.

**Call relations**: This function is used by `attribute_connector_send` when a connector call has already been recognized as a Slack send. It relies on `asyncio.timeout` to keep the hook from waiting too long, uses `re.match` to check that the stored value has the expected shape, and logs store-read failures through the observability logger so operators can investigate without interrupting the message flow.

*Call graph*: called by 1 (attribute_connector_send); 3 external calls (timeout, match, log).


### `extensions/slack/ufo_ext_slack/mentions.py`

`domain_logic` · `message ingest and reply sending`

Slack does not send every message exactly as a person sees it. A person may see `@Alex`, but Slack may deliver `<@U123>`. A channel, broadcast like `@here`, or labeled link can also arrive in this encoded form. This file is the translator between those two worlds.

On the way into the system, `render_markup` rewrites Slack's codes into readable words, using known user and channel names when it can. If it cannot safely identify a name, it leaves the original code alone rather than guessing. That matters because the stored conversation, summaries, and model prompts should all read like a real conversation, not a list of Slack IDs.

On the way out, `mention_markup` does a narrower job. If the agent writes `@Alex`, it may need to become `<@U123>` so Slack actually notifies Alex. The file is careful here: it only maps names the caller has explicitly allowed, avoids code blocks and links, refuses ambiguous names, never turns names into broad broadcasts like `@here`, and caps how many mentions it will create. This prevents an agent reply that quotes a crowd from accidentally paging everyone.

The file also keeps Slack's escape sequences separate. Turning `&lt;` into `<` is only safe for the message author's own words, so unescaping is deliberately not mixed into general mention rendering.

#### Function details

##### `mentioned_users`  (lines 67–70)

```
def mentioned_users(text: str) -> frozenset[str]
```

*Call graph*: calls 1 internal fn (_mentioned).


##### `mentioned_channels`  (lines 73–75)

```
def mentioned_channels(text: str) -> frozenset[str]
```

*Call graph*: calls 1 internal fn (_mentioned).


##### `_mentioned`  (lines 78–83)

```
def _mentioned(text: str, kind: str) -> frozenset[str]
```

*Call graph*: called by 2 (mentioned_channels, mentioned_users).


##### `render_markup`  (lines 86–96)

```
def render_markup(text: str, names: Mapping[str, str]) -> str
```


##### `unescape`  (lines 99–108)

```
def unescape(text: str) -> str
```


##### `mention_key`  (lines 111–115)

```
def mention_key(name: str) -> str
```

*Call graph*: called by 2 (_mention_at, mention_index).


##### `mention_index`  (lines 118–132)

```
def mention_index(names: Mapping[str, str]) -> dict[str, str]
```

*Call graph*: calls 1 internal fn (mention_key).


##### `mention_markup`  (lines 135–166)

```
def mention_markup(text: str, ids: Mapping[str, str], limit: int=MENTION_MARKUP_MAX) -> str
```

*Call graph*: calls 1 internal fn (_mention_at); 1 external calls (finditer).


##### `_mention_at`  (lines 169–182)

```
def _mention_at(text: str, start: int, ids: Mapping[str, str]) -> tuple[int, str] | None
```

*Call graph*: calls 1 internal fn (mention_key); called by 1 (mention_markup); 2 external calls (islice, finditer).


##### `_entity`  (lines 185–201)

```
def _entity(match: re.Match[str], names: Mapping[str, str]) -> str
```


### `extensions/slack/ufo_ext_slack/surface.py`

`io_transport` · `install, request handling, turn admission, live turn following, reply delivery`

This file is the bridge between Slack and the core ufo conversation system. Without it, Slack could not safely install the app, identify workspaces, decide which messages are meant for the agent, download attached files, or deliver replies back to the right thread.

It does several jobs. First, it proves incoming HTTP requests really came from Slack by checking Slack's signature. Then it figures out which workspace the request belongs to and whether the message should become a ufo turn. Direct messages and @mentions are admitted right away. Unmentioned replies in an active thread are handled more carefully: they may be absorbed into a running turn, or a separate model decision may decide whether the agent should speak.

The file also translates between Slack's world and ufo's world. Slack threads become conversation keys. Slack user IDs are resolved to member identities. Slack file downloads are streamed into the workspace, and ufo shared artifacts are streamed back to Slack uploads.

While a turn runs, background follower tasks keep Slack's native “thinking” status fresh and, for long waits, post occasional progress messages. When the turn finishes, this file formats the answer, questions, connect buttons, footer links, and attachments, with checkpoints so retries do not post duplicates.

#### Function details

##### `_env_signing_secret`  (lines 217–221)

```
def _env_signing_secret() -> str | None
```

**Purpose**: Reads the deploy-wide Slack signing secret from the process environment. This is the fallback secret used when a workspace does not store its own Slack signing secret.

**Data flow**: It reads one environment variable and returns the secret string, or returns nothing if it is unset.

**Call relations**: Workspace-specific secret lookup helpers call this when their stored credential path is unavailable.

*Call graph*: called by 2 (_auth_signing_secret, _ctx_signing_secret).


##### `_ctx_signing_secret`  (lines 224–231)

```
async def _ctx_signing_secret(ctx: SurfaceContext) -> str | None
```

**Purpose**: Finds the signing secret to use for a request after a workspace has already been chosen. This lets bring-your-own Slack apps use their private secret while OAuth installs use the deploy secret.

**Data flow**: It asks the surface context for the workspace credential. If that slot is empty, it falls back to the environment secret and returns whichever one exists.

**Call relations**: The Slack event and interactivity routes call it before verifying request signatures.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 2 (ingest, interactive).


##### `_auth_signing_secret`  (lines 234–242)

```
async def _auth_signing_secret(auth: SurfaceAuth, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the signing secret for a workspace during the early routing stage, before the normal workspace context is bound.

**Data flow**: It receives an auth helper and workspace ID, tries to read that workspace's stored signing secret, falls back to the deploy secret, and returns none if the workspace is unknown or no secret exists.

**Call relations**: The workspace resolver uses it after a Slack team ID points to a possible workspace, so it can verify the raw request before trusting it.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 1 (resolve_workspace).


##### `slack_client_id`  (lines 245–249)

```
def slack_client_id() -> str
```

**Purpose**: Reads the Slack OAuth client ID from the environment. OAuth cannot start without this public app identifier.

**Data flow**: It reads one environment variable and returns it, or raises an error if it is missing.

**Call relations**: The OAuth exchange uses it when trading Slack's temporary authorization code for a bot token.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_client_secret`  (lines 252–256)

```
def slack_client_secret() -> str
```

**Purpose**: Reads the Slack OAuth client secret from the environment. This secret proves the deploy owns the Slack app during installation.

**Data flow**: It reads one environment variable and returns it, or raises an error if it is missing.

**Call relations**: The OAuth exchange sends it to Slack along with the client ID and authorization code.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_oauth_redirect_uri`  (lines 259–261)

```
def slack_oauth_redirect_uri(public_base_url: str) -> str
```

**Purpose**: Builds the callback URL Slack should send the installer back to after OAuth approval.

**Data flow**: It takes the public base URL of the deploy, trims a trailing slash, appends the Slack surface OAuth path, and returns the full URL.

**Call relations**: The OAuth callback uses the same URL during code exchange that was used when the install link was created.

*Call graph*: called by 1 (oauth_callback).


##### `slack_authorize_url`  (lines 264–276)

```
def slack_authorize_url(client_id: str, redirect_uri: str, state: str) -> str
```

**Purpose**: Builds the “Add to Slack” link an owner clicks to install the app.

**Data flow**: It takes a client ID, redirect URL, and sealed state token, combines them with the required Slack permission scopes, URL-encodes them, and returns Slack's authorization URL.

**Call relations**: It prepares the browser side of the OAuth install flow; the callback later receives the state and code produced by that flow.

*Call graph*: 1 external calls (urlencode).


##### `SlackIdentityError.__init__`  (lines 280–282)

```
def __init__(self, error: str)
```

**Purpose**: Creates an error that carries Slack identity or install failure text in a predictable field.

**Data flow**: It stores the error string on the object and passes it to the normal runtime error constructor.

**Call relations**: Identity proving and OAuth exchange raise this when Slack responses are malformed or rejected.

*Call graph*: called by 2 (_prove, slack_oauth_exchange).


##### `bot_token_fingerprint`  (lines 299–300)

```
def bot_token_fingerprint(bot_token: str) -> str
```

**Purpose**: Creates a safe fingerprint of a Slack bot token. The fingerprint can be stored and compared without storing the token itself in metadata.

**Data flow**: It takes the token text, hashes it with SHA-256, and returns the hex digest.

**Call relations**: Identity reads and writes use it to make sure stored team and bot-user IDs still belong to the current token.

*Call graph*: called by 3 (_prove, oauth_callback, read_identity); 1 external calls (sha256).


##### `read_identity`  (lines 303–315)

```
async def read_identity(blob: BlobStore, bot_token: str) -> SlackIdentity | None
```

**Purpose**: Reads the stored Slack identity record for a workspace, but only if it matches the current bot token.

**Data flow**: It checks whether the identity blob exists, parses it, compares its token fingerprint to the current token, and returns the identity or none.

**Call relations**: Install, request handling, and identity helpers use it to avoid routing Slack events with stale app metadata.

*Call graph*: calls 3 internal fn (exists, get, bot_token_fingerprint); called by 3 (resolve, _identity, resolve_self_user_id).


##### `resolve_self_user_id`  (lines 318–324)

```
async def resolve_self_user_id(ctx: SurfaceIdentityContext) -> str | None
```

**Purpose**: Returns the bot user's Slack ID for this workspace if the bot token and identity record are available.

**Data flow**: It reads the bot token credential, reads the matching identity blob, and returns the bot user ID or none.

**Call relations**: This is a small identity hook used by code that needs to know which Slack user is the app itself.

*Call graph*: calls 1 internal fn (read_identity); 1 external calls (credential).


##### `_identity`  (lines 327–335)

```
async def _identity(ctx: SurfaceContext) -> SlackIdentity | None
```

**Purpose**: Loads the current workspace's Slack identity for request handling. It also mirrors the bot user ID into a store that turn-time hooks can read.

**Data flow**: It reads the bot token, validates the identity blob against it, mirrors the bot user ID if found, and returns the identity or none.

**Call relations**: The event route, interactive route, and mention-mapping code call it before they trust team IDs or bot-user IDs.

*Call graph*: calls 3 internal fn (credential, _mirror_self_user_id, read_identity); called by 3 (_reply_mention_ids, ingest, interactive).


##### `_mirror_self_user_id`  (lines 341–357)

```
async def _mirror_self_user_id(workspace_id: UUID, bot_user_id: str) -> None
```

**Purpose**: Copies the bot's Slack user ID into this extension's scoped store. This makes the ID available to hook code that cannot read the identity blob directly.

**Data flow**: It takes a workspace ID and bot user ID, skips the write if this process already mirrored the same value, otherwise writes it best-effort and updates a process cache.

**Call relations**: Identity loading and OAuth installation call it after proving the app's Slack identity.

*Call graph*: called by 2 (_identity, oauth_callback); 1 external calls (__init__).


##### `SlackIdentityResolver.resolve`  (lines 370–376)

```
async def resolve(self) -> SlackIdentity
```

**Purpose**: Returns a proven Slack identity for a bring-your-own-app install. It reuses a valid stored identity or proves the token through Slack.

**Data flow**: It reads any current identity, returns it if valid, otherwise calls Slack to prove the token and stores the resulting identity blob.

**Call relations**: Background identity proof uses this when an installed workspace has credentials but no identity record yet.

*Call graph*: calls 2 internal fn (_prove, read_identity).


##### `SlackIdentityResolver._prove`  (lines 378–403)

```
async def _prove(self) -> SlackIdentity
```

**Purpose**: Asks Slack what workspace and bot user a pasted bot token belongs to.

**Data flow**: It calls Slack auth.test with the bot token, validates the response shape and ID formats, and returns a SlackIdentity with a token fingerprint.

**Call relations**: The resolver calls it only when no matching identity is already stored.

*Call graph*: calls 2 internal fn (__init__, bot_token_fingerprint); called by 1 (resolve); 3 external calls (__init__, AsyncClient, match).


##### `_prove_identity_in_background`  (lines 409–422)

```
def _prove_identity_in_background(ctx: SurfaceContext) -> None
```

**Purpose**: Starts a background task to prove Slack identity without blocking the incoming Slack request.

**Data flow**: It checks whether a proof task already exists for the workspace, creates one if not, and registers cleanup when the task ends.

**Call relations**: The event and interactive routes use it when credentials exist but the identity record is missing.

*Call graph*: calls 1 internal fn (_run_identity_proof); called by 2 (ingest, interactive); 1 external calls (create_task).


##### `_prove_identity_in_background._untrack`  (lines 418–420)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Removes a finished identity-proof task from the per-process tracking table.

**Data flow**: It receives the completed task and deletes it only if it is still the tracked task for that workspace.

**Call relations**: It is attached as the completion callback for the background identity-proof task.


##### `_run_identity_proof`  (lines 425–432)

```
async def _run_identity_proof(ctx: SurfaceContext) -> None
```

**Purpose**: Performs the actual background identity proof and logs failures.

**Data flow**: It reads the bot token from the workspace, runs the Slack identity resolver, and catches expected and unexpected errors so the request path is not crashed.

**Call relations**: The background task launcher creates this coroutine when identity needs proving after an inbound request.

*Call graph*: calls 1 internal fn (credential); called by 1 (_prove_identity_in_background); 1 external calls (__init__).


##### `signing_secret_fingerprint`  (lines 438–441)

```
def signing_secret_fingerprint(signing_secret: str) -> str
```

**Purpose**: Creates a safe fingerprint of a Slack signing secret. This lets the system know which secret proved a URL without storing the secret in the marker.

**Data flow**: It hashes the secret text with SHA-256 and returns the hex digest.

**Call relations**: URL verification marking uses it before writing the proof marker.

*Call graph*: called by 1 (_mark_url_verified); 1 external calls (sha256).


##### `slack_oauth_exchange`  (lines 455–482)

```
async def slack_oauth_exchange(code: str, redirect_uri: str) -> SlackInstall
```

**Purpose**: Trades Slack's temporary OAuth code for the workspace bot token and identity details.

**Data flow**: It posts the code, client credentials, and redirect URL to Slack, validates the returned token, team ID, and bot user ID, then returns a SlackInstall object.

**Call relations**: The OAuth callback calls it after validating the sealed install state.

*Call graph*: calls 4 internal fn (__init__, _slack_ok, slack_client_id, slack_client_secret); called by 1 (oauth_callback); 3 external calls (__init__, AsyncClient, match).


##### `SlackConversationSearch.run`  (lines 569–583)

```
async def run(self) -> SlackConversationMatches
```

**Purpose**: Searches Slack conversations a user might want the agent to act in. It supports channels and direct-message rooms by matching names, topics, purposes, and people.

**Data flow**: It normalizes the query, lists Slack conversations, resolves people for DMs, converts raw Slack rows into conversation objects, filters matches, and returns matches plus a truncation flag.

**Call relations**: It coordinates the helper methods that list conversations, resolve people, and shape final search results.

*Call graph*: calls 3 internal fn (_conversation, _list, _people); 2 external calls (__init__, AsyncClient).


##### `SlackConversationSearch._list`  (lines 585–604)

```
async def _list(self, client: httpx.AsyncClient) -> tuple[list[object], bool]
```

**Purpose**: Fetches a bounded number of Slack conversation-list pages.

**Data flow**: It repeatedly calls Slack conversations.list with a cursor, gathers channel rows, and returns the rows plus whether the page limit was reached.

**Call relations**: The search runner calls it before resolving people and filtering matches.

*Call graph*: calls 3 internal fn (_next_cursor, _params, _slack_ok); called by 1 (run); 1 external calls (get).


##### `SlackConversationSearch._params`  (lines 606–614)

```
def _params(self, cursor: str) -> dict[str, str]
```

**Purpose**: Builds the query parameters for one Slack conversations.list request.

**Data flow**: It starts with conversation types, archive exclusion, and page size, adds a cursor when present, and returns the parameter dictionary.

**Call relations**: The list helper calls it for every page request.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._next_cursor`  (lines 616–619)

```
def _next_cursor(self, payload: dict[str, object]) -> str
```

**Purpose**: Extracts Slack's next-page cursor from a response.

**Data flow**: It reads response metadata, returns the next cursor if it is a string, otherwise returns an empty string.

**Call relations**: The list helper uses it to decide whether to fetch another page.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._people`  (lines 621–649)

```
async def _people(self, client: httpx.AsyncClient, listed: list[object]) -> tuple[dict[str, tuple[str, ...]], bool]
```

**Purpose**: Builds readable people labels for direct messages and group DMs.

**Data flow**: It gathers member IDs for a bounded number of DM rooms, resolves each user once, labels them by name and email when available, and returns labels by conversation plus whether it hit the cap.

**Call relations**: The search runner uses these labels so DM search can match people rather than unnamed room IDs.

*Call graph*: calls 4 internal fn (_kind, _label, _members, _slack_user); called by 1 (run).


##### `SlackConversationSearch._kind`  (lines 651–658)

```
def _kind(self, raw: dict[str, object]) -> SlackConversationKind
```

**Purpose**: Classifies a raw Slack conversation as public channel, private channel, group DM, or one-to-one DM.

**Data flow**: It reads Slack boolean flags and returns the matching kind string.

**Call relations**: Conversation conversion, member lookup, and people resolution use this classification.

*Call graph*: called by 3 (_conversation, _members, _people).


##### `SlackConversationSearch._members`  (lines 660–674)

```
async def _members(self, client: httpx.AsyncClient, raw: dict[str, object], convo_id: str) -> tuple[str, ...]
```

**Purpose**: Finds member IDs for a DM-style conversation.

**Data flow**: For a one-to-one DM it reads the user ID from the conversation row. For a group DM it calls Slack conversations.members and returns the IDs Slack provides.

**Call relations**: People resolution calls it before turning member IDs into readable labels.

*Call graph*: calls 2 internal fn (_kind, _slack_ok); called by 1 (_people); 1 external calls (get).


##### `SlackConversationSearch._label`  (lines 676–681)

```
def _label(self, user: SlackUser | None, user_id: str) -> str
```

**Purpose**: Chooses a readable label for a Slack user in search results.

**Data flow**: It receives a resolved user and fallback ID, combines name and email when both exist, or falls back to whichever field is available.

**Call relations**: People resolution uses it after each Slack user lookup.

*Call graph*: called by 1 (_people).


##### `SlackConversationSearch._conversation`  (lines 683–700)

```
def _conversation(self, raw: object, people: dict[str, tuple[str, ...]]) -> SlackConversation | None
```

**Purpose**: Turns one raw Slack conversation row into the smaller model used by search results.

**Data flow**: It validates the row and ID, reads name, purpose, topic, people labels, kind, and membership flag, then returns a SlackConversation or none.

**Call relations**: The search runner calls it for each listed Slack row before filtering by query.

*Call graph*: calls 2 internal fn (_kind, _nested_value); called by 1 (run); 1 external calls (__init__).


##### `SlackConversationSearch._nested_value`  (lines 702–704)

```
def _nested_value(self, field: object) -> str
```

**Purpose**: Safely extracts Slack's nested purpose or topic text.

**Data flow**: It receives a field that may be a dictionary, reads its value entry if it is text, and otherwise returns an empty string.

**Call relations**: Conversation conversion uses it for purpose and topic fields.

*Call graph*: called by 1 (_conversation); 1 external calls (get).


##### `verify_slack_signature`  (lines 860–875)

```
def verify_slack_signature(headers: Mapping[str, str], body: bytes, signing_secret: str, now: float | None=None) -> None
```

**Purpose**: Checks that a request really came from Slack and is recent enough to prevent replay. A replay is an old valid request sent again by an attacker.

**Data flow**: It reads timestamp and signature headers, checks the timestamp age, rebuilds Slack's expected HMAC signature from the raw body and signing secret, and raises an error if anything does not match.

**Call relations**: Workspace resolution, event ingest, and interactivity ingest all call it before trusting request contents.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 4 external calls (__init__, compare_digest, new, time).


##### `_slack_request_body`  (lines 882–901)

```
async def _slack_request_body(request: Request) -> bytes
```

**Purpose**: Reads and caches the raw request body while enforcing a size limit.

**Data flow**: It returns a cached body if present, otherwise streams chunks from the request, stops if the total is too large, caches the bytes or overflow marker, and returns the bytes.

**Call relations**: All Slack routes use it because signature verification must use the exact raw bytes Slack signed.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 1 external calls (stream).


##### `url_verification_challenge`  (lines 904–913)

```
def url_verification_challenge(body: bytes) -> str | None
```

**Purpose**: Detects Slack's URL verification handshake and extracts the challenge text to echo back.

**Data flow**: It parses the body as JSON, checks for type url_verification, and returns the challenge string or none.

**Call relations**: Workspace resolution and event ingest use it to answer Slack setup probes without creating turns.

*Call graph*: called by 2 (ingest, resolve_workspace); 1 external calls (loads).


##### `slack_team_hint`  (lines 916–933)

```
def slack_team_hint(body: bytes) -> str | None
```

**Purpose**: Extracts a Slack team ID from an untrusted request body so routing can find the possible workspace.

**Data flow**: It tries JSON first, then form-encoded interactive payloads, validates the team ID shape, and returns it or none.

**Call relations**: The workspace resolver uses this hint only to choose a candidate workspace whose secret will then verify the request.

*Call graph*: called by 1 (resolve_workspace); 3 external calls (loads, fullmatch, parse_qs).


##### `slack_installation_id`  (lines 936–937)

```
def slack_installation_id(team_id: str) -> str
```

**Purpose**: Turns a Slack team ID into the installation key used by ufo.

**Data flow**: It prefixes the team ID with a fixed label and returns the string.

**Call relations**: OAuth binding writes this key, and request resolution reads it to map Slack teams back to workspaces.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `resolve_workspace`  (lines 940–978)

```
async def resolve_workspace(request: Request, auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Figures out which ufo workspace a Slack request belongs to before normal request handling begins.

**Data flow**: For OAuth callbacks it opens the sealed state and returns the workspace. For Slack POSTs it reads the raw body, handles URL verification, extracts a team hint, finds the installed workspace, verifies the signature with that workspace's secret, and returns the workspace or a response.

**Call relations**: This is the pre-routing guard for all Slack routes; it calls the low-level body, team, install, secret, and signature helpers.

*Call graph*: calls 9 internal fn (open_credential_authorization, workspace, _auth_signing_secret, _is_install_state, _slack_request_body, slack_installation_id, slack_team_hint, url_verification_challenge, verify_slack_signature); 1 external calls (JSONResponse).


##### `_is_install_state`  (lines 981–984)

```
def _is_install_state(claims: CredentialRequestState) -> bool
```

**Purpose**: Checks whether a sealed credential state belongs to Slack OAuth installation.

**Data flow**: It inspects the state payload and requested credential slots and returns true only for the Slack bot-token install marker.

**Call relations**: Workspace resolution and the OAuth callback use it to reject unrelated sealed states.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `slack_thread_key`  (lines 987–992)

```
def slack_thread_key(channel: str, root_ts: str, is_dm: bool) -> str
```

**Purpose**: Builds the ufo conversation key for a Slack message.

**Data flow**: It returns just the channel for DMs, and channel plus root timestamp for channel threads.

**Call relations**: Inbound message parsing and interactive click parsing use it so later replies go to the same conversation.

*Call graph*: called by 2 (_to_click, _to_inbound).


##### `slack_message_addressed`  (lines 995–1013)

```
def slack_message_addressed(event: Mapping[str, object], bot_user_id: str, is_dm: bool) -> bool
```

**Purpose**: Decides whether a Slack message is directly asking the agent to participate.

**Data flow**: It treats DMs as addressed, scans message bodies for a real mention of the bot, and carefully avoids treating the agent's own attribution footer as a new request.

**Call relations**: Inbound parsing uses it to decide whether a message is admitted immediately or considered ambient thread chatter.

*Call graph*: called by 1 (_to_inbound); 2 external calls (addressing_mention, message_bodies).


##### `_link_count`  (lines 1016–1019)

```
def _link_count(text: str) -> int
```

**Purpose**: Counts links in reply text so Slack previews can be limited.

**Data flow**: It counts Markdown links, removes them, counts remaining bare URLs, and returns the total.

**Call relations**: Slack reply body building uses it to disable link unfurls when a reply has many links.

*Call graph*: called by 1 (slack_reply_body); 2 external calls (findall, sub).


##### `slack_reply_parts`  (lines 1022–1099)

```
def slack_reply_parts(text: str, limit: int=SLACK_MARKDOWN_TEXT_LIMIT) -> list[str]
```

**Purpose**: Splits long Slack replies into message-sized pieces without cutting awkwardly through Markdown when possible.

**Data flow**: It receives text and a size limit, finds code blocks and tables as safer atomic regions, chooses cut points at paragraph, line, sentence, or word boundaries, and returns the parts.

**Call relations**: Reply posting, mid-turn speaking, and body building use it before sending text to Slack.

*Call graph*: called by 3 (post, slack_reply_body, speak); 2 external calls (finditer, match).


##### `slack_reply_body`  (lines 1102–1169)

```
def slack_reply_body(channel: str, thread_ts: str | None, text: str, metadata: str | None, delivery_id: str | None=None, blocks: bool=True, actions: list[dict[str, object]] | None=None, sections: bool
```

**Purpose**: Builds the JSON body for one Slack chat.postMessage call.

**Data flow**: It takes channel, thread, text, optional metadata, buttons, and formatting options; adds message metadata and Block Kit blocks when possible; disables excessive unfurls; falls back to plain text when needed; and returns encoded JSON bytes.

**Call relations**: Terminal replies, mid-turn replies, and progress posts all use it before calling Slack.

*Call graph*: calls 2 internal fn (_link_count, slack_reply_parts); called by 3 (_post, post, speak); 2 external calls (dumps, sub).


##### `_mrkdwn_section`  (lines 1172–1173)

```
def _mrkdwn_section(text: str) -> dict[str, object]
```

**Purpose**: Creates one Slack Block Kit section containing Slack markdown text.

**Data flow**: It trims the text to Slack's section limit and wraps it in the dictionary Slack expects.

**Call relations**: Question rendering uses it to build ask-user blocks.

*Call graph*: called by 1 (slack_ask_blocks).


##### `slack_ask_blocks`  (lines 1176–1225)

```
def slack_ask_blocks(question: AskUserInput | None) -> list[dict[str, object]] | None
```

**Purpose**: Renders an agent question as Slack blocks, including answer buttons when the question is simple enough.

**Data flow**: It receives an ask-user object, creates title and question sections, creates button rows for single-choice questions with few options, and falls back to text instructions for richer answers.

**Call relations**: Terminal reply posting adds these blocks when a turn ends by asking the user something.

*Call graph*: calls 1 internal fn (_mrkdwn_section); called by 1 (post).


##### `slack_connect_blocks`  (lines 1228–1249)

```
def slack_connect_blocks(request: ConnectRequest | None, turn_id: UUID) -> list[dict[str, object]] | None
```

**Purpose**: Renders a private connection request as a Slack button.

**Data flow**: It receives a connect request and turn ID, returns an action block containing a button whose value names the turn, or none if no request exists.

**Call relations**: Terminal reply posting adds this when a turn asks the user to connect an external provider.

*Call graph*: called by 1 (post).


##### `_string_field`  (lines 1252–1256)

```
def _string_field(event: Mapping[str, object], field: str) -> str
```

**Purpose**: Reads a required string field from a Slack payload.

**Data flow**: It receives a mapping and field name, returns the non-empty string value, or raises if it is missing or not text.

**Call relations**: Inbound and click parsers use it for required Slack IDs and timestamps.

*Call graph*: called by 2 (_to_click, _to_inbound).


##### `_inbound_files`  (lines 1259–1271)

```
def _inbound_files(event: Mapping[str, object]) -> tuple[InboundFile, ...]
```

**Purpose**: Extracts downloadable file references from a Slack message event.

**Data flow**: It inspects the event's files list, skips hidden or tombstoned entries, keeps up to the file limit, and returns names plus private download URLs.

**Call relations**: Inbound parsing and declared-file recovery use it before downloads are streamed into the workspace.

*Call graph*: called by 2 (_declared_files, _to_inbound); 1 external calls (__init__).


##### `_declared_files`  (lines 1274–1305)

```
async def _declared_files(bot_token: str, channel: str, ts: str, root_ts: str | None) -> tuple[InboundFile, ...]
```

**Purpose**: Fetches file declarations for a Slack app_mention event when the initial event body may not include them.

**Data flow**: It asks Slack for the exact message in its thread range, finds the matching timestamp, extracts files from that message, and returns them or an empty tuple on failure.

**Call relations**: Inbound parsing calls it for mention events that need a second Slack read to discover attachments.

*Call graph*: calls 2 internal fn (_inbound_files, _slack_ok); called by 1 (_to_inbound); 1 external calls (AsyncClient).


##### `oauth_callback`  (lines 1308–1353)

```
async def oauth_callback(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Completes Slack OAuth installation after the owner approves the app.

**Data flow**: It validates the sealed state and code, exchanges the code for a bot token, binds the Slack team to the workspace, stores the credential and identity record, mirrors the bot user ID, and returns a small HTML success or error page.

**Call relations**: This is the browser callback half of the preferred Slack install path.

*Call graph*: calls 10 internal fn (bind_installation, fulfill_credential_request, open_credential_authorization, _install_page, _is_install_state, _mirror_self_user_id, bot_token_fingerprint, slack_installation_id, slack_oauth_exchange, slack_oauth_redirect_uri); 1 external calls (__init__).


##### `_install_page`  (lines 1356–1363)

```
def _install_page(message: str, status: int) -> Response
```

**Purpose**: Builds a simple HTML page for Slack install success or failure.

**Data flow**: It escapes the message text, embeds it in a small HTML response, sets the status code, and returns it.

**Call relations**: The OAuth callback uses it for every user-visible result.

*Call graph*: called by 1 (oauth_callback); 2 external calls (escape, Response).


##### `_mark_url_verified`  (lines 1369–1383)

```
async def _mark_url_verified(ctx: SurfaceContext, signing_secret: str) -> None
```

**Purpose**: Records that Slack successfully reached this deploy with the current signing secret.

**Data flow**: It fingerprints the signing secret, skips duplicate writes in this process, writes a timestamped marker to the workspace blob store, and logs but ignores write failures.

**Call relations**: Event and interactive routes call it after a verified request so manual app setup can detect a working URL.

*Call graph*: calls 1 internal fn (signing_secret_fingerprint); called by 2 (ingest, interactive); 2 external calls (dumps, time).


##### `ingest`  (lines 1386–1431)

```
async def ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Receives Slack Events API requests and turns relevant messages into ufo turns.

**Data flow**: It reads and verifies the raw body, handles URL verification, loads identity, ignores wrong-team or irrelevant events, parses an inbound message, decides whether to admit immediately or run ambient decision, and returns Slack's required acknowledgment.

**Call relations**: This is the main Slack event route and orchestrates signature checking, inbound conversion, admission, ambient gating, and follower arming.

*Call graph*: calls 12 internal fn (credential, _admit_inbound, _ctx_signing_secret, _decide_ambient_in_background, _folds_into_live_turn, _identity, _mark_url_verified, _prove_identity_in_background, _slack_request_body, _to_inbound (+2 more)); 3 external calls (loads, JSONResponse, Response).


##### `_folds_into_live_turn`  (lines 1434–1473)

```
async def _folds_into_live_turn(ctx: SurfaceContext, bot_token: str, inbound: Inbound) -> bool
```

**Purpose**: Checks whether an unmentioned reply should be absorbed by a turn already running in that Slack thread.

**Data flow**: It finds a live absorbing turn, resolves the speaker and seat permission, logs the skip of ambient gating if the message can fold in, and returns true or false.

**Call relations**: The event route calls it before asking the ambient-reply model, because a running turn should see corrections and interruptions directly.

*Call graph*: calls 4 internal fn (absorbing_turn, transaction, _resolve_member, _slack_user); called by 1 (ingest); 2 external calls (__init__, log).


##### `_admit_inbound`  (lines 1476–1526)

```
async def _admit_inbound(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Performs the work needed to admit a Slack message as a ufo conversation turn.

**Data flow**: It gathers sender info, ambient context, permalink, and mention names; resolves the member and conversation; mirrors the Slack thread; downloads files; fences the member text; admits the turn; anchors DM replies; and starts live followers if a run opened.

**Call relations**: The event route and ambient-decision task call it once they have decided the message should become a turn.

*Call graph*: calls 13 internal fn (admit, conversation_for, retitle_conversation, _ambient_context, _anchor_dm_thread, _arm_followers, _download_files, _mirror_thread, _resolve_member, _slack_permalink (+3 more)); called by 2 (_run_ambient_decision, ingest); 9 external calls (__init__, __init__, __init__, gather, conversation_audience, fence_member_message, mint_marker, render_markup, unescape).


##### `_decide_ambient_in_background`  (lines 1532–1557)

```
def _decide_ambient_in_background(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Starts a background decision for an unaddressed thread reply after Slack has already been acknowledged.

**Data flow**: It uses the inbound message ID as a per-process key, starts a task if one is not already running, and keeps it tracked until completion.

**Call relations**: The event route calls it for ambient messages that are not obviously addressed and do not fold into a live turn.

*Call graph*: calls 1 internal fn (_run_ambient_decision); called by 1 (ingest); 1 external calls (create_task).


##### `_decide_ambient_in_background._untrack`  (lines 1553–1555)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Removes a finished ambient-decision task from the tracking dictionary.

**Data flow**: It receives the finished task and deletes the matching entry for that message key.

**Call relations**: It is attached as the cleanup callback for each ambient decision task.


##### `_run_ambient_decision`  (lines 1560–1575)

```
async def _run_ambient_decision(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Runs the model-backed ambient decision and admits the message if a reply is wanted.

**Data flow**: It asks whether the agent should reply, admits the inbound if yes, and logs any admission failure that happens after Slack was already acknowledged.

**Call relations**: The background ambient task launcher creates this coroutine.

*Call graph*: calls 2 internal fn (_admit_inbound, _ambient_reply_wanted); called by 1 (_decide_ambient_in_background); 1 external calls (log).


##### `_author_is_foreign`  (lines 1578–1585)

```
def _author_is_foreign(event: Mapping[str, object], team_id: str) -> bool
```

**Purpose**: Detects messages authored by a different Slack organization in shared channels.

**Data flow**: It compares source/user team fields to the installed team ID and returns true when they differ.

**Call relations**: Inbound parsing uses it to skip external Slack Connect bystanders before identity or admission work.

*Call graph*: called by 1 (_to_inbound).


##### `_channel_origin`  (lines 1601–1637)

```
async def _channel_origin(ctx: SurfaceContext, payload: Mapping[str, object], event: Mapping[str, object], channel: str, audience_known: bool) -> ChannelOrigin
```

**Purpose**: Determines the privacy audience and friendly label for the Slack place a message came from.

**Data flow**: It uses event channel type, shared-channel flags, and sometimes conversations.info to choose direct, private room, foreign room, or general conversation audience, plus a channel label when available.

**Call relations**: Inbound parsing calls it so the ufo conversation has the right disclosure boundary.

*Call graph*: calls 2 internal fn (credential, _channel_info); called by 1 (_to_inbound); 4 external calls (__init__, conversation_audience, foreign_room_audience, room_audience).


##### `_to_inbound`  (lines 1640–1690)

```
async def _to_inbound(ctx: SurfaceContext, payload: Mapping[str, object], identity: SlackIdentity) -> Inbound | None
```

**Purpose**: Converts a raw Slack event payload into the smaller Inbound object used by admission.

**Data flow**: It filters unsupported events, bots, foreign authors, and irrelevant unaddressed messages; computes DM/thread keys; finds existing conversation participation; resolves origin and files; and returns an Inbound or none.

**Call relations**: The event route calls it after verifying the request and loading identity.

*Call graph*: calls 9 internal fn (credential, _author_is_foreign, _channel_origin, _declared_files, _inbound_files, _participating_conversation, _string_field, slack_message_addressed, slack_thread_key); called by 1 (ingest); 2 external calls (__init__, gather).


##### `_participating_conversation`  (lines 1693–1704)

```
async def _participating_conversation(ctx: SurfaceContext, queue_key: str) -> UUID | None
```

**Purpose**: Checks whether a Slack thread already has an admitted ufo turn.

**Data flow**: It finds a conversation by queue key, verifies it has at least one turn, and returns the conversation ID only then.

**Call relations**: Inbound parsing uses it to decide whether unmentioned thread replies belong to an existing agent conversation.

*Call graph*: calls 2 internal fn (find_conversation, latest_turn); called by 1 (_to_inbound).


##### `_slack_user`  (lines 1707–1739)

```
async def _slack_user(bot_token: str, slack_user_id: str) -> SlackUser | None
```

**Purpose**: Looks up a Slack user's display details and confirmed email.

**Data flow**: It calls Slack users.info, validates the user/profile fields, keeps email only when Slack says it is confirmed, and returns a SlackUser or none on failure.

**Call relations**: Admission, member resolution, conversation search, and name caching use it whenever Slack user IDs need human meaning.

*Call graph*: calls 1 internal fn (_slack_ok); called by 5 (_people, _name, _admit_inbound, _folds_into_live_turn, interactive); 2 external calls (__init__, AsyncClient).


##### `_conversation_members`  (lines 1753–1773)

```
async def _conversation_members(bot_token: str, channel: str) -> tuple[str, ...]
```

**Purpose**: Reads a bounded roster of Slack member IDs for a conversation.

**Data flow**: It calls Slack conversations.members with a limit and returns the string member IDs, or an empty tuple on failure.

**Call relations**: Outbound mention mapping uses it so @name notifications only target people already in the Slack conversation.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (mention_ids); 1 external calls (AsyncClient).


##### `SlackNames.of`  (lines 1794–1802)

```
async def of(self, texts: Sequence[str], users: Sequence[str]=()) -> dict[str, str]
```

**Purpose**: Resolves Slack user and channel IDs mentioned in text into readable names.

**Data flow**: It scans text for mentioned user and channel IDs, adds explicitly supplied user IDs, resolves them through cache or Slack, and returns an ID-to-name map.

**Call relations**: Admission and ambient digest code use it before rendering Slack markup into readable text.

*Call graph*: calls 1 internal fn (_resolved); 2 external calls (mentioned_channels, mentioned_users).


##### `SlackNames.mention_ids`  (lines 1804–1818)

```
async def mention_ids(self, channel: str, identity: SlackIdentity) -> dict[str, str]
```

**Purpose**: Builds the safe map from human @names in an agent reply to Slack mention IDs.

**Data flow**: It reads the conversation roster, drops the bot and external-team users, resolves names, and returns a mention index Slack markup can use.

**Call relations**: Reply delivery uses it before posting agent-authored text that contains @names.

*Call graph*: calls 2 internal fn (_resolved, _conversation_members); 1 external calls (mention_index).


##### `SlackNames._resolved`  (lines 1820–1828)

```
async def _resolved(self, wanted: Mapping[str, str]) -> dict[str, _NamedId]
```

**Purpose**: Resolves a set of Slack IDs using cache first and Slack lookups only for missing entries.

**Data flow**: It reads remembered names, picks a bounded set of missing IDs, fetches them concurrently, stores the fresh names, and returns the combined map.

**Call relations**: Both inbound name rendering and outbound mention mapping rely on this shared cache path.

*Call graph*: calls 3 internal fn (_name, _remember, _remembered); called by 2 (mention_ids, of); 1 external calls (gather).


##### `SlackNames._remembered`  (lines 1830–1851)

```
async def _remembered(self, ids: Sequence[str]) -> dict[str, _NamedId]
```

**Purpose**: Reads fresh cached Slack names from the extension store.

**Data flow**: It asks the scoped store for cache rows, rejects malformed or stale entries, and returns usable named IDs.

**Call relations**: The resolver calls it before making Slack API requests.

*Call graph*: called by 1 (_resolved); 3 external calls (__init__, __init__, now).


##### `SlackNames._name`  (lines 1853–1870)

```
async def _name(self, id_: str, url: str) -> _NamedId | None
```

**Purpose**: Fetches and cleans the display name for one Slack user or channel ID.

**Data flow**: It calls users.info for users or conversations.info for channels, removes forbidden mention delimiters, collapses whitespace, enforces a length limit, and returns the name plus team.

**Call relations**: The resolver calls it for cache misses.

*Call graph*: calls 2 internal fn (_channel_info, _slack_user); called by 1 (_resolved); 1 external calls (__init__).


##### `SlackNames._remember`  (lines 1872–1882)

```
async def _remember(self, names: Mapping[str, _NamedId]) -> None
```

**Purpose**: Writes freshly resolved Slack names into the extension cache.

**Data flow**: It timestamps each name and stores it by ID, logging but ignoring individual write failures.

**Call relations**: The resolver calls it after successful Slack lookups.

*Call graph*: called by 1 (_resolved); 2 external calls (__init__, now).


##### `_slack_permalink`  (lines 1885–1905)

```
async def _slack_permalink(bot_token: str, channel: str, ts: str) -> str | None
```

**Purpose**: Gets Slack's canonical link to a message.

**Data flow**: It calls chat.getPermalink with channel and timestamp, returns the permalink string when Slack provides one, or none on failure.

**Call relations**: Admission and answer-click handling put this source link into the turn context.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_admit_inbound, interactive); 1 external calls (AsyncClient).


##### `_turn_context`  (lines 1908–1925)

```
def _turn_context(sender: SlackUser | None, source: str | None, question: str | None=None) -> TurnContext
```

**Purpose**: Builds the context metadata that accompanies an admitted ufo turn.

**Data flow**: It combines sender name/email, timezone, source permalink, and optional answered question, drops invalid timezones, and returns a TurnContext.

**Call relations**: Message admission and button-answer admission use it when calling core admit.

*Call graph*: called by 2 (_admit_inbound, interactive); 1 external calls (__init__).


##### `_resolve_member`  (lines 1928–1946)

```
async def _resolve_member(ctx: SurfaceContext, slack_user_id: str, is_dm: bool, sender: SlackUser | None) -> UUID | None
```

**Purpose**: Maps a Slack user to a ufo member.

**Data flow**: It first checks an existing Slack-user link. If none exists and Slack provided a confirmed email, it joins or links a member by email; otherwise it returns none, except unresolved DMs fail loudly.

**Call relations**: Admission, live-turn folding, and interactive answers call it before setting the turn speaker.

*Call graph*: calls 2 internal fn (join_member, linked_member); called by 3 (_admit_inbound, _folds_into_live_turn, interactive); 1 external calls (__init__).


##### `_ambient_reply_wanted`  (lines 1949–1974)

```
async def _ambient_reply_wanted(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> bool
```

**Purpose**: Asks whether an unmentioned thread reply should start a new agent turn.

**Data flow**: It fetches recent thread history, admits by default if no trustworthy history exists, otherwise asks core's ambient decision and logs when the answer is no.

**Call relations**: The background ambient-decision task calls it before possibly admitting the inbound message.

*Call graph*: calls 2 internal fn (ambient_reply_wanted, _ambient_history); called by 1 (_run_ambient_decision); 2 external calls (__init__, log).


##### `_ambient_history`  (lines 1977–1999)

```
async def _ambient_history(bot_token: str, inbound: Inbound, identity: SlackIdentity) -> tuple[AmbientMessage, ...]
```

**Purpose**: Builds the recent Slack thread history for the ambient reply decision.

**Data flow**: It fetches the thread tail before the inbound message, converts usable entries into AmbientMessage objects, sorts them, and keeps the newest bounded slice.

**Call relations**: The ambient decision helper calls it to give the model context.

*Call graph*: calls 2 internal fn (_ambient_entry, _thread_tail); called by 1 (_ambient_reply_wanted).


##### `_thread_tail`  (lines 2002–2048)

```
async def _thread_tail(bot_token: str, channel: str, root_ts: str, latest: str) -> tuple[object, ...] | None
```

**Purpose**: Fetches messages before a given timestamp from a Slack thread, walking pages so the tail is trustworthy.

**Data flow**: It calls conversations.replies with a latest bound, follows cursors up to a page cap, returns all fetched items, or returns none if the range exceeds the cap or fails.

**Call relations**: Ambient decision and unseen-context digest code use it to read Slack thread messages.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_ambient_history, _unseen_tail); 1 external calls (AsyncClient).


##### `_ambient_entry`  (lines 2051–2071)

```
def _ambient_entry(item: object, inbound: Inbound, identity: SlackIdentity) -> tuple[float, AmbientMessage] | None
```

**Purpose**: Turns one fetched Slack message into an ambient-history entry when it is relevant.

**Data flow**: It validates user, timestamp, and text, drops other bots, marks whether the speaker is the agent itself, rejects messages at or after the inbound timestamp, and returns sortable time plus AmbientMessage.

**Call relations**: Ambient history calls it for each item returned by the thread-tail fetch.

*Call graph*: called by 1 (_ambient_history); 1 external calls (__init__).


##### `_ambient_context`  (lines 2074–2129)

```
async def _ambient_context(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Creates background context text for a turn from Slack messages that are not already in the ufo transcript.

**Data flow**: It skips DMs, reads unseen dropped replies for existing conversations, otherwise fetches recent channel or thread messages, resolves names, renders a bounded digest, and returns text or an empty string on failure.

**Call relations**: Inbound admission includes this digest inside the fenced member message.

*Call graph*: calls 4 internal fn (_digest_names, _slack_ok, _unseen_tail, ambient_digest); called by 1 (_admit_inbound); 1 external calls (AsyncClient).


##### `_digest_names`  (lines 2132–2141)

```
async def _digest_names(bot_token: str, messages: Sequence[object]) -> dict[str, str]
```

**Purpose**: Resolves author and mention names for messages that will be placed in an ambient digest.

**Data flow**: It collects message text and author IDs from Slack rows and asks SlackNames to resolve them.

**Call relations**: Ambient context and unseen-tail digest helpers use it before rendering readable background lines.

*Call graph*: called by 2 (_ambient_context, _unseen_tail); 1 external calls (__init__).


##### `_unseen_tail`  (lines 2144–2190)

```
async def _unseen_tail(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Finds recent Slack thread replies that were never admitted as turns and summarizes them for the next admitted turn.

**Data flow**: It fetches the thread tail, walks backward until it finds a message already admitted, keeps a bounded unseen slice, resolves names, and returns an ambient digest.

**Call relations**: Ambient context uses it for established Slack conversations.

*Call graph*: calls 4 internal fn (admitted_body, _digest_names, _thread_tail, ambient_digest); called by 1 (_ambient_context).


##### `ambient_digest`  (lines 2193–2261)

```
def ambient_digest(messages: list[object], bot_user_id: str, note: str, marker: str, names: Mapping[str, str]) -> str
```

**Purpose**: Renders Slack messages into a safe, bounded background-context block.

**Data flow**: It filters out bots, the app itself, and messages directly addressing the bot; formats timestamps, names, and cleaned one-line text; keeps the root and newest messages if too long; and wraps them in a marked context element.

**Call relations**: Ambient context and unseen-tail helpers call it after fetching Slack messages and resolving names.

*Call graph*: called by 2 (_ambient_context, _unseen_tail); 3 external calls (fromtimestamp, addressing_mention, render_markup).


##### `_slack_download_host_ok`  (lines 2264–2266)

```
def _slack_download_host_ok(url: str) -> bool
```

**Purpose**: Checks whether a file download URL belongs to Slack before attaching the bot token.

**Data flow**: It parses the URL host and returns true only for slack.com or a Slack subdomain.

**Call relations**: The file download stream calls it before making an authenticated request.

*Call graph*: called by 1 (_stream_download); 1 external calls (urlparse).


##### `_stream_download`  (lines 2269–2287)

```
async def _stream_download(bot_token: str, url: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a private Slack file download into chunks without loading the whole file into memory.

**Data flow**: It verifies the host, sends the bot token to Slack, yields download chunks, tracks total size, and raises if the file exceeds the workspace write limit.

**Call relations**: Inbound file download uses it as the byte stream passed to the workspace writer.

*Call graph*: calls 1 internal fn (_slack_download_host_ok); called by 1 (_download_files); 2 external calls (__init__, AsyncClient).


##### `_download_files`  (lines 2299–2315)

```
async def _download_files(ctx: SurfaceContext, conversation_id: UUID, bot_token: str, files: tuple[InboundFile, ...]) -> DownloadedFiles
```

**Purpose**: Downloads Slack message attachments into the ufo workspace.

**Data flow**: It gives each file a safe inbox name, streams it into the workspace, records delivered names, records over-large skipped files, and returns both lists.

**Call relations**: Inbound admission calls it before creating the final member message body.

*Call graph*: calls 2 internal fn (write_workspace_file, _stream_download); called by 1 (_admit_inbound); 2 external calls (__init__, inbox_name).


##### `files_note`  (lines 2318–2327)

```
def files_note(downloaded: DownloadedFiles) -> str
```

**Purpose**: Creates a short note telling the agent which attached files were saved or skipped.

**Data flow**: It receives delivered and skipped file lists and returns human-readable lines naming saved workspace paths and files too large to download.

**Call relations**: Inbound admission appends this note inside the fenced member message.

*Call graph*: called by 1 (_admit_inbound).


##### `MirroredThread.read`  (lines 2339–2344)

```
def read(cls, row: JsonValue) -> 'MirroredThread'
```

**Purpose**: Reads a stored Slack-thread mirror row into a MirroredThread object.

**Data flow**: It accepts either an older string row or a structured row and returns a normalized model.

**Call relations**: The turn-follow hook uses it after reading the mirror from the extension store.


##### `MirroredThread.anchor`  (lines 2346–2350)

```
def anchor(self) -> str | None
```

**Purpose**: Finds the Slack message timestamp that status and progress should attach to.

**Data flow**: It prefers the root timestamp embedded in a channel queue key, otherwise uses the stored DM message timestamp, otherwise returns none.

**Call relations**: Status tracking and progress posting use it to choose the Slack thread parent.

*Call graph*: called by 1 (_track_status).


##### `_thread_mirror_key`  (lines 2353–2354)

```
def _thread_mirror_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the scoped-store key for a conversation's Slack thread mirror.

**Data flow**: It prefixes the conversation ID with the Slack thread mirror namespace.

**Call relations**: Thread mirroring writes this key, and turn-follow hooks read it.

*Call graph*: called by 2 (_mirror_thread, follow_turn).


##### `_mirror_thread`  (lines 2357–2365)

```
async def _mirror_thread(conversation_id: UUID, thread: MirroredThread) -> None
```

**Purpose**: Stores which Slack thread belongs to a ufo conversation.

**Data flow**: It serializes the MirroredThread and writes it to the extension scoped store under the conversation's mirror key.

**Call relations**: Message admission and answer-click admission call it before the turn runs so follower hooks know where to write status and progress.

*Call graph*: calls 1 internal fn (_thread_mirror_key); called by 2 (_admit_inbound, interactive); 2 external calls (__init__, model_dump).


##### `_dm_anchor_key`  (lines 2368–2374)

```
def _dm_anchor_key(turn_id: UUID, message_ref: UUID | None=None) -> str
```

**Purpose**: Builds the store key that links a DM turn or absorbed message to its Slack message timestamp.

**Data flow**: It combines the turn ID and, when needed, the absorbed message reference under the DM-anchor prefix.

**Call relations**: DM anchoring, reply-thread lookup, and cleanup in attach all use this key shape.

*Call graph*: called by 3 (_anchor_dm_thread, _reply_thread, attach).


##### `_anchor_dm_thread`  (lines 2377–2384)

```
async def _anchor_dm_thread(admitted: Admitted, message_ts: str) -> None
```

**Purpose**: Records which Slack DM message a ufo turn or absorbed arrival should answer under.

**Data flow**: It stores the Slack message timestamp using the turn ID and arrival reference from the admission result.

**Call relations**: Inbound admission and answer-click admission call it for DMs because the DM queue key alone has no root timestamp.

*Call graph*: calls 1 internal fn (_dm_anchor_key); called by 2 (_admit_inbound, interactive); 1 external calls (__init__).


##### `_reply_thread`  (lines 2387–2402)

```
async def _reply_thread(queue_key: str, turn_id: UUID, message_ref: UUID | None=None) -> str | None
```

**Purpose**: Finds the Slack thread timestamp where a reply should be posted.

**Data flow**: It uses the root timestamp from channel queue keys, or reads the matching DM anchor for the turn/message reference, falling back to the turn's founding anchor.

**Call relations**: Terminal replies, mid-turn replies, and attachment upload use it to post under the member message being answered.

*Call graph*: calls 1 internal fn (_dm_anchor_key); called by 3 (attach, post, speak); 1 external calls (__init__).


##### `FollowerContext.workspace_id`  (lines 2413–2413)

```
def workspace_id(self) -> UUID
```

**Purpose**: Defines that a follower context must expose the current workspace ID.

**Data flow**: Implementations provide the UUID; this protocol method itself returns no concrete value.

**Call relations**: Status, progress, and footer code rely on this through either SurfaceContext or the hook adapter.


##### `FollowerContext.public_base_url`  (lines 2416–2416)

```
def public_base_url(self) -> str | None
```

**Purpose**: Defines that a follower context may expose the deploy's public URL.

**Data flow**: Implementations return a URL string or none.

**Call relations**: Footer rendering uses it to build web and debug links.


##### `FollowerContext.credential`  (lines 2418–2418)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Defines how follower code reads credentials such as the Slack bot token.

**Data flow**: Implementations receive a slot name and return the credential string.

**Call relations**: Status, progress, and delivery helpers use this abstraction from both request and hook contexts.


##### `FollowerContext.tail`  (lines 2420–2422)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Defines how follower code reads live frames from a running turn.

**Data flow**: Implementations receive a turn ID and optional cursor and return an async stream of live frames.

**Call relations**: Thread status and progress followers consume this stream to narrate work in Slack.


##### `FollowerContext.turn_is_terminal`  (lines 2424–2424)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Defines how a follower checks whether a turn has already finished.

**Data flow**: Implementations receive a turn ID and return a boolean.

**Call relations**: Progress reporting uses it at checkpoints so it does not post after the final reply.


##### `FollowerContext.conversation_agent`  (lines 2426–2426)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Defines how follower code finds the agent assigned to a conversation.

**Data flow**: Implementations receive a conversation ID and return an agent ID or none.

**Call relations**: Progress footer rendering uses it before building conversation and config links.


##### `FollowerContext.is_operator_workspace`  (lines 2428–2428)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Defines how follower code asks whether the workspace is an internal operator workspace.

**Data flow**: Implementations return a boolean.

**Call relations**: Slack footer rendering uses it to decide whether to include accounting and debug details.

*Call graph*: called by 1 (_slack_footer).


##### `ThreadStatus.thread`  (lines 2485–2486)

```
def thread(self) -> tuple[UUID, str, str]
```

**Purpose**: Returns the unique key for the Slack thread whose status this follower writes.

**Data flow**: It combines workspace ID, channel ID, and thread timestamp into a tuple.

**Call relations**: Status writer tracking uses it to ensure the newest turn is the thread's active status writer.


##### `ThreadStatus.run`  (lines 2488–2507)

```
async def run(self) -> None
```

**Purpose**: Runs the live Slack status follower for one turn.

**Data flow**: It reads the bot token, opens an HTTP client, writes the initial Thinking status, follows live frames, logs cancellation, clears status on errors and completion when appropriate.

**Call relations**: The status task wrapper calls it after _track_status starts a task.

*Call graph*: calls 3 internal fn (_clear, _follow, _set); called by 1 (_run_status); 2 external calls (AsyncClient, log).


##### `ThreadStatus._set`  (lines 2509–2549)

```
async def _set(self, client: httpx.AsyncClient, bot_token: str, status: str) -> bool
```

**Purpose**: Writes one native Slack assistant thread status line.

**Data flow**: It checks whether this turn is still the claimed writer, builds Slack's status body, posts it, logs success or failure, and returns whether Slack accepted the line.

**Call relations**: ThreadStatus.run, _follow, and _clear use it for initial, updated, refreshed, and cleared statuses.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_clear, _follow, run); 3 external calls (post, dumps, log).


##### `ThreadStatus._clear`  (lines 2551–2560)

```
async def _clear(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Clears the Slack status if no sibling turn is still running in the same thread.

**Data flow**: It scans live statuses for another turn on the same thread and, only if none exists, writes an empty status.

**Call relations**: ThreadStatus.run calls it when a followed turn ends or fails.

*Call graph*: calls 1 internal fn (_set); called by 1 (run).


##### `ThreadStatus._follow`  (lines 2562–2620)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str, shown: str) -> None
```

**Purpose**: Turns live turn frames into short Slack status messages.

**Data flow**: It tails frames, wakes on blanking or refresh timeout, maps tool calls, skill loads, absorbed arrivals, and text streaming into short status strings, rate-limits writes, and exits on terminal frames.

**Call relations**: ThreadStatus.run calls it after the initial status write.

*Call graph*: calls 1 internal fn (_set); called by 1 (run); 4 external calls (ensure_future, gather, wait, monotonic).


##### `_restamp_thread_status`  (lines 2630–2637)

```
def _restamp_thread_status(workspace_id: UUID, channel: str, thread_ts: str) -> None
```

**Purpose**: Wakes status followers for a thread so they rewrite the current status after Slack blanks it.

**Data flow**: It scans active status followers and sets their blanked event when workspace, channel, and thread timestamp match.

**Call relations**: Progress posting calls it after a progress message lands in the same Slack thread.

*Call graph*: called by 1 (_post).


##### `_track_status`  (lines 2640–2661)

```
def _track_status(ctx: FollowerContext, turn_id: UUID, thread: MirroredThread) -> None
```

**Purpose**: Starts one status follower task for a turn in this process.

**Data flow**: It skips already-tracked turns, finds the Slack channel and thread anchor, records the new writer claim, stores the ThreadStatus, and starts the async task.

**Call relations**: Follower arming calls it from admission and turn-execution hooks.

*Call graph*: calls 2 internal fn (anchor, _run_status); called by 1 (_arm_followers); 3 external calls (__init__, create_task, log).


##### `_run_status`  (lines 2664–2692)

```
async def _run_status(status: ThreadStatus) -> None
```

**Purpose**: Wraps a status follower task with logging and cleanup.

**Data flow**: It runs the ThreadStatus, logs if the follower dies, removes task and status records, and hands the writer claim back to another running turn on the same thread when needed.

**Call relations**: _track_status starts this coroutine as the task body.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_status); 1 external calls (log).


##### `ProgressCadence.__post_init__`  (lines 2704–2708)

```
def __post_init__(self) -> None
```

**Purpose**: Validates the progress-report timing configuration.

**Data flow**: It checks that the base interval is positive and the cap is not smaller than the base, raising errors for invalid settings.

**Call relations**: Progress tracker construction triggers it when creating a cadence.


##### `ProgressCadence.intervals`  (lines 2710–2721)

```
def intervals(self) -> Iterator[float]
```

**Purpose**: Generates the wait intervals between long-running progress posts.

**Data flow**: It yields the base wait first, then doubles based on elapsed time until reaching the cap, after which it keeps yielding the cap.

**Call relations**: checkpoint calculation uses it to find future report times.

*Call graph*: called by 1 (checkpoints_after).


##### `ProgressCadence.checkpoints_after`  (lines 2723–2732)

```
def checkpoints_after(self, elapsed_seconds: float) -> Iterator[float]
```

**Purpose**: Finds upcoming progress checkpoints for a turn that may already have been running.

**Data flow**: It walks the interval schedule, accumulates elapsed checkpoint times, and yields only checkpoints after the supplied elapsed seconds.

**Call relations**: Thread progress following uses it so resumed reporters continue the original schedule.

*Call graph*: calls 1 internal fn (intervals).


##### `TurnActivity.tool`  (lines 2747–2752)

```
def tool(self, tool: str, description: str) -> None
```

**Purpose**: Records the current tool-related activity in human-readable form.

**Data flow**: It clears streamed text, turns the tool slug into words, prefers the model's description when present, limits the text, and stores it as current activity.

**Call relations**: Thread progress following calls it when live frames report tool or subagent work.


##### `TurnActivity.skill`  (lines 2754–2756)

```
def skill(self, skill: str) -> None
```

**Purpose**: Records that a skill is being loaded.

**Data flow**: It clears streamed text, formats the skill name into a short activity line, and stores it.

**Call relations**: Thread progress following calls it for skill-load frames.


##### `TurnActivity.stream`  (lines 2758–2759)

```
def stream(self, text: str) -> None
```

**Purpose**: Notes that response text is currently streaming.

**Data flow**: It appends the streamed text fragment to the streaming list.

**Call relations**: Thread progress following calls it for text-delta frames so progress can say the response is being prepared.


##### `TurnActivity.current_step`  (lines 2761–2765)

```
def current_step(self) -> str
```

**Purpose**: Returns the most useful current activity to show a waiting member.

**Data flow**: It returns “Preparing the response” if text is streaming; otherwise it returns the last recorded activity.

**Call relations**: TurnActivity.report uses it when building a checkpoint message.

*Call graph*: called by 1 (report).


##### `TurnActivity.report`  (lines 2767–2777)

```
def report(self, elapsed_seconds: float) -> str | None
```

**Purpose**: Builds the text for one progress checkpoint.

**Data flow**: It gets the current step, returns none if there is no signal, formats elapsed time in minutes or hours, and returns a short progress line.

**Call relations**: ThreadProgress._post calls it before sending a progress message to Slack.

*Call graph*: calls 1 internal fn (current_step); called by 1 (_post).


##### `ThreadProgress.run`  (lines 2814–2817)

```
async def run(self) -> None
```

**Purpose**: Runs the long-wait progress reporter for one turn.

**Data flow**: It reads the Slack bot token, opens an HTTP client, and delegates to the frame-following loop.

**Call relations**: The progress task wrapper calls it after _track_progress starts a task.

*Call graph*: calls 1 internal fn (_follow); called by 1 (_run_progress); 1 external calls (AsyncClient).


##### `ThreadProgress._elapsed`  (lines 2819–2822)

```
def _elapsed(self) -> float
```

**Purpose**: Calculates how long the member has been waiting for this turn.

**Data flow**: It subtracts the turn's durable start time from the current UTC time and returns seconds.

**Call relations**: The progress-following loop uses it for checkpoint scheduling and message text.

*Call graph*: called by 1 (_follow); 1 external calls (now).


##### `ThreadProgress._follow`  (lines 2824–2869)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Watches live turn frames and posts progress at scheduled checkpoints.

**Data flow**: It computes the next deadline, tails live frames to update activity and spend, waits until each checkpoint, skips posting if the turn is already terminal, posts progress, then advances to the next checkpoint.

**Call relations**: ThreadProgress.run calls it as the main progress loop.

*Call graph*: calls 2 internal fn (_elapsed, _post); called by 1 (run); 4 external calls (__init__, ensure_future, gather, wait).


##### `ThreadProgress._post`  (lines 2871–2929)

```
async def _post(self, client: httpx.AsyncClient, bot_token: str, activity: TurnActivity, elapsed_seconds: float, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Posts one interim progress message to Slack if there is meaningful activity to report.

**Data flow**: It asks TurnActivity for report text, optionally builds a footer for the first post, sends a Slack message in the destination thread, logs success or failure, and wakes status restamping if needed.

**Call relations**: The progress loop calls it whenever a checkpoint is reached.

*Call graph*: calls 5 internal fn (_footer, report, _restamp_thread_status, _slack_ok, slack_reply_body); called by 1 (_follow); 2 external calls (post, log).


##### `ThreadProgress._footer`  (lines 2931–2952)

```
async def _footer(self, bot_token: str, channel: str, spend: CostTick | None) -> str | None
```

**Purpose**: Builds the footer for the first progress post of a turn.

**Data flow**: It finds the conversation agent, formats current cost and token data if available, and delegates to the common Slack footer builder.

**Call relations**: Progress posting calls it only for the first progress message.

*Call graph*: calls 1 internal fn (_slack_footer); called by 1 (_post).


##### `_track_progress`  (lines 2958–2985)

```
def _track_progress(ctx: FollowerContext, turn_id: UUID, conversation_id: UUID, thread: MirroredThread, started_at: datetime) -> None
```

**Purpose**: Starts one progress reporter task for a turn in this process.

**Data flow**: It skips already-tracked turn IDs, creates a ThreadProgress with the schedule and durable start time, starts the task, and stores it strongly.

**Call relations**: Follower arming calls it only when the caller knows the turn's durable start time, usually from the turn-execution hook.

*Call graph*: calls 1 internal fn (_run_progress); called by 1 (_arm_followers); 4 external calls (__init__, __init__, create_task, now).


##### `_run_progress`  (lines 2988–3003)

```
async def _run_progress(progress: ThreadProgress) -> None
```

**Purpose**: Wraps a progress reporter task with logging and cleanup.

**Data flow**: It runs the reporter, logs if it is abandoned by an unrecoverable error, and removes its task record at the end.

**Call relations**: _track_progress starts this coroutine as the task body.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_progress); 1 external calls (log).


##### `_arm_followers`  (lines 3017–3031)

```
def _arm_followers(ctx: FollowerContext, turn: FollowedTurn, thread: MirroredThread) -> None
```

**Purpose**: Starts all Slack live-feedback followers that a turn should have.

**Data flow**: It always tries to track status, and tracks progress only when the turn start time is available.

**Call relations**: Admission, interactive answer admission, and the turn-execution hook all call it so followers are restored after retries or restarts.

*Call graph*: calls 2 internal fn (_track_progress, _track_status); called by 3 (_admit_inbound, follow_turn, interactive).


##### `_HookFollowerContext.workspace_id`  (lines 3043–3044)

```
def workspace_id(self) -> UUID
```

**Purpose**: Exposes the hook context's workspace ID through the follower interface.

**Data flow**: It returns the workspace ID from the wrapped extension context.

**Call relations**: Followers armed from hooks use this adapter instead of a SurfaceContext.


##### `_HookFollowerContext.public_base_url`  (lines 3047–3048)

```
def public_base_url(self) -> str | None
```

**Purpose**: Exposes the hook context's public base URL through the follower interface.

**Data flow**: It returns the public base URL from the wrapped extension context.

**Call relations**: Footer rendering uses it when followers are armed from a hook.


##### `_HookFollowerContext.credential`  (lines 3050–3051)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Lets hook-armed followers read extension credentials.

**Data flow**: It receives a credential slot name and returns the value from the hook extension credential store.

**Call relations**: Status and progress followers use it to get the Slack bot token.


##### `_HookFollowerContext.tail`  (lines 3053–3056)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Lets hook-armed followers read the live frame stream for a turn.

**Data flow**: It forwards the turn ID and cursor to the extension context's tail method.

**Call relations**: Status and progress loops use it just like they would use a SurfaceContext tail.


##### `_HookFollowerContext.turn_is_terminal`  (lines 3058–3059)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Lets hook-armed progress code check whether a turn has ended.

**Data flow**: It forwards the turn ID to the extension context and returns the boolean result.

**Call relations**: ThreadProgress uses it at checkpoints before posting.


##### `_HookFollowerContext.conversation_agent`  (lines 3061–3062)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Lets hook-armed footer code find the agent for a conversation.

**Data flow**: It forwards the conversation ID to the extension context and returns an agent ID or none.

**Call relations**: Progress footer rendering uses it through the follower abstraction.


##### `_HookFollowerContext.is_operator_workspace`  (lines 3064–3065)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Lets hook-armed footer code know whether internal debug details are allowed.

**Data flow**: It asks the extension context and returns the boolean answer.

**Call relations**: The shared Slack footer builder calls it through the follower interface.


##### `follow_turn`  (lines 3068–3110)

```
async def follow_turn(ctx: HookContext) -> HookOutcome
```

**Purpose**: Hook handler that re-arms Slack status and progress followers from the turn's own execution.

**Data flow**: It ignores missing or subagent turns, reads the stored Slack thread mirror with a short timeout, adapts the hook context to the follower interface, starts followers, and never blocks the turn on follower failure.

**Call relations**: The manifest hook calls it when a user prompt starts executing, which restores live feedback after process restarts.

*Call graph*: calls 2 internal fn (_arm_followers, _thread_mirror_key); 4 external calls (__init__, __init__, timeout, log).


##### `interactive`  (lines 3150–3231)

```
async def interactive(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Receives Slack button-click payloads for ask-user answers and connect handoffs.

**Data flow**: It reads and verifies the signed form body, loads identity, parses the click, answers connect clicks privately, or admits answer clicks as turns, mirrors/anchors the thread, arms followers for a new run, and schedules message rewrites.

**Call relations**: This is the Slack interactivity route and shares admission, identity, permalink, member, and follower helpers with message ingest.

*Call graph*: calls 23 internal fn (admit, admitted_body, connect_url, conversation_for, credential, find_conversation, linked_member, _anchor_dm_thread, _arm_followers, _ctx_signing_secret (+13 more)); 8 external calls (__init__, __init__, gather, conversation_audience, JSONResponse, Response, fence_member_message, mint_marker).


##### `_rewrite_in_background`  (lines 3237–3240)

```
def _rewrite_in_background(bot_token: str, click: AnswerClick) -> None
```

**Purpose**: Starts a background task to rewrite answered question buttons.

**Data flow**: It creates the rewrite task, stores it in a set so it stays alive, and removes it when done.

**Call relations**: The interactive route calls it after confirming the winning answer body was admitted.

*Call graph*: calls 1 internal fn (_run_rewrite); called by 1 (interactive); 1 external calls (create_task).


##### `_run_rewrite`  (lines 3243–3247)

```
async def _run_rewrite(bot_token: str, click: AnswerClick) -> None
```

**Purpose**: Runs the Slack message rewrite and logs failures.

**Data flow**: It calls the button replacement helper and catches any error so the Slack acknowledgment path is not affected.

**Call relations**: The background rewrite launcher uses it as the task body.

*Call graph*: calls 1 internal fn (_replace_buttons_with_answer); called by 1 (_rewrite_in_background).


##### `_ephemeral_in_background`  (lines 3250–3253)

```
def _ephemeral_in_background(ctx: SurfaceContext, click: ConnectClick, text: str) -> None
```

**Purpose**: Starts a background task to send a private ephemeral response for a connect click.

**Data flow**: It creates the ephemeral-post task, stores it in the same background-task set, and removes it when done.

**Call relations**: The interactive route calls it for connect-button clicks.

*Call graph*: calls 1 internal fn (_post_ephemeral); called by 1 (interactive); 1 external calls (create_task).


##### `_post_ephemeral`  (lines 3256–3281)

```
async def _post_ephemeral(ctx: SurfaceContext, click: ConnectClick, text: str) -> None
```

**Purpose**: Sends a private Slack message visible only to the user who clicked a connect button.

**Data flow**: It reads the bot token, posts chat.postEphemeral to the button's channel and thread if available, and logs failures.

**Call relations**: The ephemeral background launcher runs it after interactive click handling chooses the response text.

*Call graph*: calls 2 internal fn (credential, _slack_ok); called by 1 (_ephemeral_in_background); 2 external calls (AsyncClient, dumps).


##### `_to_click`  (lines 3284–3344)

```
def _to_click(raw: bytes, identity: SlackIdentity) -> AnswerClick | ConnectClick | None
```

**Purpose**: Parses a Slack interactive form body into either an answer click or connect click.

**Data flow**: It decodes the form payload JSON, validates block action shape and team, reads user/channel/message/action fields, parses turn IDs or question indexes, computes queue keys, and returns a typed click or none.

**Call relations**: The interactive route calls it after request verification and identity loading.

*Call graph*: calls 3 internal fn (_dict_field, _string_field, slack_thread_key); called by 1 (interactive); 5 external calls (__init__, __init__, loads, parse_qs, UUID).


##### `_dict_field`  (lines 3347–3351)

```
def _dict_field(payload: Mapping[str, object], field: str) -> Mapping[str, object]
```

**Purpose**: Reads a required dictionary field from a Slack payload.

**Data flow**: It returns the field value if it is a dictionary, otherwise raises an error.

**Call relations**: Click parsing uses it for nested user, channel, and message objects.

*Call graph*: called by 1 (_to_click).


##### `_replace_buttons_with_answer`  (lines 3354–3396)

```
async def _replace_buttons_with_answer(bot_token: str, click: AnswerClick) -> None
```

**Purpose**: Updates a Slack question message so the clicked button row becomes the chosen answer.

**Data flow**: It builds a context block showing the selected label and Slack user, copies the delivered blocks, replaces the clicked block by block ID or appends the answer block, and calls chat.update.

**Call relations**: The rewrite background task calls it after answer admission succeeds.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (_run_rewrite); 2 external calls (AsyncClient, dumps).


##### `_reply_text`  (lines 3399–3409)

```
def _reply_text(writeback: Writeback) -> str
```

**Purpose**: Chooses the main text to send for a finished turn.

**Data flow**: It returns a failure notice, cancellation reason or notice, the terminal answer text, or a no-reply placeholder.

**Call relations**: Terminal reply formatting calls it before adding credential hints or oversize file links.

*Call graph*: called by 1 (_reply_with_oversize_links).


##### `_reply_with_oversize_links`  (lines 3412–3441)

```
def _reply_with_oversize_links(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Adds Slack-specific delivery notes to the terminal reply text.

**Data flow**: It starts with the terminal reply text, adds a credentials-page hint when the turn requested a secret, adds links for artifacts too large for Slack upload, and returns the combined Markdown.

**Call relations**: Terminal reply posting uses it before splitting and sending the Slack messages.

*Call graph*: calls 3 internal fn (home_url, _oversize_link_line, _reply_text); called by 1 (post).


##### `_oversize_link_line`  (lines 3444–3447)

```
def _oversize_link_line(ctx: SurfaceContext, artifact: SharedArtifact) -> str
```

**Purpose**: Formats one too-large artifact as a Markdown list item.

**Data flow**: It asks the context for a temporary artifact link, uses the filename as linked or plain text, adds the byte size, and returns the line.

**Call relations**: Oversize reply formatting calls it for each artifact Slack cannot upload inline.

*Call graph*: calls 1 internal fn (artifact_link); called by 1 (_reply_with_oversize_links).


##### `_reply_mention_ids`  (lines 3450–3461)

```
async def _reply_mention_ids(ctx: SurfaceContext, bot_token: str, channel: str, text: str) -> dict[str, str]
```

**Purpose**: Builds the safe Slack mention map for an outgoing reply only when the text contains @ signs.

**Data flow**: It skips roster work if no @ appears, loads identity, returns an empty map if identity is missing, otherwise asks SlackNames for mention IDs in the channel.

**Call relations**: Reply mention mapping calls it before replacing readable @names with Slack notification markup.

*Call graph*: calls 1 internal fn (_identity); called by 1 (_reply_mentions_mapped); 1 external calls (__init__).


##### `_channel_info`  (lines 3464–3478)

```
async def _channel_info(bot_token: str, channel: str) -> Mapping[str, object] | None
```

**Purpose**: Reads Slack metadata for one channel or conversation.

**Data flow**: It calls conversations.info with a short timeout and returns the channel dictionary, or none on failure.

**Call relations**: Audience detection, name resolution, and footer privacy checks use it.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_name, _channel_is_externally_shared, _channel_origin); 1 external calls (AsyncClient).


##### `_channel_is_externally_shared`  (lines 3481–3494)

```
async def _channel_is_externally_shared(bot_token: str, channel: str) -> bool
```

**Purpose**: Checks whether a Slack channel may include people outside the installed workspace.

**Data flow**: It reads channel info and returns true if the info is unavailable or any shared-channel flag is set.

**Call relations**: Footer rendering uses it to avoid exposing operator accounting or debug details in shared rooms.

*Call graph*: calls 1 internal fn (_channel_info); called by 1 (_slack_footer).


##### `_slack_footer`  (lines 3497–3532)

```
async def _slack_footer(ctx: FollowerContext, bot_token: str, channel: str, conversation_id: UUID, agent_id: UUID, turn_id: UUID, accounting: str | None) -> str | None
```

**Purpose**: Builds the small context footer attached to Slack progress and final reply messages.

**Data flow**: It builds web links when a public URL exists, checks whether operator-only details are allowed, optionally adds accounting and debug links, and trims to Slack's context limit.

**Call relations**: Terminal reply posting and first progress posting use it for consistent footer content.

*Call graph*: calls 2 internal fn (is_operator_workspace, _channel_is_externally_shared); called by 2 (_footer, post).


##### `_slack_reply_progress_key`  (lines 3554–3559)

```
def _slack_reply_progress_key(turn_id: UUID, reply_id: UUID | None=None) -> str
```

**Purpose**: Builds the scoped-store key for exactly-once delivery progress of a Slack reply.

**Data flow**: It combines the turn ID and optional mid-turn reply ID under the reply-progress prefix.

**Call relations**: Terminal posting, mid-turn speaking, and cleanup use this key to checkpoint and later remove delivery records.

*Call graph*: called by 3 (attach, post, speak).


##### `_slack_reply_progress`  (lines 3562–3575)

```
async def _slack_reply_progress(store: ScopedStore, key: str) -> tuple[_SlackReplyProgress, JsonValue]
```

**Purpose**: Reads or creates the delivery checkpoint record for a Slack reply.

**Data flow**: It gets the stored record if present, otherwise creates an empty one with compare-and-set, then returns the parsed progress and stored JSON value.

**Call relations**: Terminal and mid-turn reply delivery call it before posting any Slack message parts.

*Call graph*: calls 2 internal fn (get, put_if); called by 2 (post, speak); 2 external calls (__init__, __init__).


##### `_checkpoint_slack_reply`  (lines 3578–3587)

```
async def _checkpoint_slack_reply(store: ScopedStore, key: str, expected: JsonValue, progress: _SlackReplyProgress) -> tuple[_SlackReplyProgress, JsonValue]
```

**Purpose**: Atomically updates a Slack reply delivery checkpoint.

**Data flow**: It serializes the progress object, writes it only if the stored value still matches the expected value, and returns the new progress and encoded value or raises on conflict.

**Call relations**: Delivery, mention mapping, post, and speak use it to make retries safe.

*Call graph*: calls 1 internal fn (put_if); called by 4 (_deliver_slack_reply, _reply_mentions_mapped, post, speak); 2 external calls (__init__, model_dump).


##### `_slack_reply_delivery`  (lines 3590–3605)

```
def _slack_reply_delivery(message: object, delivery_id: str) -> str | None
```

**Purpose**: Detects whether a Slack message is a previously posted reply part with a matching delivery ID.

**Data flow**: It examines Slack message metadata and timestamp, returns the timestamp when the event type and payload ID match, otherwise none.

**Call relations**: Reply reconciliation uses it while scanning Slack history after an uncertain post.

*Call graph*: called by 1 (_reconcile_slack_reply).


##### `_reconcile_slack_reply`  (lines 3608–3648)

```
async def _reconcile_slack_reply(client: httpx.AsyncClient, bot_token: str, channel: str, thread_ts: str | None, delivery_id: str) -> str | None
```

**Purpose**: Looks in Slack for a message that may have been accepted before the HTTP response was lost.

**Data flow**: It scans recent history or thread replies with metadata included, follows pages up to a cap, returns the found timestamp, returns none if not found, or raises if the scan is too large.

**Call relations**: Terminal and mid-turn reply delivery call it when a checkpoint says a delivery was pending.

*Call graph*: calls 2 internal fn (_slack_ok, _slack_reply_delivery); called by 2 (post, speak); 3 external calls (__init__, get, time).


##### `_deliver_slack_reply`  (lines 3651–3691)

```
async def _deliver_slack_reply(client: httpx.AsyncClient, bot_token: str, store: ScopedStore, key: str, progress: _SlackReplyProgress, expected: JsonValue, delivery_id: str, body: bytes) -> tuple[_Sla
```

**Purpose**: Posts one Slack reply part with exactly-once checkpointing.

**Data flow**: It returns an already-delivered result if present, marks the delivery ID pending, posts to Slack, handles recoverable invalid-blocks without recording delivery, records accepted message timestamp, and returns the updated progress plus Slack payload.

**Call relations**: Terminal post and mid-turn speak use it for each message part and fallback attempt.

*Call graph*: calls 3 internal fn (_chat_post, _checkpoint_slack_reply, _posted_message_ts); called by 2 (post, speak); 2 external calls (__init__, model_copy).


##### `_reply_mentions_mapped`  (lines 3694–3724)

```
async def _reply_mentions_mapped(ctx: SurfaceContext, bot_token: str, channel: str, text: str, store: ScopedStore, key: str, progress: _SlackReplyProgress, expected: JsonValue) -> tuple[_SlackReplyPro
```

**Purpose**: Replaces readable @names in outgoing text with Slack mention markup, using a pinned map for retry stability.

**Data flow**: It reuses a stored mention map if present, otherwise resolves and checkpoints the map, applies mention markup to the text, and returns updated progress plus mapped text.

**Call relations**: Terminal and mid-turn reply posting call it before splitting text into Slack-sized parts.

*Call graph*: calls 2 internal fn (_checkpoint_slack_reply, _reply_mention_ids); called by 2 (post, speak); 2 external calls (model_copy, mention_markup).


##### `post`  (lines 3727–3883)

```
async def post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Delivers the terminal reply for a completed turn to Slack and returns the first Slack message reference.

**Data flow**: It finds the destination thread, loads token and delivery progress, reconciles pending posts, maps mentions, builds reply text, actions, and footer, splits the text, posts each part with checkpoints and fallbacks for Slack block rejection, marks delivery complete, and returns channel:timestamp.

**Call relations**: Core writeback delivery calls this as the main final-answer path for Slack.

*Call graph*: calls 15 internal fn (credential, _checkpoint_slack_reply, _deliver_slack_reply, _posted_message_ts, _reconcile_slack_reply, _reply_mentions_mapped, _reply_thread, _reply_with_oversize_links, _slack_footer, _slack_reply_progress (+5 more)); 4 external calls (__init__, __init__, __init__, AsyncClient).


##### `speak`  (lines 3886–3972)

```
async def speak(ctx: SurfaceContext, reply: MidTurnReply) -> str
```

**Purpose**: Delivers a mid-turn reply to Slack before the turn has finished.

**Data flow**: It finds the correct thread for the message reference, loads token and progress, reconciles pending delivery, maps mentions, splits text, posts each part with checkpointing and plain fallback if blocks fail, marks complete, and returns the first message reference.

**Call relations**: Core mid-turn delivery calls it when the agent speaks during a running turn.

*Call graph*: calls 11 internal fn (credential, _checkpoint_slack_reply, _deliver_slack_reply, _posted_message_ts, _reconcile_slack_reply, _reply_mentions_mapped, _reply_thread, _slack_reply_progress, _slack_reply_progress_key, slack_reply_body (+1 more)); 4 external calls (__init__, __init__, __init__, AsyncClient).


##### `_chat_post`  (lines 3975–4015)

```
async def _chat_post(client: httpx.AsyncClient, bot_token: str, body: bytes) -> Mapping[str, object]
```

**Purpose**: Sends one chat.postMessage request while preserving recoverable Slack errors for callers.

**Data flow**: It posts the prepared JSON body, converts HTTP errors into delivery errors with retry-after seconds when available, and otherwise returns the parsed Slack JSON without requiring ok:true.

**Call relations**: The exactly-once delivery helper calls it so it can branch on invalid_blocks before failing.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_deliver_slack_reply); 1 external calls (post).


##### `_posted_message_ts`  (lines 4018–4024)

```
def _posted_message_ts(payload: Mapping[str, object]) -> str
```

**Purpose**: Extracts the timestamp of a successfully posted Slack message.

**Data flow**: It checks ok:true, validates the ts field, returns it, or raises a Slack API error.

**Call relations**: Reply delivery, post, and speak use it after Slack accepts a message.

*Call graph*: called by 3 (_deliver_slack_reply, post, speak); 1 external calls (__init__).


##### `attach`  (lines 4027–4057)

```
async def attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Uploads shareable artifacts from a completed turn into the Slack thread and cleans delivery scratch records.

**Data flow**: It finds the reply thread, deletes reply-progress and DM-anchor records for the turn, filters artifacts that fit Slack's upload limit, streams all uploads concurrently, and logs individual upload failures.

**Call relations**: Core calls it after the terminal reply reference is durably recorded, so cleanup is safe.

*Call graph*: calls 5 internal fn (credential, _dm_anchor_key, _reply_thread, _slack_reply_progress_key, _upload_artifact); 2 external calls (__init__, gather).


##### `_upload_artifact`  (lines 4060–4106)

```
async def _upload_artifact(ctx: SurfaceContext, bot_token: str, channel: str, thread_ts: str | None, artifact: SharedArtifact) -> None
```

**Purpose**: Streams one ufo artifact into Slack using Slack's external upload flow.

**Data flow**: It reserves an upload URL with the exact size, streams blob bytes to that URL, then completes the upload into the channel and optional thread with a title.

**Call relations**: Attachment delivery calls it concurrently for each artifact small enough for Slack.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (attach); 4 external calls (__init__, AsyncClient, Timeout, dumps).


##### `_slack_ok`  (lines 4109–4118)

```
async def _slack_ok(request: Awaitable[httpx.Response]) -> dict[str, object]
```

**Purpose**: Standardizes Slack API calls that must return ok:true.

**Data flow**: It awaits an HTTP response, raises for HTTP failure, parses JSON, raises a SlackApiError if ok is not true, and returns the payload dictionary.

**Call relations**: Most Slack API helpers use it so malformed or rejected Slack responses are handled consistently.

*Call graph*: called by 16 (_list, _members, _post, _set, _ambient_context, _channel_info, _conversation_members, _declared_files, _post_ephemeral, _reconcile_slack_reply (+6 more)); 1 external calls (__init__).


### Command-client event surface
Converts server-side conversation events into tab-separated commands for the `ufo` shell client to display, prompt from, or act on.

### `extensions/ufo/ufo_ext_ufo/surface.py`

`io_transport` · `request handling`

This file is the bridge between the UFO backend and the terminal client a member runs in their shell. The client makes HTTP requests, and the server answers with plain text directive lines such as “say this”, “ask for input”, “run this terminal operation”, or “poll again soon”. Think of it like a teleprompter script: the server writes short stage directions, and the shell client follows them.

The file authenticates requests with bearer tokens, finds or creates the member’s conversation, accepts new messages, and streams back live updates from the agent’s current turn. If the agent takes longer than one request can safely stay open, the stream ends with a “poll” instruction so the client reconnects without losing its place. The file also supports special client actions: sending a second message while a first answer is still running, stopping a turn, retracting a queued message, supplying private secrets, and responding to terminal operations.

A key job here is careful translation. Internal server events like text deltas, tool calls, costs, shared files, credential prompts, and terminal completion states are converted into small terminal-friendly commands. Without this file, the command-line UFO client would have no safe, resumable way to talk to the backend or display the agent’s work.

#### Function details

##### `directive`  (lines 94–102)

```
def directive(verb: str, *fields: str) -> bytes
```

**Purpose**: Builds one line of the simple text protocol that the shell client reads. It protects tabs, newlines, and backslashes inside fields so the client can split the line safely.

**Data flow**: It receives a command word and zero or more text fields. It escapes characters that would break the line format, joins everything with tabs, adds a newline, and returns bytes ready to send over HTTP.

**Call relations**: Most of this file’s rendering helpers call this when they need to speak to the terminal client. It is the last formatting step before messages like replies, prompts, file links, install notices, or run commands leave the server.

*Call graph*: called by 9 (_answer, _fulfill_secret, _say_lines, _send, _subagent_note, channel, directives_for, history_directives, stream_directives).


##### `shared_files`  (lines 116–127)

```
async def shared_files(ctx: SurfaceContext, turn_id: UUID) -> tuple[SharedFile, ...]
```

**Purpose**: Collects the files that a turn shared and prepares them for terminal display. Each file gets a name, size, and, when possible, a download link.

**Data flow**: It receives the surface context and a turn id. It asks the backend for artifacts shared by that turn, asks the context to turn each artifact into a public link, and returns a tuple of `SharedFile` records.

**Call relations**: The streaming path calls this near the end of a turn, because only then is the full set of shared files known. Its results are handed to `_answer`, which turns them into `file` directives for the shell.

*Call graph*: calls 2 internal fn (artifact_link, shared_artifacts); 1 external calls (__init__).


##### `resolve_workspace`  (lines 130–137)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Identifies which workspace an incoming request claims to belong to before the main handler runs. If the request does not carry a usable bearer token, it rejects the request by returning nothing.

**Data flow**: It reads the Authorization header, checks that it is a bearer token, and extracts the workspace claim from that token. The result is either a workspace UUID or `None`.

**Call relations**: This is used by the shared surface routing layer to scope the request early. The main request handler later verifies the same token again for the member email, so workspace and identity both come from the signed token.

*Call graph*: 1 external calls (workspace_claim).


##### `history_directives`  (lines 143–202)

```
def history_directives(conversation: Conversation) -> tuple[bytes, ...]
```

**Purpose**: Turns an existing conversation transcript into terminal lines for a client that is resuming without a cursor. It shows past user messages and completed agent replies without duplicating the newest live turn.

**Data flow**: It receives a conversation transcript. It walks through messages, extracts readable text, summarizes completed work steps, keeps the newest content within a character budget, and returns `you`, `say`, and `note` directive bytes.

**Call relations**: The main `channel` handler uses this only on a fresh resume. It relies on `_history_text` to extract text and `_dispatched` to count real tool-dispatch steps, then formats the result through `directive`.

*Call graph*: calls 3 internal fn (_dispatched, _history_text, directive); called by 1 (channel).


##### `_dispatched`  (lines 205–212)

```
def _dispatched(message: Message, active: set[str]) -> int
```

**Purpose**: Counts how many tool calls in an assistant message actually became active work. It avoids counting tool calls that were written by the model but never dispatched.

**Data flow**: It receives one message and a set of active tool-use ids. If the message is plain text it returns zero; otherwise it counts matching tool-use blocks and returns that number.

**Call relations**: This is a small helper for `history_directives`. It helps replay old history in a way that matches what the live terminal originally showed as work notes.

*Call graph*: called by 1 (history_directives).


##### `_history_text`  (lines 215–220)

```
def _history_text(message: Message) -> str
```

**Purpose**: Extracts the readable text from a stored message. For user messages, it also normalizes the text the same way member messages are shown elsewhere.

**Data flow**: It receives one message. If the content is plain text, it uses that directly; if the content is block-based, it joins the text blocks. For user messages it passes the result through the member-message text cleaner, then returns the final string.

**Call relations**: Only `history_directives` calls this while reconstructing transcript lines for a resumed terminal session.

*Call graph*: called by 1 (history_directives); 1 external calls (member_message_text).


##### `directives_for`  (lines 223–258)

```
def directives_for(frame: LiveFrame, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None, files: tuple[SharedFile, ...]=()) -> tuple[bytes, ...]
```

**Purpose**: Converts one live backend frame into one or more terminal directives. It is the central translator from internal agent events to the tiny protocol the shell understands.

**Data flow**: It receives a live frame plus context such as whether text has already streamed, pending credential prompts, a connection link, and shared files. It pattern-matches the frame type and returns the corresponding directive bytes.

**Call relations**: `stream_directives` calls this for each frame it reads from the live turn. For more detailed formatting it delegates to `_activity`, `_subagent_note`, and `_answer`, while simple frames are formatted directly with `directive`.

*Call graph*: calls 4 internal fn (_activity, _answer, _subagent_note, directive); called by 1 (stream_directives).


##### `_activity`  (lines 261–263)

```
def _activity(frame: ToolCall) -> str
```

**Purpose**: Creates a short human-readable note for a tool call. It explains that a tool is running and includes a description or preview when one exists.

**Data flow**: It receives a tool-call frame. It chooses the most useful detail text, combines it with the tool name, and returns a sentence for display.

**Call relations**: `directives_for` uses this when it sees a tool-call frame. The returned sentence is then wrapped in a `note` directive.

*Call graph*: called by 1 (directives_for).


##### `_subagent_note`  (lines 266–276)

```
def _subagent_note(frame: SubagentActivity) -> tuple[bytes, ...]
```

**Purpose**: Creates terminal notes for work done by a subagent, which is a helper agent running under the main one. It only reports useful activity, not start or finish bookkeeping.

**Data flow**: It receives a subagent activity frame. If the subagent is running a tool or loading a skill, it builds a labeled note and returns it as directive bytes; otherwise it returns no lines.

**Call relations**: `directives_for` calls this when a live frame reports subagent activity. It uses `directive` to produce the terminal-ready `note` line.

*Call graph*: calls 1 internal fn (directive); called by 1 (directives_for).


##### `_answer`  (lines 279–327)

```
def _answer(terminal: Terminal, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None, files: tuple[SharedFile, ...]=()) -> tuple[bytes, ...]
```

**Purpose**: Finishes the terminal display for a turn. It decides what the user should see when the agent is done, failed, cancelled, asking for credentials, or sharing files.

**Data flow**: It receives a terminal frame and extra context such as streamed text, credential prompts, a connection message, and shared files. It builds the right mix of `say`, `file`, `secret`, `ask`, or `exit` directives and returns them.

**Call relations**: `directives_for` calls this for terminal frames, which mark the end state of a turn. It uses `_say_lines` for multi-line text and `directive` for final protocol lines.

*Call graph*: calls 2 internal fn (_say_lines, directive); called by 1 (directives_for).


##### `_say_lines`  (lines 330–331)

```
def _say_lines(text: str) -> tuple[bytes, ...]
```

**Purpose**: Turns a block of text into separate `say` directives, one per line. This keeps multi-line messages readable in the terminal protocol.

**Data flow**: It receives text, splits it into lines, and wraps each line with `directive("say", ...)`. It returns a tuple of byte lines.

**Call relations**: `_answer` uses this when it needs to display final answer text or an error message to the user.

*Call graph*: calls 1 internal fn (directive); called by 1 (_answer).


##### `stream_directives`  (lines 334–471)

```
async def stream_directives(tail: AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]], hold_seconds: float, pending: Callable[[str, str], Awaitable[bool]] | None=None, connect: Callable[
```

**Purpose**: Streams live turn events to the shell client while keeping the HTTP request open only for a safe amount of time. If the turn is still running when the hold time ends, it tells the client where to resume and to poll again.

**Data flow**: It receives a live-frame tail, timing limits, optional callbacks for credentials, connection links, shared files, terminal operations, and cursor state. It reads frames and terminal-operation requests, converts them into directives, yields bytes as they are ready, and finally yields either a completion, a run instruction, or a resume-and-poll instruction.

**Call relations**: The main `channel` handler builds this stream after admitting or resuming a turn. It calls `_next` to safely read frames, `directives_for` to render them, and `directive` for protocol-control lines like `since`, `poll`, and `run`.

*Call graph*: calls 3 internal fn (_next, directive, directives_for); called by 1 (channel); 4 external calls (ensure_future, get_running_loop, wait, suppress).


##### `_next`  (lines 474–480)

```
async def _next(frames: AsyncIterator[tuple[str, LiveFrame]]) -> tuple[str, LiveFrame] | None
```

**Purpose**: Reads the next item from an async frame stream and turns the natural end of the stream into `None`. This makes the surrounding waiting logic simpler.

**Data flow**: It receives an async iterator of live frames. It awaits the next item; if the iterator is exhausted, it returns `None` instead of letting the end-of-stream exception escape.

**Call relations**: `stream_directives` uses this inside an asynchronous task so it can race frame arrival against timeouts and terminal operations.

*Call graph*: called by 1 (stream_directives).


##### `_authenticated_email`  (lines 483–487)

```
def _authenticated_email(request: Request, workspace_id: UUID) -> str | None
```

**Purpose**: Verifies the request’s bearer token and extracts the member email. If the token is missing, malformed, invalid, or for the wrong workspace, it returns nothing.

**Data flow**: It reads the Authorization header and the workspace id. It checks for a bearer token, verifies it against the workspace, and returns the email from the token or `None`.

**Call relations**: Both `channel` and `op_body` call this before doing member-specific work. It is the gate that stops unauthenticated terminal requests from entering conversations or reading operation data.

*Call graph*: called by 2 (channel, op_body); 1 external calls (verify_token).


##### `_utf8_header`  (lines 490–497)

```
def _utf8_header(request: Request, name: str) -> str
```

**Purpose**: Recovers a UTF-8 string from an HTTP header, especially for paths that may contain non-ASCII characters. This matters because HTTP servers often expose header bytes through a Latin-1 decoding step.

**Data flow**: It reads a named header, re-encodes it as Latin-1 bytes, decodes those bytes as UTF-8, and returns the recovered string. Missing headers become an empty string.

**Call relations**: `channel` uses this for the current working directory and terminal-operation error text. That lets the shell send real filesystem paths without corrupting unusual characters.

*Call graph*: called by 1 (channel).


##### `_stale_client`  (lines 500–506)

```
def _stale_client(request: Request) -> bool
```

**Purpose**: Checks whether the shell script version that made the request differs from the version the server wants clients to run. If so, the next normal stream can tell the client to reinstall or update.

**Data flow**: It reads the expected client version from the environment and the client’s version from a request header. It returns true only when the server has a version set and the client’s value does not match.

**Call relations**: `channel` calls this before handling a normal message or resume. When it returns true, `channel.bound` prepends an `install` directive to the outgoing stream.

*Call graph*: called by 1 (channel).


##### `_resumed_from`  (lines 509–516)

```
def _resumed_from(request: Request, turn_id: UUID) -> str
```

**Purpose**: Decides which live-frame cursor, if any, the client may resume from. It only trusts the cursor when it belongs to the same turn currently being tailed.

**Data flow**: It reads the client’s `since` header, separates the named turn from the cursor, compares the turn to the current turn id, and returns either the cursor or an empty string.

**Call relations**: `channel` uses this just before opening the live tail. This prevents a cursor from an older turn from accidentally skipping frames in a newer turn.

*Call graph*: called by 1 (channel).


##### `_turn_context`  (lines 519–531)

```
def _turn_context(email: str, request: Request) -> TurnContext
```

**Purpose**: Builds the context attached to a newly admitted member message. It records who sent the message, where it came from, and, when valid, the member’s timezone.

**Data flow**: It receives the member email and request. It reads the timezone header, tries to create a `TurnContext`, logs and drops the timezone if it is invalid, and returns the context object.

**Call relations**: `channel` and `_send` call this when they admit a message to the backend. The resulting context travels with the turn so the agent can attribute and localize its work.

*Call graph*: called by 2 (_send, channel); 2 external calls (__init__, log).


##### `channel`  (lines 534–653)

```
async def channel(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles the main POST endpoint used by the `ufo` terminal client. It authenticates the member, finds their channel conversation, accepts messages or special actions, and returns either a quick response or a live directive stream.

**Data flow**: It receives the surface context and HTTP request. It verifies the email token, links the member if needed, reads headers and body, then branches for secret fulfillment, send, unsend, terminal operation reply, stop, resume, or new message admission. It returns plain text for short actions or a streaming response for live conversation output.

**Call relations**: This is the hub of the file. It calls helpers for authentication, UTF-8 headers, stale-client checks, history replay, send/unsend/secret paths, turn context, shared files, and live streaming; it also uses the privileged `SurfaceContext` to touch conversations, turns, terminal bindings, credentials, and transcripts.

*Call graph*: calls 21 internal fn (admit, claim_terminal, conversation_for, latest_turn, link_member, linked_member, read_transcript, stop_turn, tail, terminal_resolve (+11 more)); 5 external calls (partial, conversation_audience, PlainTextResponse, body, StreamingResponse).


##### `channel.moved_on`  (lines 621–623)

```
async def moved_on() -> bool
```

**Purpose**: Checks whether the conversation has advanced to a newer non-finished turn while this stream was finishing. This tells the client to reconnect immediately instead of thinking the session is idle.

**Data flow**: It reads the latest turn id from the conversation, compares it with the turn this stream is following, checks whether the newer turn is still active, and returns a boolean.

**Call relations**: `channel` passes this nested callback into `stream_directives`. The stream uses it after a terminal frame so it can emit a cursor and immediate poll when the conversation has already moved on.


##### `channel.bound`  (lines 637–651)

```
async def bound() -> AsyncIterator[bytes]
```

**Purpose**: Wraps the outgoing directive stream with terminal connection setup and cleanup. It also prepends update notices, replayed history, and workspace notes before live lines.

**Data flow**: It receives no direct arguments but closes over request state prepared by `channel`. When iteration starts, it may mark the terminal as connected, yields install/history/workspace lines, forwards live stream bytes, and finally disconnects the terminal if it was connected.

**Call relations**: `channel` returns this async generator inside a `StreamingResponse`. It is the final path through which directives leave the server for the shell client.


##### `_send`  (lines 656–706)

```
async def _send(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, email: str, cwd: str) -> Response
```

**Purpose**: Accepts a message without holding the HTTP request open for streaming. This lets a member send another message while an earlier stream is still running.

**Data flow**: It reads and validates a send id and message body, optionally claims the terminal workspace, admits the message with an idempotency key so retries do not duplicate it, and returns a `sent` acknowledgement plus any workspace note.

**Call relations**: `channel` calls this when the request has the send header. The live consequences of the send are not returned here; they appear on the separate held stream that is already tailing the conversation.

*Call graph*: calls 4 internal fn (admit, claim_terminal, _turn_context, directive); called by 1 (channel); 3 external calls (PlainTextResponse, body, UUID).


##### `_unsend`  (lines 709–731)

```
async def _unsend(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, unsend: str) -> Response
```

**Purpose**: Retracts a queued message that the agent has not yet taken up. It is the server side of taking back a pending line from the terminal.

**Data flow**: It confirms the request body is empty, checks that there is a real member, parses the arrival id, asks the context to retract that pending arrival, and returns success or a conflict message.

**Call relations**: `channel` calls this when the unsend header is present. It does not admit a new turn; it only asks the backend to remove a still-pending arrival owned by the member.

*Call graph*: calls 1 internal fn (retract_arrival); called by 1 (channel); 3 external calls (PlainTextResponse, body, UUID).


##### `_fulfill_secret`  (lines 734–753)

```
async def _fulfill_secret(ctx: SurfaceContext, request: Request, member_id: UUID | None, sealed: str) -> Response
```

**Purpose**: Stores a credential value that the user typed privately in response to a prompt. The secret is not treated as a chat message and does not enter the transcript.

**Data flow**: It reads the credential slot header and request body, validates that the value is present and not too large, asks the context to fulfill the sealed credential request, and returns a `say` directive explaining whether it was stored.

**Call relations**: `channel` calls this before normal conversation handling when a secret header is present. It relies on the surface context to verify the sealed request and store the value securely.

*Call graph*: calls 2 internal fn (fulfill_credential_request, directive); called by 1 (channel); 2 external calls (PlainTextResponse, body).


##### `op_body`  (lines 756–768)

```
async def op_body(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the raw bytes for a pending terminal operation to the client. This is used when the terminal needs to fetch operation input, such as data to write to a temporary file.

**Data flow**: It authenticates the request, finds the linked member, builds the conversation queue key from email and channel, asks the context for the operation body, and returns either the bytes or an error response.

**Call relations**: This handles the GET route for operation bodies. It shares authentication logic with `channel` but does not create messages, admit turns, or stream directives.

*Call graph*: calls 3 internal fn (linked_member, terminal_op_body, _authenticated_email); 2 external calls (PlainTextResponse, Response).
