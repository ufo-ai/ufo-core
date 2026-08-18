# Audience, visibility, governance, and untrusted content safety  `stage-18.6`

This stage is shared behind-the-scenes safety gear. It does not run the main conversation by itself. Instead, it sets rules that other parts of the system rely on before showing content, changing prompts, or accepting outside data.

The audience and subjects files define the allowed labels for “who may see this,” such as a whole workspace or one member. They check those labels so a private conversation is not accidentally treated as public, and so every part of the system uses the same wording. The scheduled task visibility file applies those rules to task prompts and descriptions, deciding whether a member may read them.

The governance file protects agent prompt changes with a proposal and approval step. It only applies an approved change if the original prompt has not changed meanwhile, like signing the exact version of a document.

The untrusted content file wraps outside text so the model treats it as material to inspect, not commands to follow. The image preview file similarly checks outside image data before use, making sure it matches its promised type and size and is not malformed or dangerously large.

## Files in this stage

### Audience boundaries
Defines and validates conversation audience labels to prevent accidental broadening or external leakage.

### `core/src/ufo/audience.py`

`domain_logic` · `cross-cutting during conversation reads, writes, and audience validation`

A conversation can belong to different audiences: everyone in the workspace, one specific member, a normal room, or a room shared with an outside organization. This file gives those audiences a single string format and a set of rules for creating, reading, and comparing them.

Think of an audience like the label on a sealed envelope. If the label says “shared,” everyone in the workspace may read it. If it says “member:<id>,” only that member’s private scope is included. If it says “room:<surface>:<room>” or “foreign:<surface>:<room>,” the conversation is tied to a room, with “foreign” meaning extra caution because an outside party may be present.

The important work here is not just making strings. The file validates that audience strings are shaped exactly as expected, rejects ambiguous values, and decides which stored subjects a conversation may read from. It also contains the rule for “narrowing” an audience: a request may become more specific, but it must not silently switch to an unrelated audience. That prevents a conversation turn from drifting into a different disclosure scope by mistake.

#### Function details

##### `conversation_audience`  (lines 14–15)

```
def conversation_audience(member_id: UUID | None) -> Audience
```

**Purpose**: Builds the audience label for a normal conversation. If there is no specific member, it returns the workspace-wide shared audience; otherwise it returns the private audience for that member.

**Data flow**: It receives either a member UUID or no member at all. With no member, it outputs the shared audience label. With a member UUID, it turns that UUID into the standard member audience string and returns it as an Audience.

**Call relations**: Other functions use this as the one trusted way to spell member and shared audiences. `parse_audience` uses it to confirm that an incoming member label has the exact approved form, and `readable_audiences` uses it when listing what a member can read.

*Call graph*: called by 2 (parse_audience, readable_audiences).


##### `room_audience`  (lines 18–19)

```
def room_audience(surface: str, room: str) -> Audience
```

**Purpose**: Builds an audience label for a normal internal room. This is used when a conversation belongs to a named room on a named surface.

**Data flow**: It receives a surface name and room name. It passes them to the shared room-building helper with the normal room prefix, and returns the resulting Audience.

**Call relations**: This is the public doorway for creating normal room audiences. `parse_audience` calls it when checking that a text value beginning with `room:` is valid and exactly matches the canonical spelling.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `foreign_room_audience`  (lines 22–23)

```
def foreign_room_audience(surface: str, room: str) -> Audience
```

**Purpose**: Builds an audience label for a room that is externally shared. This special label matters because foreign rooms are treated more cautiously than internal rooms.

**Data flow**: It receives a surface name and room name. It passes them to the shared room-building helper with the foreign-room prefix, and returns the resulting Audience.

**Call relations**: This mirrors `room_audience`, but for external rooms. `parse_audience` calls it to validate `foreign:` audience strings against the exact format the system expects.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `_room_audience`  (lines 26–29)

```
def _room_audience(prefix: str, surface: str, room: str) -> Audience
```

**Purpose**: Creates the common room-style audience string after checking that its pieces are safe to combine. It prevents empty names and colons inside the surface or room, because colons are used as separators in the audience format.

**Data flow**: It receives a prefix, a surface, and a room. It first checks that the surface and room are present and do not contain `:`. If the values are safe, it joins them into one audience string; otherwise it raises a ValueError instead of producing an ambiguous label.

**Call relations**: `room_audience` and `foreign_room_audience` both rely on this helper so normal and foreign room audiences follow the same validation rules. It is the shared stamp-maker for room labels.

*Call graph*: called by 2 (foreign_room_audience, room_audience).


##### `parse_audience`  (lines 32–55)

```
def parse_audience(value: str) -> Audience
```

**Purpose**: Checks whether a raw string is a valid audience label and returns it as an Audience if it passes. This is the gatekeeper that stops malformed or misleading audience strings from entering later logic.

**Data flow**: It receives a string. It accepts the exact shared audience immediately. Otherwise it splits the string into its prefix and remaining text, then checks the rules for member, room, and foreign-room audiences. For member labels it verifies the UUID and canonical spelling. For room labels it verifies the surface and room through the normal audience builders. It returns the validated Audience or raises ValueError if anything is wrong.

**Call relations**: Several higher-level functions call this before trusting an audience. `audience_member` uses it before extracting a member ID, `audience_subjects` uses it before deciding what stored subjects can be read, and `narrow_audience` uses it before comparing two audiences.

*Call graph*: calls 3 internal fn (conversation_audience, foreign_room_audience, room_audience); called by 3 (audience_member, audience_subjects, narrow_audience); 1 external calls (UUID).


##### `readable_audiences`  (lines 58–63)

```
def readable_audiences(member_id: UUID) -> tuple[Audience, ...]
```

**Purpose**: Returns the conversation audiences that a specific member is allowed to read from member-facing views. In this model, that means the shared workspace audience and that member’s own private audience.

**Data flow**: It receives a member UUID. It creates that member’s private audience and combines it with the shared audience in a tuple. It does not include rooms, because this workspace-level rule does not know who belongs to each room or external channel.

**Call relations**: This function calls `conversation_audience` to create the member-specific entry. It provides a simple, consistent answer for code that needs to list conversations or conversation-linked objects visible to one member.

*Call graph*: calls 1 internal fn (conversation_audience).


##### `audience_member`  (lines 66–70)

```
def audience_member(audience: Audience) -> UUID | None
```

**Purpose**: Finds the member ID inside a member-specific audience, if there is one. For shared, room, or foreign-room audiences, it reports that there is no single member attached.

**Data flow**: It receives an Audience, validates it with `parse_audience`, then checks whether the validated value starts with the member prefix. If it does, it removes the prefix, turns the remainder into a UUID, and returns it. If not, it returns None.

**Call relations**: This function depends on `parse_audience` so it never extracts data from an invalid label. It is useful for code that needs to ask, “Is this conversation private to one member, and if so, which one?”

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (UUID).


##### `audience_subjects`  (lines 73–80)

```
def audience_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: Decides which stored knowledge subjects a conversation audience is allowed to read. This is a privacy rule: externally shared rooms do not get access to the workspace-shared subject.

**Data flow**: It receives an Audience and first validates it with `parse_audience`. If the audience is foreign, it returns only that foreign audience as the readable subject. For all other valid audiences, it returns both the workspace-shared subject and the audience’s own subject.

**Call relations**: This function sits between audience labels and memory or fact lookup. By calling `parse_audience` first, it works only with trusted labels, then applies the important rule that foreign channels must not recall internal shared workspace information.

*Call graph*: calls 1 internal fn (parse_audience).


##### `narrow_audience`  (lines 83–98)

```
def narrow_audience(current: Audience, requested: Audience) -> Audience
```

**Purpose**: Safely combines an existing audience with a newly requested audience without accidentally changing the disclosure scope. It allows a request to stay the same or become narrower, but rejects unrelated audience changes.

**Data flow**: It receives the current audience and a requested audience. It validates both. If they match, or the request is only the shared audience, it keeps the current audience. If the current audience is shared, it allows switching to the requested more specific audience. If both are room-style audiences for the same surface and room, it chooses the stricter foreign form when needed. Otherwise it raises ValueError to block the audience change.

**Call relations**: This function relies on `parse_audience` before comparing labels. It is used when a conversation or turn carries an audience forward and a later request tries to refine it, acting like a safety catch that prevents the conversation from jumping to a different room, member, or disclosure area.

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (partition).


### Prompt governance
Requires proposed prompt edits to be approved against the exact prompt version they were created from.

### `core/src/ufo/governance.py`

`domain_logic` · `request handling`

This file is a safety gate for changing an agent’s configuration, specifically its prompt. Instead of letting code directly overwrite an agent prompt, it creates a proposal first. That proposal records what agent should change, what the prompt looked like at the time, and what the new prompt should be.

The key idea is like signing a delivery slip only if the package is still sealed the way it was when it left the warehouse. The file uses a digest, which is a short fixed fingerprint made from the prompt text. If even one character in the prompt changes, the digest changes too.

When a change is proposed, the code checks that the agent exists in the current workspace, stores the proposed new prompt, and marks the proposal as pending. When a proposal is approved, the code locks the agent row in the database, recomputes the current prompt fingerprint, and compares it with the fingerprint saved in the proposal. If they match, the prompt is updated and the proposal becomes approved. If they do not match, that means someone changed the prompt in the meantime, so the proposal is rejected instead.

This prevents stale approvals from overwriting newer work. Without this file, two people or extensions could accidentally race each other and silently lose someone’s prompt update.

#### Function details

##### `prompt_digest`  (lines 16–17)

```
def prompt_digest(prompt: str) -> str
```

**Purpose**: This function turns a prompt string into a stable fingerprint. It is used to tell whether a prompt is still the exact same text later, without storing or comparing the whole text every time.

**Data flow**: It receives prompt text, encodes it as bytes, and runs it through SHA-256, a common hashing method that produces a fixed-length fingerprint. It returns that fingerprint as a hexadecimal string. It does not change any outside state.

**Call relations**: When a change is proposed, Governance.propose_change uses this to record the fingerprint of the proposed new prompt. When a proposal is approved, Governance.approve_proposal uses it again to compare the saved original fingerprint with the agent’s current prompt fingerprint.

*Call graph*: called by 2 (approve_proposal, propose_change); 1 external calls (sha256).


##### `Governance.propose_change`  (lines 28–55)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: This method opens a new proposal to change an agent’s prompt. It does not change the agent immediately; it records the requested change so it can be reviewed and safely approved later.

**Data flow**: It receives an AgentChange, which includes the target agent, the prompt fingerprint the proposer thinks is current, and the desired new prompt. It creates a new proposal ID, opens a database transaction, checks that the agent exists in this workspace, stores the proposal with pending status, and saves the new prompt inside the proposal body. It returns a ProposalRef containing the new proposal ID.

**Call relations**: Higher-level code calls this when someone or some extension wants to request a prompt change. Inside the flow, it uses the workspace database transaction to keep the check and insert together, uses prompt_digest to fingerprint the new prompt, and returns a lightweight reference that later code can pass to Governance.approve_proposal.

*Call graph*: calls 1 internal fn (prompt_digest); 5 external calls (__init__, insert, select, workspace_tx, uuid4).


##### `Governance.approve_proposal`  (lines 57–114)

```
async def approve_proposal(self, proposal_id: UUID) -> None
```

**Purpose**: This method approves a pending proposal if it is still safe to apply. It updates the agent prompt only when the agent’s current prompt still matches the prompt fingerprint recorded when the proposal was made.

**Data flow**: It receives a proposal ID and looks up that proposal in the current workspace. If the proposal does not exist or is no longer pending, it raises an error. It then reads and locks the target agent’s prompt, recomputes its fingerprint, and compares it with the proposal’s original fingerprint. If they differ, it marks the proposal rejected and logs that result. If they match, it writes the new prompt to the agent, marks the proposal approved, and logs the approval.

**Call relations**: Higher-level approval code calls this after a proposal has been reviewed. It relies on the database transaction so the prompt check and update happen as one safe operation, uses prompt_digest for the compare-and-swap check, and uses logging to leave an observable record of either approval or rejection.

*Call graph*: calls 1 internal fn (prompt_digest); 4 external calls (select, update, workspace_tx, log).


### Image preview safety
Validates signed preview metadata against actual image bytes to reject malformed or oversized images.

### `core/src/ufo/image_previews.py`

`domain_logic` · `request handling`

Image previews are convenient, but they are also untrusted input: someone can send a file that claims to be a tiny PNG while actually being incomplete, mislabeled, huge after decoding, or crafted to exhaust memory. This file is the gatekeeper for those previews. It defines the supported raster image types, the safety limits, and the checks used before an image preview is trusted.

The flow is like accepting a parcel with a label. First, the code can guess the expected media type from a filename suffix such as “.png” or “.jpg”. Then, when preview bytes arrive as an asynchronous stream, it counts every chunk and compares the total with an ImagePreviewGrant, which is a signed-looking promise containing the media type and byte size. If the stream is larger, smaller, or over the hard maximum, it is rejected.

After the byte count is right, the file is opened with Pillow, the Python image library. The validator checks that the outer container looks complete, verifies that Pillow agrees with the claimed type, walks through animation frames, enforces width, height, frame count, and total decoded-pixel limits, and forces each frame to actually decode. Any suspicious image error is converted into InvalidImagePreview, so callers get one clear failure type instead of many low-level exceptions.

#### Function details

##### `raster_image_media_type`  (lines 43–44)

```
def raster_image_media_type(path: str) -> RasterImageMediaType | None
```

**Purpose**: This function guesses the image media type from a path or filename. It is useful before validation, when the system needs to know whether a file extension represents a supported preview format such as PNG, JPEG, GIF, or WebP.

**Data flow**: It takes a path string, looks only at the final filename suffix, lowercases it, and checks it against the known raster image suffixes. It returns the matching media type string when the suffix is supported, or nothing if the suffix is unknown.

**Call relations**: This is an early helper in the image-preview flow. It uses a path parser to safely extract the suffix, then hands back the media type that later validation can compare against the actual image bytes.

*Call graph*: 1 external calls (PurePosixPath).


##### `validated_image_preview`  (lines 47–61)

```
async def validated_image_preview(stream: AsyncIterator[bytes], grant: ImagePreviewGrant) -> bytes
```

**Purpose**: This function reads an incoming image-preview byte stream and accepts it only if it exactly matches the promised size and passes full image safety checks. Callers use it when they need the final preview bytes, but only after proving they are safe enough to keep or show.

**Data flow**: It receives an asynchronous stream of byte chunks and an ImagePreviewGrant containing the claimed media type and size. As chunks arrive, it counts them and rejects the preview if the count goes past the grant or the global byte limit. When the stream ends, it requires the final byte count to exactly equal the claim, joins the chunks into one bytes object, runs the image validator in a background thread, and returns the original bytes if all checks pass. If anything is wrong, it raises InvalidImagePreview instead of returning data.

**Call relations**: This is the main public validation path for streamed preview data. It performs the cheap size checks itself, then hands the finished byte string to _ImagePreviewValidator.validate through asyncio.to_thread, so the heavier image decoding work does not block the asynchronous event loop.

*Call graph*: 2 external calls (__init__, to_thread).


##### `_ImagePreviewValidator.validate`  (lines 66–109)

```
def validate(data: bytes, media_type: RasterImageMediaType) -> None
```

**Purpose**: This function deeply inspects the image bytes to confirm they are a real, complete, correctly labeled, and reasonably sized image. It protects the rest of the system from broken images and from images that are small on disk but enormous or expensive when decoded.

**Data flow**: It takes raw image bytes and the media type the preview claims to be. First it checks format-specific container endings or headers. Then it opens the bytes with Pillow, asks Pillow to verify the image structure, records the actual detected format, opens the image again, and walks through its frames. For each frame, it checks dimensions, counts total decoded pixels, and loads the frame to make sure decoding really works. If Pillow or the container checks report a problem, the function turns that into InvalidImagePreview. If the detected media type does not match the claimed one, it also rejects the preview. On success, it returns nothing and simply means “this image is acceptable.”

**Call relations**: validated_image_preview calls this after it has collected the stream and confirmed the byte count. Inside the validation story, this function first delegates the quick outer-file checks to _ImagePreviewValidator._validate_container, then relies on Pillow to verify and decode the image contents.

*Call graph*: 5 external calls (__init__, open, BytesIO, catch_warnings, simplefilter).


##### `_ImagePreviewValidator._validate_container`  (lines 112–125)

```
def _validate_container(data: bytes, media_type: RasterImageMediaType) -> None
```

**Purpose**: This function performs quick format-specific checks that the image file appears complete before Pillow does deeper decoding. It catches obvious truncation, such as a JPEG missing its normal end marker or a WebP whose declared length does not match the actual byte count.

**Data flow**: It receives the raw bytes and the claimed media type. Depending on the type, it checks expected ending bytes, header bytes, or embedded length information. If the bytes do not look like a complete file of that type, it raises InvalidImagePreview. If the basic container check passes, it returns nothing.

**Call relations**: _ImagePreviewValidator.validate calls this near the start of deep validation. It acts as the first checkpoint before the more expensive Pillow-based verification and frame decoding happen.

*Call graph*: 1 external calls (__init__).


### Reader subject labels
Provides the canonical vocabulary for describing workspace-wide and member-specific readership.

### `core/src/ufo/subjects.py`

`domain_logic` · `cross-cutting`

This file is about visibility: who is allowed to see something. It defines one special subject, "shared", meaning content is readable by every member of the workspace. It also defines a standard way to name a member-specific subject, by putting "member:" in front of that member's unique ID. A subject here is just a text label used elsewhere in the system to talk about an audience or reader.

The reason this tiny file matters is consistency. If one part of the project wrote "member:123" and another wrote "user:123", they would not match, and private or shared content could be interpreted incorrectly. This file acts like a label maker: it gives every caller the same format.

There are two pieces of behavior. `member_subject` turns a member UUID, which is a globally unique identifier, into the text label used for that member. `subject_shared` checks whether a subject label is exactly the special shared label. Its docstring also explains an important boundary: some audiences, like rooms or externally shared channels, are not treated as "shared" just because multiple people may see them. In this model, "shared" specifically means readable by every workspace member.

#### Function details

##### `member_subject`  (lines 9–10)

```
def member_subject(member_id: UUID) -> str
```

**Purpose**: This function creates the standard subject label for one specific workspace member. Code uses it when it needs to mark content as belonging or being visible to that member rather than to everyone.

**Data flow**: A member UUID goes in. The function converts it into text and prefixes it with "member:", producing a subject string like "member:<id>". Nothing else is changed; the finished label is returned to the caller.

**Call relations**: This is a small building block for any code that needs to create member-scoped visibility labels. Instead of each caller formatting the label itself, they call this function so the rest of the system can compare subject strings reliably.


##### `subject_shared`  (lines 13–18)

```
def subject_shared(subject: str) -> bool
```

**Purpose**: This function answers a yes-or-no question: does this subject mean content is shared with every member of the workspace? It is used to distinguish truly workspace-wide content from content aimed at a narrower audience.

**Data flow**: A subject string goes in. The function compares it with the one official shared label, "shared". It returns `True` if they match exactly, and `False` for anything else, including member-specific labels, rooms, or external channels.

**Call relations**: Other visibility or audience code can call this when deciding whether a piece of content should be readable by the whole workspace. It does not try to resolve memberships or permissions itself; it only performs the simple, central check for the special shared subject.


### Untrusted text wrapping
Marks external text as data for the model to inspect rather than instructions to follow.

### `core/src/ufo/untrusted.py`

`util` · `cross-cutting`

This file solves a safety problem: an outside source might return text that looks like instructions for the agent, but the system must not let that text take control. The file defines one shared way to place that outside text behind a clear “wall.” Think of it like putting a suspicious document inside a labeled evidence bag: people can inspect it, but the label reminds them not to treat it as an order.

The wrapper has three important parts. First, it adds a plain warning that names the source and says the content is untrusted. Second, it surrounds the content with an opening and closing marker, using an XML-like tag called `<untrusted-content>`. Third, it protects against a trick where the untrusted text contains the closing marker itself. If that happened unchanged, the text could appear to “break out” of the safe area and continue as normal instructions. To stop that, the file replaces any closing marker inside the body with an escaped version, meaning it is displayed as text rather than used as a real boundary.

This matters because more than one part of the system needs this behavior. By keeping the rule in one place, tool results and background child-agent results use the same safety wrapper and cannot accidentally drift apart.

#### Function details

##### `wall`  (lines 22–30)

```
def wall(source: str, content: str) -> str
```

**Purpose**: Wraps outside content in a standard warning and boundary so the model treats it as untrusted data, not instructions. It also prevents the content from faking the end of the wrapper.

**Data flow**: It takes a source name and a text body. It writes the source name into the warning and opening marker, replaces any real closing marker inside the body with a harmless escaped version, then returns one combined string: warning, opening marker, protected content, and closing marker.

**Call relations**: Other parts of the system call this when they are about to pass external or lower-trust results to an agent, such as tool output or a background child agent’s returned result. Instead of each caller inventing its own wrapper, they hand the source and content to `wall`, which gives back the one approved safe form.


### Scheduled task visibility
Applies readership rules to decide who may view scheduled task prompts and descriptions.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/visibility.py`

`domain_logic` · `permission checks during scheduled task listing or viewing`

Scheduled tasks can post their results into different kinds of conversations. Some conversations are shared with the whole workspace, while others belong to one member. This file answers a simple but important privacy question: “Can this member see what the task was asked to do?”

The rule is based mostly on where the task reports its replies. If the task reports into a shared workspace audience, then its content is considered shared too, because everyone can already read what it posts there. If the task reports into a private member conversation, then only the matching member should be able to read it.

There is one extra allowance: the member who created the task can read it, even if the task is not addressed directly to their own member conversation. If there is no logged-in member at all, the caller can only see tasks whose audience is shared with the workspace.

The important detail is that a missing creator does not make a task public. Privacy is decided first by the audience. In everyday terms, if a note is pinned on the office noticeboard, everyone can read it; if it is left in someone’s private mailbox, only the right person can.

#### Function details

##### `task_content_visible`  (lines 7–20)

```
def task_content_visible(listed: ListedTask, member_id: UUID | None) -> bool
```

**Purpose**: Decides whether the given member may read a scheduled task’s private content, such as its prompt and description. It is used to enforce the project’s privacy rule for tasks that report either to the whole workspace or to a specific member.

**Data flow**: It receives a listed task and an optional member ID. First it checks whether the task’s audience is shared with the workspace; if so, it returns true. If there is no member ID, it returns false because an anonymous or memberless caller cannot read private task content. Then it compares the task’s audience with the subject for that member; if they match, it returns true. Finally, it checks whether the same member created the task and returns that result.

**Call relations**: This function relies on `subject_shared` to recognize workspace-wide audiences and on `member_subject` to build the private audience name for a specific member. Those two helpers let it compare the task’s audience in a consistent way instead of guessing from raw values.

*Call graph*: 2 external calls (member_subject, subject_shared).
