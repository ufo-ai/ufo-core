# Security, Identity, Credentials, Egress Policy, and Billing  `stage-17` (cross-cutting infrastructure)

This stage is shared safety and accounting support used across the whole system. It is not one single startup or shutdown step. Instead, it acts like the building’s security desk, network guard, and cashier during everyday use.

Authentication and Signed Links proves who someone is and creates safe temporary links. It uses signed tokens, which are small messages with a tamper-proof stamp, for login, shared pages, sandbox app access, and file downloads. It also completes outside account sign-ins, such as Anthropic login.

Network, Secret, and Spend Enforcement decides what an agent is allowed to use. It checks the active workspace and agent, unlocks only approved connected accounts, protects stored credentials, tells the network proxy which outside hosts are allowed, and records paid usage so billing limits and balances are respected.

The directly assigned files add privacy boundaries inside conversations. untrusted.py marks outside text as information to read, not commands to follow. audience.py labels who a message is meant for, such as private or shared. subjects.py gives a standard way to say who may read content, like one member or the whole workspace.

## Sub-stages

- [Authentication and Signed Links](stage-17.1.md) `stage-17.1` — 11 files
- [Network, Secret, and Spend Enforcement](stage-17.2.md) `stage-17.2` — 15 files

## Files in this stage

### Conversation Safety Boundaries
Defines how untrusted text and conversation visibility labels are represented and constrained so content is interpreted and shared safely.

### `core/src/ufo/harness/untrusted.py`

`util` · `cross-cutting`

This file solves a prompt-safety problem. Some text given to an agent may come from an untrusted place, such as a web page, a third-party tool, or a background subagent result. That text might contain instructions like “ignore previous rules.” Without a consistent wrapper, the model could confuse those outside words with real directions from the system.

The file defines one shared way to “wall off” that content. Think of it like putting a suspicious letter inside a clear evidence bag: the reader can inspect it, but the bag is labeled so everyone knows it is not an instruction sheet. The wrapper includes a warning notice, an opening marker that names the source, the content itself, and a closing marker.

One important detail is that the function escapes any closing marker already inside the content. This prevents hostile or accidental text from ending the wrapper early and then placing fake instructions after it. By keeping this logic in one small module, different parts of the system do not invent slightly different safety wrappers.

#### Function details

##### `wall`  (lines 22–30)

```
def wall(source: str, content: str) -> str
```

**Purpose**: This function wraps outside content in a standard warning and delimiter so the agent treats it as untrusted data. It is used when the system needs to pass external text to an agent without letting that text masquerade as instructions.

**Data flow**: It receives a source name and the content from that source. It builds a warning, adds an opening untrusted-content marker, copies in the content after replacing any fake closing marker with a harmless escaped version, and then adds the real closing marker. The result is one string that clearly labels and contains the untrusted text.

**Call relations**: When other parts of the system need to deliver tool output or a background child agent’s returned content to an agent, they can call this function first. The function does not decide whether content is trusted; it only provides the shared wrapping format so every caller uses the same safety boundary.


### `core/src/ufo/runtime/turns/audience.py`

`domain_logic` · `conversation turn processing and member-facing reads`

A conversation can be visible to different “audiences”: everyone in the workspace, one specific member, a room, or a room shared with an outside organization. This file gives those audiences a strict text format and provides the rules for using them safely. Without it, two parts of the system might spell the same audience differently, or worse, accidentally let internal shared information appear in an external channel.

The central idea is an Audience, which is just a string with a special meaning. For example, the shared audience is the same as the shared subject, a member audience is based on that member’s UUID, and room audiences are written with prefixes like room: or foreign:. The helper functions build these strings in one approved way, then parse and validate them when they come back from storage or another part of the system.

The file also answers practical questions: “Which audiences can this member read?”, “Is this audience tied to a member?”, and “Which stored subjects may this audience recall?” The foreign-room rule is especially important: an externally shared room can read only its own material, not the workspace-wide shared subject. The narrowing logic acts like a one-way gate: it allows a request to become more specific, but rejects changes that would jump to an unrelated audience.

#### Function details

##### `conversation_audience`  (lines 14–15)

```
def conversation_audience(member_id: UUID | None) -> Audience
```

**Purpose**: Builds the audience label for a normal conversation. If there is no specific member, it returns the workspace-shared audience; otherwise it returns the private audience for that member.

**Data flow**: It receives either a member UUID or None. None becomes the shared audience label, while a UUID is added after the member prefix to make a member-specific label. The result is returned as an Audience value.

**Call relations**: Other functions use this as the one trusted way to create member audience labels. parse_audience uses it to check that a member label is canonical, and readable_audiences uses it to include a member’s own private audience.

*Call graph*: called by 2 (parse_audience, readable_audiences).


##### `room_audience`  (lines 18–19)

```
def room_audience(surface: str, room: str) -> Audience
```

**Purpose**: Builds the audience label for a normal internal room. This is used when a conversation belongs to a named room on a named surface.

**Data flow**: It receives a surface name and a room name. It passes them, along with the normal room prefix, to the shared room-building helper. The returned value is an Audience such as a room-scoped label.

**Call relations**: This is the public helper for internal room audiences. It relies on _room_audience for validation and formatting, and parse_audience calls it when checking whether a room audience string is valid.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `foreign_room_audience`  (lines 22–23)

```
def foreign_room_audience(surface: str, room: str) -> Audience
```

**Purpose**: Builds the audience label for a room that is shared outside the workspace. The separate label matters because external rooms must not be allowed to read internal shared memory.

**Data flow**: It receives a surface name and room name. It passes them, with the foreign-room prefix, to the common room-building helper. The result is a foreign Audience label.

**Call relations**: This mirrors room_audience but marks the room as external. It uses _room_audience for the common checks, and parse_audience uses it to verify foreign-room audience strings.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `_room_audience`  (lines 26–29)

```
def _room_audience(prefix: str, surface: str, room: str) -> Audience
```

**Purpose**: Creates the actual room-style audience string after checking that the pieces are safe to combine. It prevents ambiguous labels by rejecting empty names and names containing colons.

**Data flow**: It receives a prefix, a surface, and a room. If the surface or room is empty, or either contains a colon, it raises a ValueError. Otherwise it joins them into one Audience string in the form prefix + surface + ':' + room.

**Call relations**: This is the shared worker behind room_audience and foreign_room_audience. Those two functions choose the meaning of the prefix, while this helper enforces the common formatting rule.

*Call graph*: called by 2 (foreign_room_audience, room_audience).


##### `parse_audience`  (lines 32–55)

```
def parse_audience(value: str) -> Audience
```

**Purpose**: Checks whether a raw string is a valid audience label and returns it as an Audience. It rejects malformed labels instead of letting questionable audience values move through the system.

**Data flow**: It receives a string. It first accepts the exact shared audience, then splits the string to inspect its prefix. Member labels must contain a valid UUID and match the canonical output of conversation_audience. Room and foreign-room labels must have the right two-part room form and match the canonical room builder. If anything is wrong, it raises ValueError; otherwise it returns the Audience.

**Call relations**: This is the file’s main safety checkpoint. audience_member, audience_subjects, and narrow_audience call it before making decisions, so those decisions are based only on known-good audience labels. It delegates canonical reconstruction to conversation_audience, room_audience, and foreign_room_audience, and uses UUID parsing to validate member IDs.

*Call graph*: calls 3 internal fn (conversation_audience, foreign_room_audience, room_audience); called by 3 (audience_member, audience_subjects, narrow_audience); 1 external calls (UUID).


##### `readable_audiences`  (lines 58–63)

```
def readable_audiences(member_id: UUID) -> tuple[Audience, ...]
```

**Purpose**: Lists the conversation audiences a member is allowed to read from normal member-facing views. A member can read workspace-shared content and their own private content.

**Data flow**: It receives a member UUID. It returns a two-item tuple: the shared audience and the member-specific audience built from that UUID. It does not include room or foreign-room audiences because this layer does not know room membership.

**Call relations**: This function uses conversation_audience to produce the member-specific label in the same format used everywhere else. It is meant for read paths that list conversations or objects visible to a member.

*Call graph*: calls 1 internal fn (conversation_audience).


##### `audience_member`  (lines 66–70)

```
def audience_member(audience: Audience) -> UUID | None
```

**Purpose**: Finds out whether an audience belongs to one specific member. If it does, it returns that member’s UUID; if not, it returns None.

**Data flow**: It receives an Audience value. It first validates and normalizes it through parse_audience. If the parsed label does not start with the member prefix, the result is None. If it does, the member UUID text is removed from the prefix and converted into a UUID object.

**Call relations**: This function depends on parse_audience so it never extracts a member ID from an invalid label. It is useful when later code needs to know whether an audience is private to a member or represents something broader like shared or room content.

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (UUID).


##### `audience_subjects`  (lines 73–80)

```
def audience_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: Returns the stored subjects that a conversation audience is allowed to read. This is the rule that prevents an externally shared room from pulling in internal workspace-shared information.

**Data flow**: It receives an Audience and validates it with parse_audience. If it is a foreign-room audience, the result is a frozen set containing only that audience itself. For all other audiences, the result contains both the workspace-shared subject and the audience’s own subject.

**Call relations**: This function uses parse_audience as its safety gate before applying access rules. It is likely used by recall or lookup code that needs to know which memory subjects are visible to a conversation.

*Call graph*: calls 1 internal fn (parse_audience).


##### `narrow_audience`  (lines 83–98)

```
def narrow_audience(current: Audience, requested: Audience) -> Audience
```

**Purpose**: Combines a current audience and a requested audience without allowing an unsafe audience switch. It permits staying the same, falling back to shared, becoming more specific from shared, or choosing the stricter form between matching internal and foreign room labels.

**Data flow**: It receives the current Audience and the requested Audience. Both are validated with parse_audience. If the request does not change anything, or merely asks for shared, it keeps the current audience. If the current audience is shared, it accepts the requested one. If both are room-like labels for the same surface and room, it chooses the safer matching room form, favoring the foreign label when needed. If the two audiences are unrelated, it raises ValueError.

**Call relations**: This function is the guardrail used when one part of a turn asks to restrict or adjust the audience. It calls parse_audience on both sides first, then uses simple string partitioning to compare room kinds and room keys before returning the allowed audience or rejecting the change.

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (partition).


### `core/src/ufo/runtime/turns/subjects.py`

`data_model` · `cross-cutting`

This file is a small but important naming helper for visibility rules. In this system, a “subject” is a text label that represents who some content was disclosed to. One special subject, `shared`, means the content is readable by every member of the workspace. A member-specific subject starts with `member:` followed by that member’s unique ID, like putting a name tag on a private envelope.

Without this file, different parts of the code might invent slightly different labels for the same idea, such as `shared`, `all`, or `member-123`. That would make permission checks unreliable. By keeping the shared label and the member label format in one place, the rest of the system can speak the same language when deciding what a person is allowed to see.

There are only two actions here. `member_subject` turns a member’s UUID, which is a unique identifier, into the standard member subject string. `subject_shared` checks whether a subject is exactly the shared workspace-wide subject. The longer comment on `subject_shared` clarifies an important boundary: not every group-like place counts as “shared.” Rooms or externally shared channels are not treated as shared just because more than one person might see them; they need their own membership facts elsewhere.

#### Function details

##### `member_subject`  (lines 9–10)

```
def member_subject(member_id: UUID) -> str
```

**Purpose**: This function creates the standard subject label for one workspace member. Code uses it when it needs to mark content as belonging to, or readable by, a specific member rather than everyone.

**Data flow**: It takes in a member UUID, which is a unique ID for that person. It places that ID after the fixed text prefix `member:`. It returns the finished subject string, such as `member:<uuid>`, without changing anything else.

**Call relations**: Other parts of the visibility and conversation system can call this when they need a reliable member-scoped label. It hands back a string in the shared format expected by later permission checks and storage records.


##### `subject_shared`  (lines 13–18)

```
def subject_shared(subject: str) -> bool
```

**Purpose**: This function answers the question: does this subject mean content is shared with every member of the workspace? It is used when the system needs to distinguish workspace-wide visibility from member-specific or other kinds of visibility.

**Data flow**: It takes in a subject string. It compares that string to the single official shared subject value, `shared`. It returns `true` if they match exactly and `false` otherwise; it does not modify any data.

**Call relations**: Other visibility-related code can call this before treating content as readable by all workspace members. It does not delegate to other functions; it simply acts as the small yes-or-no test for the shared half of the visibility model.

## 📊 State Registers Touched

- `reg-effective-config` — The merged settings that tell the whole system how it should run in this deployment.
- `reg-durable-database` — The main long-term database where shared business and runtime records are stored.
- `reg-workspace-member-agent-state` — The saved list of workspaces, people, memberships, seats, and agents.
- `reg-agent-configuration` — Each agent’s saved settings, such as model choice, reasoning mode, tools, visibility, internet access, sandbox size, and setup needs.
- `reg-surface-routing` — The shared routing state that maps browser, Slack, iMessage, terminal, site, and object requests to the right workspace, agent, and conversation.
- `reg-auth-identity-sessions` — The current proof of who a person, operator, shared-link visitor, or external service caller is.
- `reg-credential-connections` — The encrypted accounts, secrets, connection grants, and credential fulfillments that let agents use outside services safely.
- `reg-access-permissions-audience` — The shared rules for who may read, use, share, or act on workspace content and conversations.
- `reg-egress-policy-proxy` — The network allowlist and proxy state that decide which outside hosts sandboxed work may contact.
- `reg-billing-spend-ledger` — The shared accounting state for spend caps, usage charges, prepaid balances, BYOK billing, and ledger exports.
- `reg-feature-flags` — The rollout switches that turn product and infrastructure behavior on or off across the system.
- `reg-model-catalog-providers` — The shared catalog of available AI models, their prices and limits, and the provider clients used to call them.
- `reg-source-config-sync-state` — The configured external sources plus their sync progress, errors, backoff, ownership, and access grants.
- `reg-tool-catalog-allowlists` — The shared list of tools and actions an agent may see or run, including extension tools and sandbox bridge tools.
- `reg-conversation-turn-queue` — The durable state of conversations and turns, including admission, ordering, current runner, lifecycle status, and queued work.
- `reg-transcript-history` — The saved conversation transcript, summaries, compactions, and access records that preserve what happened in a chat.
- `reg-sandbox-runtime` — The durable sandbox and browser workspace handles where agent commands, files, web browsing, and hosted previews run safely.
- `reg-blob-artifact-store` — The shared file, blob, artifact, preview, download, and hosted media storage used by turns and surfaces.
- `reg-observability-trace` — The logs, metrics, traces, health signals, and trace links used to understand what the system is doing.
- `reg-turn-context-token-budget` — The active per-turn context-window and token/image budget accounting used to choose prompt contents, trigger compaction, constrain model rounds, and reconcile usage.
