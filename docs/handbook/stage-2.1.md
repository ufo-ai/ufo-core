# Core Conversation, Turn, Inbound Message, and Artifact Migrations  `stage-2.1`

This stage is shared behind-the-scenes support for the database. It is made of Alembic migrations, which are ordered scripts that change the database layout without rewriting the whole system. These changes help the product remember conversations, turns, messages, and shared files more accurately as features grow.

One group expands conversation records with useful labels: where the conversation started, who it was for, its saved title, Git workspace changes, and links to the sandbox, which is the isolated work area used for code or files. Another group makes turn records richer. A turn is one step in a conversation or agent workflow. These migrations record parent and child turns, subagent details, admission reasons, tracing information for debugging, and stored context such as sender or timezone.

A third group improves turn lookup, including who spoke, which subagent must report back, and which agent turns are active or recent. The inbound message migrations create and refine the intake tray for messages waiting to be processed. The artifact migrations create storage for shared files and add stable IDs and preview data.

## Sub-stages

- [Conversation Metadata, Surface, Sandbox, and Titles](stage-2.1.1.md) `stage-2.1.1` — 7 files
- [Turn Hierarchy, Admission, Context, and Tracing](stage-2.1.2.md) `stage-2.1.2` — 8 files
- [Turn Speaker, Spoken-Turn, Subagent, and Agent Indexes](stage-2.1.3.md) `stage-2.1.3` — 6 files
- [Inbound Message Queue Migrations](stage-2.1.4.md) `stage-2.1.4` — 3 files
- [Shared Artifact Storage and Preview Migrations](stage-2.1.5.md) `stage-2.1.5` — 3 files
