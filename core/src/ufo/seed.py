"""One conversation that exercises every shape the portal draws, written to the durable stores.

Fabricating a settled conversation — finished turns, the terminal frames they committed, the
transcript a turn writes when it ends — is engine-private state: the SDK hands an extension neither
those tables nor a seam that mints a finished turn, and one that did would let any extension write
audit history it never ran. So the verb lives beside the writer of those rows. All it holds of the
web surface is the chat row the portal gates a conversation on, written and dropped through the
extension store's own API, under that extension's key space and no other's.
"""

from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa

from ufo.blob import WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.ext.context import ScopedStore
from ufo.ext.surface import retitle_conversation
from ufo.models.interface import Message, TextBlock, ToolResultBlock, ToolUseBlock
from ufo.schema import tables
from ufo.schema.records import (
    SUBAGENT_SURFACE,
    AskQuestion,
    AskUserInput,
    QuestionOption,
    TerminalFrame,
)
from ufo.transcript import Conversation, encode, transcript_key

KITCHEN_SINK_TITLE = "Kitchen sink"

WEB_SURFACE = "web"
WEB_EXTENSION = "web"
CHAT_ROW_PREFIX = "chat/"
SEED_QUEUE_PREFIX = "kitchen-sink/"

SCROLL_AUDIT = """# Scroll audit

Every surface that draws a conversation, and what it does about staying at the foot.

| Surface | Scroll logic | Anchors a turn | Holds position on prepend |
| --- | --- | --- | --- |
| Chat | hand-rolled | no | no |
| Conversations | none | no | no |
| Subagent run | none | no | no |

## What the hand-rolled pane does

A `MutationObserver` writes `scrollTop` on every mutation, and a threshold of 40 pixels decides
whether the reader still counts as being at the foot.

```tsx
const watch = new MutationObserver(follow);
watch.observe(pane, { characterData: true, childList: true, subtree: true });
```

## What it does not do

- hold the read position when older messages load in above
- anchor a turn, so there is no way back to where the member asked
- skip layout for messages scrolled far out of view

> The pane is part of the log, not of the screen around it.
"""

MESSAGE_METRICS = """surface,messages,median_height_px,tallest_px
chat,42,96,1180
conversations,17,88,640
subagent,9,72,410
"""


def _framed(turn_id: UUID, said: str) -> Message:
    return Message(role="user", content=f"<context>\nmessage_ref: {turn_id}\n</context>\n{said}")


@dataclass(frozen=True)
class _PriorRun:
    root: UUID
    conversations: tuple[UUID, ...]
    turns: tuple[UUID, ...]


@dataclass(frozen=True)
class KitchenSink:
    """A conversation carrying one of everything a reply can hold — tool calls, a subagent that
    spawned a subagent of its own, shared files, a cost line, and a question still standing.

    Every run mints a new conversation and drops what earlier runs left, so an operator reads one
    run's output and a design change can be checked against the verb as many times as it takes.
    What marks a run as this verb's to destroy is the `queue_key` it opened under — a column no
    member can write, so a conversation a member named after the demo is never one of them. The
    turns the seed writes carry the same mark in `idempotency_key`; a turn without it was admitted
    by the engine for a member who spoke in the run, and a `transcript_access` row is an admin's
    disclosure on the record. Either makes the run workspace history: it stands whole, and the seed
    only ever destroys rows it wrote.
    """

    blob: WorkspaceBlobStore
    workspace_id: UUID
    agent_id: UUID
    member_id: UUID
    email: str

    async def write(self) -> UUID:
        conversation_id = uuid4()
        turns = tuple(uuid4() for _ in range(3))
        await self._clear()
        await self._open(conversation_id, turns)
        await self._runs(conversation_id, turns[1])
        await self._files(turns[1])
        await self.blob.put(
            transcript_key(conversation_id), encode(Conversation(seq=1, messages=self._said(turns)))
        )
        return conversation_id

    async def _clear(self) -> None:
        store = ScopedStore(extension=WEB_EXTENSION)
        for run in await self._prior():
            artifacts = await self._drop(run)
            await store.delete(f"{CHAT_ROW_PREFIX}{run.root}")
            for key in (*artifacts, *(transcript_key(held) for held in run.conversations)):
                await self.blob.delete(key)

    async def _prior(self) -> tuple[_PriorRun, ...]:
        async with workspace_tx() as connection:
            roots = (
                (
                    await connection.execute(
                        sa.select(tables.conversation.c.id).where(
                            tables.conversation.c.workspace_id == self.workspace_id,
                            tables.conversation.c.surface == WEB_SURFACE,
                            tables.conversation.c.queue_key.startswith(
                                SEED_QUEUE_PREFIX, autoescape=True
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            runs: list[_PriorRun] = []
            for root in roots:
                conversations = [root]
                turns: list[UUID] = []
                frontier = [root]
                while frontier:
                    found = (
                        (
                            await connection.execute(
                                sa.select(tables.turn.c.id).where(
                                    tables.turn.c.conversation_id.in_(frontier)
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    turns.extend(found)
                    if not found:
                        break
                    frontier = list(
                        (
                            await connection.execute(
                                sa.select(tables.conversation.c.id).where(
                                    tables.conversation.c.workspace_id == self.workspace_id,
                                    tables.conversation.c.queue_key.in_(
                                        [str(turn) for turn in found]
                                    ),
                                )
                            )
                        ).scalars()
                    )
                    conversations.extend(frontier)
                spoken = (
                    await connection.execute(
                        sa.select(tables.turn.c.id)
                        .where(
                            tables.turn.c.conversation_id.in_(conversations),
                            sa.or_(
                                tables.turn.c.idempotency_key.is_(None),
                                sa.not_(
                                    tables.turn.c.idempotency_key.startswith(
                                        SEED_QUEUE_PREFIX, autoescape=True
                                    )
                                ),
                            ),
                        )
                        .limit(1)
                    )
                ).first()
                disclosed = (
                    await connection.execute(
                        sa.select(tables.transcript_access.c.id)
                        .where(tables.transcript_access.c.conversation_id.in_(conversations))
                        .limit(1)
                    )
                ).first()
                if spoken is None and disclosed is None:
                    runs.append(
                        _PriorRun(root=root, conversations=tuple(conversations), turns=tuple(turns))
                    )
        return tuple(runs)

    async def _drop(self, run: _PriorRun) -> tuple[str, ...]:
        async with workspace_tx() as connection:
            artifacts = tuple(
                (
                    await connection.execute(
                        sa.select(tables.shared_artifact.c.blob_key).where(
                            tables.shared_artifact.c.turn_id.in_(run.turns)
                        )
                    )
                ).scalars()
            )
            await connection.execute(
                sa.delete(tables.shared_artifact).where(
                    tables.shared_artifact.c.turn_id.in_(run.turns)
                )
            )
            await connection.execute(
                sa.delete(tables.conversation_change).where(
                    tables.conversation_change.c.conversation_id.in_(run.conversations)
                )
            )
            await connection.execute(
                sa.delete(tables.turn).where(tables.turn.c.conversation_id.in_(run.conversations))
            )
            await connection.execute(
                sa.delete(tables.conversation).where(
                    tables.conversation.c.id.in_(run.conversations)
                )
            )
        return artifacts

    async def _open(self, conversation_id: UUID, turns: tuple[UUID, ...]) -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=self.workspace_id,
                    agent_id=self.agent_id,
                    surface=WEB_SURFACE,
                    queue_key=f"{SEED_QUEUE_PREFIX}{uuid4().hex}",
                    member_id=self.member_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            for seq, (turn_id, terminal) in enumerate(
                zip(turns, self._terminals(), strict=True), start=1
            ):
                await connection.execute(
                    sa.insert(tables.turn).values(
                        id=turn_id,
                        workspace_id=self.workspace_id,
                        conversation_id=conversation_id,
                        agent_id=self.agent_id,
                        seq=seq,
                        status="done",
                        inbound="ask",
                        admission_source="member",
                        speaker_member_id=self.member_id,
                        idempotency_key=f"{SEED_QUEUE_PREFIX}{uuid4().hex}",
                        terminal=terminal.model_dump(mode="json"),
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        await retitle_conversation(self.workspace_id, conversation_id, KITCHEN_SINK_TITLE)
        await ScopedStore(extension=WEB_EXTENSION).put(
            f"{CHAT_ROW_PREFIX}{conversation_id}",
            {"agent_id": str(self.agent_id), "email": self.email},
        )

    def _terminals(self) -> tuple[TerminalFrame, ...]:
        return (
            TerminalFrame(
                status="done",
                text="",
                model="claude-opus-5",
                tokens=1840,
                cost_micro_usd=27600,
            ),
            TerminalFrame(
                status="done",
                text="",
                model="claude-opus-5",
                tokens=9120,
                cost_micro_usd=143000,
            ),
            TerminalFrame(
                status="done",
                text="",
                model="claude-opus-5",
                tokens=2260,
                cost_micro_usd=31900,
                question=AskUserInput(
                    title="Two things before I open the pull request.",
                    questions=(
                        AskQuestion(
                            question="Which branch should it target?",
                            header="Target",
                            options=(
                                QuestionOption(
                                    label="main", description="Ships on the next release train."
                                ),
                                QuestionOption(
                                    label="release-candidate",
                                    description="Held until the current candidate closes.",
                                ),
                            ),
                        ),
                        AskQuestion(
                            question="Who should review it?",
                            header="Reviewers",
                            multi_select=True,
                            options=(
                                QuestionOption(label="Priya", description="Owns the web surface."),
                                QuestionOption(label="Marco", description="Owns the theme."),
                                QuestionOption(label="Nobody yet"),
                            ),
                        ),
                        AskQuestion(
                            question="Anything to add to the description?",
                            header="Description",
                            free_text_only=True,
                        ),
                    ),
                ),
            ),
        )

    async def _runs(self, conversation_id: UUID, parent: UUID) -> None:
        child, grandchild = uuid4(), uuid4()
        spawned = await self._run(
            child, parent, "general_purpose", "Four files reference the old scroll code."
        )
        await self._run(grandchild, child, "deep_research", "Nothing else imports it.")
        await self.blob.put(
            transcript_key(spawned),
            encode(
                Conversation(
                    seq=1,
                    messages=(
                        Message(role="user", content="{}"),
                        Message(
                            role="assistant",
                            content=(
                                TextBlock(text="Reading the components that scroll."),
                                ToolUseBlock(
                                    id="call-child", name="grep", input={"pattern": "scrollTop"}
                                ),
                            ),
                        ),
                        Message(
                            role="user",
                            content=(
                                ToolResultBlock(
                                    tool_use_id="call-child", content="4 matches", activity=True
                                ),
                            ),
                        ),
                    ),
                )
            ),
        )

    async def _run(self, turn_id: UUID, parent: UUID, profile: str, answered: str) -> UUID:
        spawned = uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=spawned,
                    workspace_id=self.workspace_id,
                    agent_id=self.agent_id,
                    surface=SUBAGENT_SURFACE,
                    queue_key=str(parent),
                    member_id=self.member_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=turn_id,
                    workspace_id=self.workspace_id,
                    conversation_id=spawned,
                    agent_id=self.agent_id,
                    seq=1,
                    status="done",
                    inbound="ask",
                    speaker_member_id=self.member_id,
                    parent_turn_id=parent,
                    subagent_profile=profile,
                    idempotency_key=f"{SEED_QUEUE_PREFIX}{uuid4().hex}",
                    terminal=TerminalFrame(
                        status="done", text=f'{{"result": "{answered}"}}'
                    ).model_dump(mode="json"),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        return spawned

    async def _files(self, turn_id: UUID) -> None:
        for filename, media, body in (
            ("scroll-audit.md", "text/markdown", SCROLL_AUDIT),
            ("message-metrics.csv", "text/csv", MESSAGE_METRICS),
        ):
            content = body.encode()
            key = f"artifacts/{uuid4()}/{filename}"
            await self.blob.put(key, content)
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.shared_artifact).values(
                        turn_id=turn_id,
                        blob_key=key,
                        workspace_id=self.workspace_id,
                        filename=filename,
                        subject=filename,
                        media_type=media,
                        size_bytes=len(content),
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )

    def _said(self, turns: tuple[UUID, ...]) -> tuple[Message, ...]:
        return (
            _framed(turns[0], "Where does the chat pane decide to stay at the bottom?"),
            Message(
                role="assistant",
                content=(
                    ToolUseBlock(id="call-1", name="read", input={"path": "src/views/Chat.tsx"}),
                ),
            ),
            Message(
                role="user",
                content=(
                    ToolResultBlock(tool_use_id="call-1", content="484 lines", activity=True),
                ),
            ),
            Message(
                role="assistant",
                content=(
                    "It is hand-rolled in `Chat.tsx`. A `MutationObserver` watches the pane and "
                    "writes `scrollTop` on every mutation, and a `PIN_THRESHOLD_PX` of 40 decides "
                    "whether the reader still counts as being at the foot.\n\n"
                    "Three things it does not do:\n\n"
                    "- hold the read position when older messages load in above\n"
                    '- anchor a turn, so there is no "back to where I asked"\n'
                    "- skip layout for messages scrolled far out of view\n"
                ),
            ),
            _framed(turns[1], "Check what else touches it, then write it up."),
            Message(
                role="assistant",
                content=(ToolUseBlock(id="call-2", name="grep", input={"pattern": "scrollTop"}),),
            ),
            Message(
                role="user",
                content=(ToolResultBlock(tool_use_id="call-2", content="4 files", activity=True),),
            ),
            Message(
                role="assistant",
                content=(
                    "Four files reference it. The audit is attached.\n\n"
                    "| Surface | Scroll logic | Anchors a turn |\n"
                    "| --- | --- | --- |\n"
                    "| Chat | hand-rolled | no |\n"
                    "| Conversations | none | no |\n"
                    "| Subagent run | none | no |\n\n"
                    "> The pane is part of the log, not of the screen around it.\n\n"
                    "```tsx\nconst watch = new MutationObserver(follow);\n```\n"
                ),
            ),
            _framed(turns[2], "Good. Open a pull request for it."),
            Message(
                role="assistant",
                content="Ready to open it — two things first.",
            ),
        )
