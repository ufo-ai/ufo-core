# Commit, Presentation, Media, and Outgoing Delivery  `stage-13`

This stage happens near the end of a turn, after the assistant has produced results. Its job is to make those results durable, visible, and deliverable. It is like the publishing desk: it saves the record, prepares the attachments, and sends the finished reply to the right place.

The transcript file is the safety lock for the conversation record. It reads and writes the saved transcript, but only lets the record move forward. That prevents an older or incomplete version from overwriting a newer one.

Artifacts, Files, Previews, and Hosted Media handles the things a reply may point to: files, images, downloads, and hosted sandbox websites. It checks access, creates safe links, validates previews, and can capture site screenshots for cards.

Surface Replies and Conversation Display Slots handles what people actually see in the web app or connectors like Slack, iMessage, and terminal clients. It sends final replies and prepares side panels for sources, artifacts, sites, tasks, previews, and other extras. Together, these parts turn completed work into a trustworthy conversation view.

## Sub-stages

- [Artifacts, Files, Previews, and Hosted Media](stage-13.1.md) `stage-13.1` — 9 files
- [Surface Replies and Conversation Display Slots](stage-13.2.md) `stage-13.2` — 5 files

## Files in this stage

### Commit, Presentation, Media, and Outgoing Delivery
### `core/src/ufo/runtime/transcript.py`

`domain_logic` · `turn completion and repair publishing`

A conversation transcript is the durable record of what has happened so far. This file wraps a blob store, which is a simple place to save and fetch named chunks of data, and uses it as the shared home for that record. The main risk it protects against is timing: two parts of the system may try to publish a transcript for the same turn, especially when a repair or retry path is involved. Without the checks here, a fallback record could win the race and permanently hide the fuller record produced by the run that actually did the work.

The `Transcript` object is tied to one conversation id. Its `read` method looks up that conversation’s transcript blob and turns the saved bytes back into a `Conversation` object. If nothing has been saved yet, it returns nothing instead of treating that as an error.

Its `write` method is cautious. Before saving, it reads what is already there. It only writes if the incoming conversation is newer, or if it is a same-turn replacement that is known to be more authoritative and at least as complete. The helper `_supersedes` is the gatekeeper for that decision. In everyday terms, it is like updating a shared notebook: later pages can replace earlier ones, but a rough rescue note should not overwrite the full note written by the person who actually did the task.

#### Function details

##### `Transcript.read`  (lines 18–23)

```
async def read(self) -> Conversation | None
```

**Purpose**: This reads the saved transcript for this conversation, if one exists. It gives callers a usable `Conversation` object instead of raw stored bytes.

**Data flow**: It starts with the transcript’s conversation id and blob store. It builds the storage key for that conversation, asks the blob store for the saved data, and decodes the data into a conversation record. If the blob is missing, it returns `None` to mean there is no saved transcript yet.

**Call relations**: This is the first step used by `Transcript.write` before deciding whether a new transcript may replace the old one. It relies on the shared transcript key format and decoder from `ufo.runtime.turns.transcript` so all readers and writers agree on where the transcript lives and how it is shaped.

*Call graph*: called by 1 (write); 2 external calls (decode, transcript_key).


##### `Transcript.write`  (lines 25–29)

```
async def write(self, conversation: Conversation) -> None
```

**Purpose**: This safely saves a conversation transcript without letting older or weaker information overwrite newer or fuller information. Someone uses it when a run, retry, or repair flow wants to publish the conversation’s durable state.

**Data flow**: It receives a `Conversation` object that might be written. First it reads the current stored conversation. If something is already stored, it asks `_supersedes` whether the incoming record is allowed to replace it. If not, it leaves the blob untouched. If yes, it encodes the conversation and writes it to the blob key for this conversation.

**Call relations**: This is the file’s main action. It calls `Transcript.read` to see the current state, then hands the comparison to `_supersedes`. Only after that guard passes does it use the shared encoder and transcript key helper to store the new durable transcript.

*Call graph*: calls 2 internal fn (read, _supersedes); 2 external calls (encode, transcript_key).


##### `_supersedes`  (lines 32–48)

```
def _supersedes(incoming: Conversation, stored: Conversation) -> bool
```

**Purpose**: This decides whether one transcript is allowed to replace another. Its job is to keep the saved transcript moving forward and to prefer the real run’s complete record over a repair fallback when they describe the same turn.

**Data flow**: It receives an incoming conversation and the conversation already stored. If the incoming sequence number is different, it returns true only when the incoming one is later. If both have the same sequence number, it returns true only when the incoming record came from the actual run, the stored one did not, and the incoming message list is at least as long as the stored one. The output is a simple yes-or-no decision.

**Call relations**: This helper is called by `Transcript.write` just before any overwrite can happen. It does not read or write storage itself; it supplies the rule that protects the shared transcript from race conditions between normal turn completion and repair republishing.

*Call graph*: called by 1 (write).

## 📊 State Registers Touched

- `reg-durable-database` — The main long-term database where shared business and runtime records are stored.
- `reg-surface-routing` — The shared routing state that maps browser, Slack, iMessage, terminal, site, and object requests to the right workspace, agent, and conversation.
- `reg-auth-identity-sessions` — The current proof of who a person, operator, shared-link visitor, or external service caller is.
- `reg-access-permissions-audience` — The shared rules for who may read, use, share, or act on workspace content and conversations.
- `reg-conversation-turn-queue` — The durable state of conversations and turns, including admission, ordering, current runner, lifecycle status, and queued work.
- `reg-inbound-message-queue` — The saved queue of incoming external messages waiting to be rendered, ordered, deduplicated, and admitted as turns.
- `reg-live-turn-stream` — The live event feed that lets clients and other processes watch a running turn and learn how it ended.
- `reg-transcript-history` — The saved conversation transcript, summaries, compactions, and access records that preserve what happened in a chat.
- `reg-sandbox-runtime` — The durable sandbox and browser workspace handles where agent commands, files, web browsing, and hosted previews run safely.
- `reg-blob-artifact-store` — The shared file, blob, artifact, preview, download, and hosted media storage used by turns and surfaces.
- `reg-presentation-slots` — The shared conversation display slots for showing artifacts, sources, tasks, sites, automations, image previews, and other side-panel content.
- `reg-delegation-workflows` — The saved state for subagents, parent-child turns, objectives, workflow checkpoints, pending deliveries, and recovery.
- `reg-object-journal` — The shared naming and change history for workspace objects such as tasks, prompts, skills, monitors, memories, and reports.
- `reg-observability-trace` — The logs, metrics, traces, health signals, and trace links used to understand what the system is doing.
- `reg-outbound-surface-delivery-queue` — The durable outgoing reply/writeback state, including mid-turn replies and surface deliveries that must be claimed, sent, retried, and acknowledged exactly once.
- `reg-conversation-workspace-change-state` — The persisted record of file/workspace changes detected for a conversation sandbox, used for commit summaries, artifact presentation, recovery, and debugging.
- `reg-workspace-object-store` — The current persisted workspace object records, such as tasks, monitors, todos, reports, prompts, and site metadata, read and mutated through object APIs, tools, jobs, and slots.
- `reg-support-feedback-reports` — The buffered debugger/support reports emitted by agents or operators and later delivered to or inspected by engineering.
- `reg-source-page-corpus` — The canonical stored page/document records fetched from sources, including content and browse metadata before they are chunked, embedded, searched, or displayed.
- `reg-proposal-approval-state` — Persisted proposed changes with before/after payloads, authoring information, and pending/approved/rejected status used for review and application workflows.
