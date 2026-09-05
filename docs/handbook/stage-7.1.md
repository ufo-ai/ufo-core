# Audience, visibility, and participation decisions  `stage-7.1`

This stage is shared behind-the-scenes support that runs around each conversation turn. Its job is to answer a simple but important question: “Who is allowed to see or take part in this?” It keeps replies aimed at the right people and helps stop private workspace or member information from appearing in the wrong room.

The audience code gives each turn a clear audience name, such as one member, a whole workspace, a room, or a room shared outside the workspace. It can compare and translate these names so the rest of the system knows what is safe to read or show. The subjects code supplies smaller labels for visibility, separating messages visible to everyone from messages tied to one member. The ambient reply code is a gatekeeper for busy group threads. If people are chatting without directly calling on the agent, it decides whether the agent should stay quiet instead of starting a costly full response. The web audience code applies similar access rules in the web portal, including admin tools for granting access and auditing private transcript views.

## Files in this stage

### Core turn audience decisions
Runtime logic decides when an agent should participate and names or checks the audience and visibility scope for each turn.

### `core/src/ufo/runtime/turns/ambient_reply.py`

`domain_logic` · `request handling, before starting a new ambient turn`

In a busy chat thread, not every new message is meant for the agent. Once the agent has participated, later replies can look relevant even when they are just one human asking another human for an opinion. This file is the small gatekeeper that checks those “ambient” messages before they become full agent turns.

The main idea is simple: ask a cheap model one narrow question, and require a one-word answer: REPLY or NO_REPLY. If the answer is REPLY, the larger system may start a new turn. If it is NO_REPLY, the agent stays quiet. This is like having a receptionist quickly decide whether a call is actually for you before interrupting your work.

The file defines AmbientMessage, a small record containing who spoke, what they said, and whether it was the agent’s own earlier message. It also defines AmbientReplyClassifier, which builds a compact view of the recent thread and sends it to the model with strict rules. The history is deliberately limited: only the most recent messages are included, and each message is shortened to a safe size. But the new message itself must fit without being cut; if it is too long, the classifier refuses to guess.

A key safety detail is that chat text is wrapped as JSON between fence lines, so user-written instructions inside the chat are treated as quoted data, not as instructions to the model. If the model fails or gives an unreadable answer, this file raises an error rather than silently suppressing the agent.

#### Function details

##### `MeteredModel.model`  (lines 95–95)

```
def model(self) -> str
```

**Purpose**: This describes the model name that will be billed and used for the ambient-reply check. It exists as part of a small interface so this file can depend on “something that can call a model” without importing the larger model-access system directly.

**Data flow**: Nothing is passed in except the model object itself. Reading this property gives back the model identifier string that will be placed into the request sent to the model provider.

**Call relations**: When AmbientReplyClassifier.decide prepares its one-question model request, it reads this property so the request is sent to the intended classifier model.


##### `MeteredModel.complete`  (lines 97–97)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: This is the promised method for making one model call and getting the model’s text answer back. In this file, that answer is expected to contain the decision word REPLY or NO_REPLY.

**Data flow**: A ModelRequest goes in, containing the system instructions, the thread payload, token limits, and other settings. The model service processes it and returns plain text, which the classifier later reads for the final decision word.

**Call relations**: AmbientReplyClassifier.decide calls this after building the payload. The actual implementation lives elsewhere; this file only states the shape of the object it needs.


##### `_entry`  (lines 100–105)

```
def _entry(message: AmbientMessage) -> dict[str, object]
```

**Purpose**: This turns one AmbientMessage into the simple dictionary shape sent to the model. It also trims the message text to the configured character limit so the history stays small and predictable.

**Data flow**: An AmbientMessage goes in, carrying speaker, own, and text. A dictionary comes out with the same speaker and own flag, plus text cut down to the allowed size.

**Call relations**: AmbientReplyClassifier._payload uses this helper for every recent history message and for the new message. It keeps the payload-building code simple and makes sure all messages have the same shape.

*Call graph*: called by 1 (_payload).


##### `AmbientReplyClassifier.decide`  (lines 118–136)

```
async def decide(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> AmbientDecision
```

**Purpose**: This is the main decision point: it asks whether the agent should answer one new ambient message. It uses a small model call instead of allowing every such message to become a full agent turn.

**Data flow**: The new AmbientMessage and a tuple of recent AmbientMessages go in. The function first refuses overly long new messages, because it does not want to decide from a cut-off version of the message. It then builds a fenced JSON payload, sends a ModelRequest to the configured model, searches the model’s answer for REPLY or NO_REPLY, and returns the last decision word it finds. If the message is too long or the answer cannot be read, it raises an error instead of choosing silence.

**Call relations**: This is the method the surrounding turn-admission code calls before founding a new ambient turn. It relies on AmbientReplyClassifier._payload to package the thread, uses Message and ModelRequest to form the model call, and then hands back only the compact decision that the caller needs.

*Call graph*: calls 1 internal fn (_payload); 2 external calls (__init__, __init__).


##### `AmbientReplyClassifier._payload`  (lines 138–154)

```
def _payload(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> str
```

**Purpose**: This prepares the recent thread and the new message in a format the model can judge safely. It wraps the chat data as JSON between matching fence lines so the model can tell the difference between the system’s instructions and the users’ chat text.

**Data flow**: The new message and recent history go in. The function keeps only the last configured number of history messages, converts each message with _entry, serializes the result to compact JSON, chooses a fence marker that does not appear inside the payload, and returns one string containing fence, JSON, and fence again.

**Call relations**: AmbientReplyClassifier.decide calls this just before making the model request. Inside, it calls _entry to normalize each message and json.dumps to turn the thread object into a JSON string.

*Call graph*: calls 1 internal fn (_entry); called by 1 (decide); 1 external calls (dumps).


### `core/src/ufo/runtime/turns/audience.py`

`domain_logic` · `request handling and conversation turn processing`

A conversation can have different disclosure scopes: shared with the workspace, private to one member, tied to a room, or tied to a room that includes an outside organization. This file gives those scopes exact string names and enforces their shape. Think of an audience string like a label on a folder: if the label is malformed, or if someone tries to swap it for a broader label, the code rejects it.

The main type is `Audience`, a distinct name for a string so the rest of the code can signal, “this string is an audience label.” The file provides builders for known audience labels, such as the shared workspace audience, a member-specific audience, a normal room audience, and a foreign room audience. It also provides `parse_audience`, which acts like a gatekeeper: it accepts only labels that match the allowed patterns and raises an error for anything ambiguous or unsafe.

Two helper ideas are especially important. `audience_subjects` says what stored subjects a conversation is allowed to read from. Foreign rooms are deliberately restricted to themselves, so internal workspace-shared facts are not recalled into an external channel. `narrow_audience` decides whether a requested audience change is safe, allowing movement to an equal or narrower scope but rejecting changes that would cross unrelated audiences.

#### Function details

##### `conversation_audience`  (lines 14–15)

```
def conversation_audience(member_id: UUID | None) -> Audience
```

**Purpose**: Creates the audience label for a normal conversation. If there is no member ID, the label means the shared workspace audience; if there is a member ID, the label means that one member’s private audience.

**Data flow**: It receives either a member UUID or `None`. With `None`, it returns the shared audience constant. With a UUID, it builds a string using the member prefix plus that UUID, then wraps it as an `Audience`.

**Call relations**: Other functions use this as the single trusted way to form member audience labels. `parse_audience` uses it to confirm that a text value is exactly the canonical member form, and `readable_audiences` uses it when listing what a member is allowed to read.

*Call graph*: called by 2 (parse_audience, readable_audiences).


##### `room_audience`  (lines 18–19)

```
def room_audience(surface: str, room: str) -> Audience
```

**Purpose**: Creates the audience label for a normal room on a given surface, such as a chat surface and room name. This marks a conversation as belonging to that internal room context.

**Data flow**: It receives a surface name and room name. It passes them, along with the normal room prefix, to the shared room-building helper and returns the resulting `Audience`.

**Call relations**: This is the public wrapper for building normal room audiences. It delegates the validation and formatting work to `_room_audience`, and `parse_audience` calls it to check that an existing room audience string is in the expected canonical form.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `foreign_room_audience`  (lines 22–23)

```
def foreign_room_audience(surface: str, room: str) -> Audience
```

**Purpose**: Creates the audience label for a room that is externally shared with another organization. This distinction matters because foreign rooms must not automatically read internal shared workspace facts.

**Data flow**: It receives a surface name and room name. It sends them, together with the foreign-room prefix, to `_room_audience`, which validates the pieces and builds the final label.

**Call relations**: This mirrors `room_audience` but for external rooms. `parse_audience` uses it to verify foreign room labels, while `_room_audience` supplies the common formatting and safety checks.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `_room_audience`  (lines 26–29)

```
def _room_audience(prefix: str, surface: str, room: str) -> Audience
```

**Purpose**: Builds a room-style audience label after checking that its parts are safe to join with colons. It prevents unclear labels by rejecting empty values and values that already contain a colon.

**Data flow**: It receives a prefix, a surface, and a room. It checks that the surface and room are nonempty and contain no colon; if the check fails, it raises `ValueError`. If the pieces are safe, it returns an `Audience` string shaped like `prefix + surface + ':' + room`.

**Call relations**: This is the shared helper behind both `room_audience` and `foreign_room_audience`. Those functions choose the meaning of the label, while this helper enforces the common label format.

*Call graph*: called by 2 (foreign_room_audience, room_audience).


##### `parse_audience`  (lines 32–55)

```
def parse_audience(value: str) -> Audience
```

**Purpose**: Checks that a raw string is a valid audience label and returns it as an `Audience`. It is the file’s main guardrail against malformed or misleading audience strings.

**Data flow**: It receives a string value. It first accepts the exact shared audience label. Otherwise, it splits the string around colons to identify whether it is a member, room, or foreign-room label. For member labels, it verifies the UUID and compares the result to `conversation_audience`; for room labels, it rebuilds the expected value using `room_audience` or `foreign_room_audience`. If anything does not match the allowed forms, it raises `ValueError`; otherwise, it returns the audience.

**Call relations**: This function is called before interpreting or comparing audience labels in `audience_member`, `audience_subjects`, and `narrow_audience`. It hands off to the audience-building functions so validation and construction use the same rules.

*Call graph*: calls 3 internal fn (conversation_audience, foreign_room_audience, room_audience); called by 3 (audience_member, audience_subjects, narrow_audience); 1 external calls (UUID).


##### `readable_audiences`  (lines 58–63)

```
def readable_audiences(member_id: UUID) -> tuple[Audience, ...]
```

**Purpose**: Returns the conversation audiences a member is allowed to read when looking at member-facing data: the shared workspace audience and their own private audience. It deliberately does not include room audiences because the workspace does not know room membership here.

**Data flow**: It receives a member UUID. It combines the shared audience constant with the member-specific audience made by `conversation_audience`, and returns both as a tuple.

**Call relations**: This function relies on `conversation_audience` to build the member’s own label. It is meant for read paths that need a simple, consistent answer to “which audience buckets can this member see?”

*Call graph*: calls 1 internal fn (conversation_audience).


##### `audience_member`  (lines 66–70)

```
def audience_member(audience: Audience) -> UUID | None
```

**Purpose**: Extracts the member ID from a member-specific audience label, if the label is for a member. For shared, room, and foreign-room audiences, it returns `None`.

**Data flow**: It receives an `Audience`. It first validates it with `parse_audience`. If the parsed label does not start with the member prefix, it returns `None`. If it does, it removes the prefix, turns the remaining text into a UUID, and returns that UUID.

**Call relations**: This function depends on `parse_audience` so it only tries to read member IDs from valid audience labels. It uses the standard UUID parser when turning the label text back into an ID.

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (UUID).


##### `audience_subjects`  (lines 73–80)

```
def audience_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: Tells the rest of the system which stored subjects a conversation with this audience may read. This is an access boundary: foreign rooms read only their own subject, while other audiences can also read the shared workspace subject.

**Data flow**: It receives an `Audience` and validates it with `parse_audience`. If the audience is foreign, it returns a frozen set containing only that audience label. Otherwise, it returns a frozen set containing the shared subject and the audience’s own subject.

**Call relations**: This function uses `parse_audience` as its safety check before making access decisions. Its output is likely used by conversation recall or lookup code to decide which stored facts are visible in a turn.

*Call graph*: calls 1 internal fn (parse_audience).


##### `narrow_audience`  (lines 83–98)

```
def narrow_audience(current: Audience, requested: Audience) -> Audience
```

**Purpose**: Decides whether a requested audience can safely replace the current audience without widening disclosure in an unsafe way. It allows staying the same, narrowing from shared to a more specific audience, and carefully resolving normal-vs-foreign room versions of the same room.

**Data flow**: It receives the current audience and the requested audience. It validates both with `parse_audience`. If the request is the same or asks for shared while already in a more specific audience, it keeps the current audience. If the current audience is shared, it accepts the requested audience. If both are room-like labels for the same surface and room, it chooses the safer foreign label when one side is foreign. For unrelated changes, it raises `ValueError`.

**Call relations**: This function is called when the system needs to reconcile an existing conversation audience with a newly requested one. It relies on `parse_audience` to make sure both labels are valid before comparing their parts.

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (partition).


### `core/src/ufo/runtime/turns/subjects.py`

`data_model` · `cross-cutting`

This file is a small naming helper for conversation visibility. In this system, a “subject” is a plain text label that represents an audience: either the whole shared workspace or one particular member. The file sets the shared label to the fixed word "shared", and it sets member labels to start with "member:" followed by that member’s unique ID. This is like putting either a public notice on a bulletin board, or putting a note into one person’s named mailbox.

The main reason this file exists is consistency. If different parts of the system invented their own strings for “shared” or “member-specific,” visibility checks could silently disagree, and private or shared content might be shown to the wrong audience. By keeping the labels and the small helper functions in one place, source code that creates visibility subjects and source code that checks them can speak the same language.

There is one helper to build a member-specific subject from a UUID, which is a standard unique identifier. There is another helper to test whether a subject is the special shared one. The comments make an important distinction: only the literal shared subject counts as readable by every workspace member. Other audience-like places, such as a room or externally shared channel, are not treated as shared here because they do not represent a workspace membership fact.

#### Function details

##### `member_subject`  (lines 9–10)

```
def member_subject(member_id: UUID) -> str
```

**Purpose**: This function turns a member’s unique ID into the standard subject label for content meant for that member. Code uses it so every member-specific label has the same shape.

**Data flow**: It receives a UUID, which is a unique identifier for a workspace member. It places the text prefix "member:" in front of that ID. It returns the finished subject string, such as a named mailbox label for that one member.

**Call relations**: When another part of the system needs to mark content as belonging to or readable by a particular member, it should call this helper instead of hand-writing the label. The function does not call other project code; it simply formats the shared convention into a string.


##### `subject_shared`  (lines 13–18)

```
def subject_shared(subject: str) -> bool
```

**Purpose**: This function answers the question: “Is this subject the one that means everyone in the workspace can read it?” It is used to separate truly shared content from member-specific or other audience labels.

**Data flow**: It receives a subject string. It compares that string with the single official shared label, "shared". It returns true if they match exactly, and false otherwise; it does not change anything else.

**Call relations**: When visibility code needs to know whether content is broadly readable by workspace members, it can call this function for the decision. The function relies only on the constant defined in this file and does not hand work off elsewhere.


### Web access controls
Web portal logic determines member-agent visibility and provides administrative controls for granting access and auditing transcript access.

### `extensions/web/ufo_ext_web/audience.py`

`domain_logic` · `request handling and admin access changes`

The web portal needs a clear answer to a sensitive question: “Is this person allowed to reach this agent or conversation?” This file is that rulebook. It treats a member’s email address as the web identity, then stores explicit access grants as small records keyed by agent and email. Without this file, private agents could either disappear from people who should see them, or worse, become visible to people who should not.

The main idea is simple. Workspace-visible agents are available to normal seated members. Private agents are available when the member has an explicit grant, owns the agent, or has a private extension conversation with that agent. Workspace admins can reach every agent, but the file still keeps a separate “member audience” so admin power is not accidentally used as normal discovery.

The file also exposes three tools. Admins can grant web access to a member, revoke that access, or acknowledge and record that they opened another member’s private transcript. These actions return human-readable results and refuse unsafe requests, such as a non-admin trying to grant access or a transcript acknowledgement coming from a channel shared with another organization. Think of it like the front desk access list for a building: this file writes the list, reads the list, and checks people against it at the door.

#### Function details

##### `web_extension`  (lines 38–45)

```
def web_extension() -> ExtensionContext
```

**Purpose**: Creates the web extension’s own context, which is the safe handle used to read and write the web audience records. Code that starts from a web surface context uses this to reach the extension’s private store.

**Data flow**: It takes no input. It builds a scoped store for the web extension and an empty credential-access description, then returns an extension context containing both. Nothing is written yet; it only prepares the doorway to the web extension’s data.

**Call relations**: Surface code uses this kind of context when it needs to consult the web audience store. Inside, it constructs the store, credential access object, and extension context that later functions use for transactions and grant records.

*Call graph*: 3 external calls (__init__, __init__, __init__).


##### `_grant_key`  (lines 48–49)

```
def _grant_key(agent_id: UUID, email: str) -> str
```

**Purpose**: Builds the storage key for one web access grant. The key says, in a consistent format, “this email may reach this agent.”

**Data flow**: It receives an agent ID and an email address. It trims spaces from the email, lowercases it so case differences do not create duplicate identities, and combines it with the audience prefix and agent ID. The result is a single string used as the row name in storage.

**Call relations**: _grant uses this when saving a new access grant, and _revoke uses the same key shape when deleting one. This shared helper keeps grant and revoke pointed at the exact same storage location.

*Call graph*: called by 2 (_grant, _revoke).


##### `granted_emails`  (lines 52–59)

```
async def granted_emails(store: ScopedStore) -> dict[UUID, tuple[str, ...]]
```

**Purpose**: Reads all stored web access grants and groups them by agent. This is useful for an administration view that needs to show who has been granted access to each agent.

**Data flow**: It receives the web extension’s scoped store. It lists every stored item whose key starts with the audience prefix, pulls the agent ID and email out of each key, groups emails under their agent, sorts each email list, and returns a dictionary from agent ID to email tuple.

**Call relations**: This function reads the same grant rows that _grant writes and _revoke deletes. It relies on the store’s list operation and turns the raw key strings back into UUID agent IDs for callers that need a clean admin-facing summary.

*Call graph*: calls 1 internal fn (list); 1 external calls (UUID).


##### `_granted_agent_ids`  (lines 62–69)

```
async def _granted_agent_ids(store: ScopedStore, email: str) -> frozenset[UUID]
```

**Purpose**: Finds the private agents explicitly granted to one email address. It is part of deciding what a particular web user is allowed to see.

**Data flow**: It receives the scoped store and an email address. It normalizes the email, scans all audience grant records, keeps only the records whose email matches, converts their agent IDs back from text into UUIDs, and returns them as an immutable set.

**Call relations**: web_audience calls this while building one member’s portal view. It supplies the explicit-grant part of the larger access decision, alongside workspace visibility, ownership, and private conversation access.

*Call graph*: calls 1 internal fn (list); called by 1 (web_audience); 1 external calls (UUID).


##### `WebAudience.allows`  (lines 86–87)

```
def allows(self, agent_id: UUID) -> bool
```

**Purpose**: Answers whether this web audience may directly access a specific agent. It is a quick yes-or-no check used before opening or resolving agent views.

**Data flow**: It receives an agent ID. It compares that ID with the IDs in the audience’s direct agent list and returns true if any match, otherwise false. It does not change the audience.

**Call relations**: Web surface routing code calls this when resolving existing chats, new chats, or chat targets. It gives those routes a simple door-check instead of making them repeat the full audience-building rules.

*Call graph*: called by 3 (_existing_chat_target, _new_chat_target, _resolve_chat).


##### `WebAudience.allows_chat`  (lines 89–90)

```
def allows_chat(self, agent_id: UUID) -> bool
```

**Purpose**: Answers whether this audience may chat with a specific agent, including agents available through private extension conversations. This is slightly wider than direct portal access.

**Data flow**: It receives an agent ID. It checks that ID against the combined chat-agent list, which includes normal allowed agents plus conversation-only agents, and returns true or false. It does not write anything.

**Call relations**: It uses WebAudience.chat_agents to include both regular and conversation-based access. It is intended for chat checks where a member-private conversation can open access to that agent’s chat even if the agent is not part of the normal listed audience.


##### `WebAudience.chat_agents`  (lines 93–94)

```
def chat_agents(self) -> tuple[AgentSummary, ...]
```

**Purpose**: Provides the full set of agents this audience may chat with. It combines ordinary visible agents with agents made available by private extension conversations.

**Data flow**: It reads the WebAudience object’s agents tuple and conversation_agents tuple. It returns a new tuple containing both, leaving the original stored tuples unchanged.

**Call relations**: WebAudience.allows_chat depends on this property so chat checks use the broader chat list. The property keeps that combination rule in one place.


##### `web_audience`  (lines 97–127)

```
async def web_audience(surface: SurfaceContext, extension: ExtensionContext, email: str) -> WebAudience
```

**Purpose**: Builds the complete web-portal audience for one email address in one workspace. This is the central function that decides which agents that person can see and chat with.

**Data flow**: It receives a surface context, an extension context, and an email. It normalizes the email, reads the workspace seat list in a transaction, finds the matching seated member, lists the agents, reads explicit grants, and asks which agents have member-private extension conversations. If the email is not a seated member, it returns an empty audience. Otherwise it returns a WebAudience showing admin status, direct agents, member-level agents, and conversation-only agents.

**Call relations**: This function pulls together lower-level pieces: it uses the extension transaction to read seats, the surface context to list agents and member extension conversations, and _granted_agent_ids to include explicit grants. The result is the object later used by web routes such as chat resolution to decide what to allow.

*Call graph*: calls 4 internal fn (transaction, list_agents, member_extension_agent_ids, _granted_agent_ids); 2 external calls (__init__, __init__).


##### `_refusal`  (lines 137–138)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: Creates a standard error result with a plain message for tool actions that must say no. It keeps refusals consistent and readable.

**Data flow**: It receives a text message. It wraps that message in text content, marks the tool result as an error, and returns it. It does not inspect or change any external data.

**Call relations**: _gate and _read_private_transcript call this whenever a request fails a safety check, such as a non-admin attempting an admin-only action. It hands back a result the tool system can show directly to the user.

*Call graph*: called by 2 (_gate, _read_private_transcript); 2 external calls (__init__, __init__).


##### `_target_agent`  (lines 141–148)

```
def _target_agent(ctx: ToolContext) -> tuple[UUID, str]
```

**Purpose**: Figures out which agent an access action is about, and chooses a friendly label for the reply. If the action did not name another agent, it uses the current turn’s agent.

**Data flow**: It receives the tool context. If there is no target object at all, it raises an internal error because the tool was dispatched incorrectly. If no separate agent was named, it returns the current agent ID and the label “this agent.” If an agent was named, it returns that agent’s ID and name.

**Call relations**: _grant, _revoke, and _read_private_transcript call this after their basic checks. It gives them the exact agent ID needed for storage or transcript recording, plus the human-facing name used in the success message.

*Call graph*: called by 3 (_grant, _read_private_transcript, _revoke).


##### `_gate`  (lines 151–166)

```
async def _gate(ctx: ToolContext, extension: ExtensionContext) -> ToolResult | SeatEntry
```

**Purpose**: Performs the shared permission checks for granting or revoking web access. It makes sure the speaker is a workspace admin and that the targeted member really exists.

**Data flow**: It receives the tool context and extension context. It checks that there is a speaking member, asks whether that speaker is an admin, reads the workspace seat list, parses the target member ID, and looks up that member. On failure it returns an error ToolResult; on success it returns the member’s seat entry.

**Call relations**: _grant and _revoke both call this before touching access records. It centralizes the “only admins can change another member’s portal access” rule so the two actions cannot drift apart.

*Call graph*: calls 3 internal fn (transaction, speaker_is_admin, _refusal); called by 2 (_grant, _revoke); 2 external calls (__init__, UUID).


##### `_grant`  (lines 169–186)

```
async def _grant(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the admin tool that gives a workspace member web access to an agent. It writes the grant record and returns a clear confirmation.

**Data flow**: It receives the tool context and an empty validated input object. It requires an extension context, runs _gate to confirm the speaker may act and to find the target member, identifies the target agent, and special-cases the main agent because every member can already reach it. For normal grants, it stores a row keyed by agent and the member’s normalized email, including who granted it, then returns a success message.

**Call relations**: This function is registered as the handler for the grant_web_access tool. It depends on _gate for admin and member checks, _target_agent for choosing the agent, and _grant_key for writing the storage row that web_audience later reads.

*Call graph*: calls 4 internal fn (agent_is_main, _gate, _grant_key, _target_agent); 2 external calls (__init__, __init__).


##### `_revoke`  (lines 189–209)

```
async def _revoke(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the admin tool that removes a member’s explicit web access grant for an agent. It deletes the stored grant and explains the result.

**Data flow**: It receives the tool context and an empty validated input object. It requires an extension context, runs _gate, identifies the target agent, normalizes the member’s email, and deletes the matching grant key from the store. If the action concerns the main agent, it explains that the member still reaches it because the main agent is available to every member; otherwise it confirms removal.

**Call relations**: This function is registered as the handler for the revoke_web_access tool. It mirrors _grant by using the same permission gate, target-agent resolution, and grant-key format, so deleting a grant removes exactly the row that granting created.

*Call graph*: calls 4 internal fn (agent_is_main, _gate, _grant_key, _target_agent); 2 external calls (__init__, __init__).


##### `_read_private_transcript`  (lines 220–256)

```
async def _read_private_transcript(ctx: ToolContext, args: PrivateTranscriptInput) -> ToolResult
```

**Purpose**: Implements the admin tool that records an acknowledgement before opening another member’s private transcript in the web portal. The record is an audit trail: it notes who opened whose private conversation and when.

**Data flow**: It receives the tool context and an empty validated input object. It checks that the speaker is a member, that the speaker is an admin, that the action is not happening in a foreign/shared audience, and that a conversation target is present. It finds the relevant agent, asks the surface layer to record transcript access, and either returns a refusal if nothing valid needed acknowledgement or returns a message confirming the record.

**Call relations**: This function is registered as the handler for the read_private_transcript tool. It uses _refusal for safety failures, _target_agent to identify which agent’s conversation is being opened, and record_transcript_access to write the audit record that the portal’s content gate relies on.

*Call graph*: calls 3 internal fn (speaker_is_admin, _refusal, _target_agent); 4 external calls (__init__, __init__, record_transcript_access, UUID).
