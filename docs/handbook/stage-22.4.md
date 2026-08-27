# Content Visibility, Audience Labels, and Governance  `stage-22.4`

This stage is shared behind-the-scenes support for keeping workspace content in the right hands. It does not run one main feature by itself. Instead, it supplies the rules that other parts of the system use before showing, sharing, or changing sensitive text.

The audience code labels each conversation turn with who it is meant for, such as the whole workspace, one person, one room, or an externally shared room. The subject rules give those labels a simple shape, distinguishing “everyone here can read this” from “only this member can read this.” Together they act like address labels on mail, and they also check that private or external material is not accidentally combined with the wrong audience.

The untrusted-text wrapper marks outside text as something to examine, not instructions to follow, which helps protect the model from being tricked. Scheduled task visibility applies similar access checks to task prompts and descriptions, based on where the task reports and who created it. Governance protects agent prompts by requiring proposed edits to be approved, and only applying them if the original prompt has not changed meanwhile.

## Files in this stage

### Prompt Governance
Controls how agent prompt changes are proposed, reviewed, and safely applied.

### `core/src/ufo/kinds/governance.py`

`domain_logic` · `request handling`

This file is a safety gate for changing an agent’s configuration, specifically its prompt. Instead of letting code overwrite an agent prompt immediately, it creates a proposal that says: “change this prompt from version A to version B.” Later, when someone approves the proposal, the file checks that the agent is still on version A. If anything has changed in the meantime, the proposal is rejected rather than accidentally overwriting newer work.

The key idea is a digest, which is a short fingerprint made from the prompt text. Like checking that a sealed envelope still has the same stamp before replacing it, the code compares the saved fingerprint from proposal time with the current fingerprint at approval time.

The `Governance` class is tied to one workspace, so proposals cannot accidentally affect agents in another workspace. `propose_change` first confirms the agent exists in that workspace, then stores a pending proposal with the old prompt fingerprint, the new prompt, and the new prompt’s fingerprint. `approve_proposal` loads the proposal, locks the agent row while checking it, and either applies the prompt update or marks the proposal as rejected. It also writes log events when proposals are approved or rejected, which helps operators understand what happened later.

#### Function details

##### `prompt_digest`  (lines 16–17)

```
def prompt_digest(prompt: str) -> str
```

**Purpose**: This function turns a prompt into a stable fingerprint. It is used to tell whether a prompt is still the same without comparing or storing the whole text in every check.

**Data flow**: It receives a prompt as plain text. It encodes that text and runs it through SHA-256, a standard one-way fingerprinting method. It returns the fingerprint as a string of hexadecimal characters.

**Call relations**: When a change is proposed, `Governance.propose_change` uses this to record the fingerprint of the proposed new prompt. When a proposal is approved, `Governance.approve_proposal` uses it again to compare the agent’s current prompt with the fingerprint saved when the proposal was opened.

*Call graph*: called by 2 (approve_proposal, propose_change); 1 external calls (sha256).


##### `Governance.propose_change`  (lines 28–55)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: This method opens a new pending proposal to change an agent’s prompt. It does not change the agent yet; it only records the requested change for later approval.

**Data flow**: It receives an `AgentChange`, which includes the target agent, the expected old prompt fingerprint, and the new prompt text. It creates a new proposal ID, opens a database transaction, checks that the agent belongs to this workspace, and writes a pending proposal row containing the old fingerprint, the new prompt, and the new prompt’s fingerprint. It returns a `ProposalRef`, which is a small reference to the proposal that was just created.

**Call relations**: This is the first half of the governance flow. A caller uses it when they want to request a prompt change safely. It calls `prompt_digest` to fingerprint the new prompt, uses database transaction helpers and SQL building functions to read and write rows, and returns a proposal reference that can later be passed to `Governance.approve_proposal`.

*Call graph*: calls 1 internal fn (prompt_digest); 5 external calls (__init__, insert, select, workspace_tx, uuid4).


##### `Governance.approve_proposal`  (lines 57–114)

```
async def approve_proposal(self, proposal_id: UUID) -> None
```

**Purpose**: This method tries to approve and apply a pending prompt-change proposal. It applies the change only if the agent’s prompt still matches the fingerprint recorded when the proposal was made.

**Data flow**: It receives a proposal ID. Inside a database transaction, it loads that proposal for the current workspace, rejects missing or non-pending proposals with an error, then reads and locks the target agent’s prompt so no competing update can slip in during the check. If the current prompt fingerprint no longer matches the proposal’s saved old fingerprint, it marks the proposal rejected and logs that result. If the fingerprint matches, it updates the agent prompt, marks the proposal approved, and logs the approval.

**Call relations**: This is the second half of the governance flow. It is called after a proposal has been created and someone or something decides it should be approved. It relies on `prompt_digest` for the safety comparison, uses database updates to either apply or reject the proposal, and sends log events through `ufo.o11y.log` so the decision is visible outside the database.

*Call graph*: calls 1 internal fn (prompt_digest); 4 external calls (select, update, workspace_tx, log).


### Conversation Audience Labels
Defines and validates the visibility labels and subject naming rules for conversation content.

### `core/src/ufo/turns/audience.py`

`domain_logic` · `conversation and turn access decisions`

A conversation can produce information that should be visible to different audiences. Some content is shared with the whole workspace. Some belongs only to one member. Some is tied to a room, and some room content may be shared with people outside the organization. This file gives those cases exact string labels and makes sure they are formed safely.

The main idea is simple: an audience label is like a tag on an envelope. Before the system reads from or writes to conversation history, it needs to know which envelope it is allowed to open. The file builds labels such as a shared audience, a member audience, a room audience, or a foreign room audience. It rejects malformed labels, especially labels with missing parts or extra colons that could make them ambiguous.

It also answers permission-shaped questions. For example, `readable_audiences` says a member can read the shared workspace audience and their own private audience. `audience_subjects` says what stored subjects a conversation may draw facts from, with a special rule: an externally shared room reads only itself, not the workspace-shared memory. That prevents internal workspace facts from being recalled into a channel that another organization can see.

Finally, `narrow_audience` lets a conversation become more specific, while blocking unsafe changes from one audience to an unrelated one.

#### Function details

##### `conversation_audience`  (lines 14–15)

```
def conversation_audience(member_id: UUID | None) -> Audience
```

**Purpose**: This function creates the audience label for a normal conversation. If there is no specific member, it uses the shared workspace audience; otherwise it creates a member-specific audience label.

**Data flow**: It receives either a member UUID or `None`. If the input is `None`, it returns the shared audience label. If a member ID is provided, it turns that ID into a string label with the member prefix and returns it as an `Audience`.

**Call relations**: Other code in this file uses it whenever it needs the official spelling of a member audience. `parse_audience` uses it to confirm that a member label is valid, and `readable_audiences` uses it to include a member's private audience alongside the shared one.

*Call graph*: called by 2 (parse_audience, readable_audiences).


##### `room_audience`  (lines 18–19)

```
def room_audience(surface: str, room: str) -> Audience
```

**Purpose**: This function creates an audience label for a room inside the workspace. It is used when content belongs to a named room on a named surface, such as a particular chat system or integration.

**Data flow**: It receives a surface name and a room name. It passes those pieces, along with the normal room prefix, to the shared room-label builder. The result is a validated `Audience` string for that room.

**Call relations**: This is the public helper for normal room audiences. It relies on `_room_audience` for the actual validation and formatting, and `parse_audience` calls it to compare an incoming room label against the one the system would generate itself.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `foreign_room_audience`  (lines 22–23)

```
def foreign_room_audience(surface: str, room: str) -> Audience
```

**Purpose**: This function creates an audience label for a room that is externally shared. That distinction matters because external rooms must not automatically receive internal workspace-shared information.

**Data flow**: It receives a surface name and a room name. It sends those values to the shared room-label builder with the foreign-room prefix. It returns a validated `Audience` label that marks the room as external-facing.

**Call relations**: This is the public helper for foreign room audiences. It uses `_room_audience` to enforce the same safe format as normal rooms, and `parse_audience` uses it to validate labels that claim to describe foreign rooms.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `_room_audience`  (lines 26–29)

```
def _room_audience(prefix: str, surface: str, room: str) -> Audience
```

**Purpose**: This helper builds the actual room audience string and makes sure the room pieces are unambiguous. It prevents empty names and colons inside the surface or room name, because colons are used as separators in the label format.

**Data flow**: It receives a prefix, a surface name, and a room name. It checks that the surface and room are present and do not contain colons. If the inputs are safe, it joins them into one audience label; if not, it raises a `ValueError` instead of producing a confusing label.

**Call relations**: `room_audience` and `foreign_room_audience` both delegate to this helper so normal and external room labels follow the same rules. It is the single gatekeeper for the room-label shape.

*Call graph*: called by 2 (foreign_room_audience, room_audience).


##### `parse_audience`  (lines 32–55)

```
def parse_audience(value: str) -> Audience
```

**Purpose**: This function checks whether a raw string is a valid audience label and returns it as an `Audience` if it is. It protects the rest of the system from accepting misspelled, ambiguous, or forged-looking audience strings.

**Data flow**: It receives a string. It first accepts the exact shared audience label. Otherwise, it splits the string into its prefix and remaining content. For member labels, it checks that the rest is a valid UUID and that the label matches the official member format. For room and foreign-room labels, it checks that the surface and room are present and that rebuilding the label gives the same value. If any check fails, it raises a `ValueError`; otherwise it returns the validated audience.

**Call relations**: This is the file's central validator. `audience_member`, `audience_subjects`, and `narrow_audience` call it before making decisions, so those later decisions are based on a clean, known audience format. It also calls the audience-building helpers to avoid duplicating the rules for member, room, and foreign-room labels.

*Call graph*: calls 3 internal fn (conversation_audience, foreign_room_audience, room_audience); called by 3 (audience_member, audience_subjects, narrow_audience); 1 external calls (UUID).


##### `readable_audiences`  (lines 58–63)

```
def readable_audiences(member_id: UUID) -> tuple[Audience, ...]
```

**Purpose**: This function says which conversation audiences a member is allowed to read in ordinary member-facing views. A member can read the workspace-shared audience and their own private member audience.

**Data flow**: It receives a member UUID. It creates that member's private audience label, pairs it with the shared audience label, and returns both as a tuple. It does not include room or foreign-room audiences, because this function does not know room membership.

**Call relations**: It uses `conversation_audience` to produce the member-specific label in the same format used everywhere else. Other parts of the system can use this result when listing conversations or objects visible to a member.

*Call graph*: calls 1 internal fn (conversation_audience).


##### `audience_member`  (lines 66–70)

```
def audience_member(audience: Audience) -> UUID | None
```

**Purpose**: This function extracts the member ID from an audience label if the label belongs to a single member. If the audience is shared, room-based, or foreign-room-based, it returns nothing.

**Data flow**: It receives an `Audience` value. It first validates it with `parse_audience`. If the validated label does not start with the member prefix, it returns `None`. If it is a member label, it removes the prefix, converts the remaining text back into a UUID, and returns that UUID.

**Call relations**: It depends on `parse_audience` so it only tries to extract a member ID from a well-formed label. Callers can use it when they need to know whether an audience is tied to one specific member.

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (UUID).


##### `audience_subjects`  (lines 73–80)

```
def audience_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: This function tells the system which stored subjects a conversation with this audience may read from. Its most important safety rule is that externally shared rooms may read only their own subject, not the workspace-shared subject.

**Data flow**: It receives an audience label and validates it. If the label is for a foreign room, it returns a frozen set containing only that exact audience subject. For all other valid audiences, it returns a frozen set containing the shared workspace subject plus the audience's own subject.

**Call relations**: It calls `parse_audience` first so the subject decision is made only from a valid label. This function is where the file's privacy boundary becomes concrete: internal audiences can include shared workspace context, while foreign rooms cannot.

*Call graph*: calls 1 internal fn (parse_audience).


##### `narrow_audience`  (lines 83–98)

```
def narrow_audience(current: Audience, requested: Audience) -> Audience
```

**Purpose**: This function decides whether a conversation's audience can safely become more specific. It allows harmless narrowing, such as moving from shared to a requested audience, but rejects unrelated audience changes that could mix up visibility.

**Data flow**: It receives the current audience and a requested audience. It validates both. If they are the same, or if the requested audience is just the shared audience, it keeps the current one. If the current audience is shared, it accepts the requested one. For room and foreign-room labels that point to the same surface-and-room key, it chooses the safer or more specific label according to the room type. If none of those safe cases apply, it raises a `ValueError`.

**Call relations**: It uses `parse_audience` to make sure both inputs have a known shape before comparing them. This function sits at the point where one part of the system asks to change audience scope, and it either returns the allowed audience to continue with or stops the flow with an error.

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (partition).


### `core/src/ufo/turns/subjects.py`

`data_model` · `cross-cutting`

This file is a tiny but important vocabulary file for visibility. In this system, a “subject” is a string label that says who some disclosed content belongs to or can be read by. The simplest subject is "shared", meaning the content is readable by every member of the workspace. Member-specific subjects use the prefix "member:" followed by that member’s unique ID, so each person gets a distinct label.

The file exists so the rest of the codebase does not invent these labels in different ways. That matters because visibility checks depend on exact string matches. If one part of the system wrote "member-123" while another expected "member:123", private or shared content could be misunderstood.

There are two helper functions. One builds the subject string for a member from a UUID, which is a standard unique identifier. The other answers the key question: “Is this the shared subject?” It deliberately only returns true for the exact shared label. Other audience-like places, such as a room or externally shared channel, are not treated as shared here because this check is about workspace-wide readability, not every possible way content might be seen.

#### Function details

##### `member_subject`  (lines 9–10)

```
def member_subject(member_id: UUID) -> str
```

**Purpose**: Builds the standard subject label for one workspace member. Code uses this when it needs a reliable string that means “this belongs to, or is readable by, this specific member.”

**Data flow**: It receives a member UUID, which is a unique member identifier. It puts the fixed text "member:" in front of that ID. It returns the resulting string, such as "member:<uuid>", without changing anything else.

**Call relations**: This is a small shared helper that other code can call whenever it needs to create a member-scoped subject. It pairs with the shared subject constant so callers use one consistent naming scheme instead of hand-building labels.


##### `subject_shared`  (lines 13–18)

```
def subject_shared(subject: str) -> bool
```

**Purpose**: Checks whether a subject label means workspace-wide shared content. Someone would use it before treating disclosed content as readable by every member.

**Data flow**: It receives a subject string. It compares that string with the exact shared label, "shared". It returns true only for that exact value, and false for member-specific labels or any other audience-like strings.

**Call relations**: This helper is used wherever code needs to decide whether a subject is the shared, everyone-can-read case. It does not try to interpret rooms, channels, or member ownership; it only answers the narrow shared-label question so the wider visibility logic can make the next decision correctly.


### Untrusted Text Handling
Wraps externally sourced text so it is treated as inspectable data rather than trusted instructions.

### `core/src/ufo/turns/untrusted.py`

`util` · `cross-cutting`

This file solves a subtle but important safety problem. Tools and background agents may return text from places the system does not fully trust, such as web pages or third-party sources. That text might contain phrases that look like commands. Without a standard wrapper, the model could confuse outside text with real instructions from the system or user.

The file defines one shared way to build that wrapper, called a “wall.” Think of it like putting a suspicious document inside a sealed evidence bag. The model can read what is inside, but the label on the bag says: this is evidence, not an order.

The wrapper has three parts: a warning notice, an opening marker that names the source, and a closing marker. The important safety detail is that if the outside content itself contains the closing marker, the code replaces it with harmless text. That prevents the outside content from “breaking out” of the wrapper and pretending to be trusted instructions afterward.

This matters because different parts of the system may deliver untrusted content to an agent. By keeping the wall format in one file, those paths all use the same wording and the same escaping rule, instead of each inventing its own slightly different version.

#### Function details

##### `wall`  (lines 22–30)

```
def wall(source: str, content: str) -> str
```

**Purpose**: Turns untrusted text into a clearly labeled block that tells the model to treat it only as data. It is used when content from an outside or less-trusted source needs to be shown safely.

**Data flow**: It takes a source name and the content from that source. It builds a warning message, adds an opening untrusted-content marker with the source name, places the content inside, and then adds a closing marker. Before placing the content inside, it replaces any fake closing marker already present in the content, so the content cannot escape the wrapper.

**Call relations**: When another part of the system needs to pass outside content to an agent, it calls this function instead of formatting the warning itself. The function hands back one safe string: the original content sealed inside a standard untrusted-content wall.


### Scheduled Task Visibility
Determines who may read private scheduled task content based on reporting context and creator identity.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/visibility.py`

`domain_logic` · `request handling`

Scheduled tasks can post their results into different kinds of conversations. Some conversations are shared with the whole workspace, while others belong to one specific member. This file answers a simple but important question: “Can this member see what the task is actually about?” Without this check, private task prompts could be shown to the wrong people, or shared tasks could be hidden unnecessarily.

The rule is built around the task’s audience, meaning the place where the task reports its updates. If the audience is shared with everyone, then the task content is visible to everyone too. This is like saying: if the notice is posted on the office bulletin board, the text behind that notice is not private.

If the task reports into a private member conversation, the caller must be that member to see it. If there is no caller member at all, the answer is no unless the task is shared workspace-wide. As a final allowance, the task creator can also see the task content. This matters for cases where the task’s reporting location and creator are separate pieces of information.

The file contains one focused permission helper. Other parts of the scheduled task system can call it before showing task prompts or descriptions.

#### Function details

##### `task_content_visible`  (lines 7–20)

```
def task_content_visible(listed: ListedTask, member_id: UUID | None) -> bool
```

**Purpose**: Decides whether a given member may read a scheduled task’s prompt and description. It is used to keep private task content private while still allowing shared workspace tasks to be readable by everyone.

**Data flow**: It receives a listed task and an optional member ID. First it checks whether the task’s audience is shared with the whole workspace; if so, it returns true. If there is no member ID, it returns false because there is no specific person to match against a private audience. Otherwise it compares the task’s audience with that member’s private subject, and if that does not match, it finally checks whether the same member created the task. The output is a true-or-false answer, and it does not change the task or member data.

**Call relations**: When another part of the scheduled task extension needs to decide whether to reveal task content, it calls this function. The function asks `subject_shared` whether the reporting audience is workspace-wide, and uses `member_subject` to build the private audience name for the member being checked. It then hands back a single permission answer for the caller to enforce.

*Call graph*: 2 external calls (member_subject, subject_shared).
