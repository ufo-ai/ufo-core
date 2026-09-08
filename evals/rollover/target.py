"""The rollover suite's live collaborator: the model client and blob store the artifact layer rolls
over with, and the materializer that swaps a full-scale fixture window in as a conversation's
durable transcript so a probe turn loads it and crosses the boundary for real."""

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from ufo_ext_context_rollover.rollover import (
    RECOVERY_RESERVE_TOKENS,
    ROLLOVER_BUFFER_TOKENS,
    ContextRollover,
)
from ufo_ext_context_rollover.tools import history_filename

from ufo.blob import WorkspaceBlobStore
from ufo.harness.models.interface import Message
from ufo.harness.models.registry import ServingModel
from ufo.runtime.turns.transcript import Conversation, encode, transcript_key

WORKSPACE_PREFIX = "/workspace/"


@dataclass(frozen=True)
class FileJournal:
    """The history file on the local filesystem — what the artifact leaves roll over into, since
    they run no sandbox, and what the grader reads back line by line."""

    path: Path

    async def display_path(self) -> str:
        return str(self.path)

    async def append(self, lines: tuple[str, ...], after: int) -> tuple[int, int]:
        held = await asyncio.to_thread(self._read_lines)
        kept = (*held[:after], *lines)
        await asyncio.to_thread(self.path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(
            self.path.write_text, "".join(f"{line}\n" for line in kept), "utf-8"
        )
        return len(held), len(kept)

    async def text(self, first: int, last: int) -> str:
        """The rendered text of lines `first`..`last`, inclusive, joined — the surface the grader
        holds a fact against."""
        held = await asyncio.to_thread(self._read_lines)
        return "\n".join(json.loads(line)["text"] for line in held[max(first, 1) - 1 : last])

    async def lines(self) -> int:
        return len(await asyncio.to_thread(self._read_lines))

    def _read_lines(self) -> list[str]:
        if not self.path.is_file():
            return []
        return self.path.read_text("utf-8").splitlines()


@dataclass(frozen=True)
class RolloverTarget:
    """`serving` is the model the live leaves' probe turns run on — the same holder `queue.py` hands
    the engine's `ContextRollover`, whose spec declares the window the line derives from. Artifact
    leaves override the line to the snapshot's own scale, so they never consult it; behavior and
    real leaves cannot, and their windows have to clear the model's real line to roll over at
    all."""

    serving: ServingModel
    blob: WorkspaceBlobStore
    workspace_root: Path

    @property
    def context_window(self) -> int:
        return self.serving.spec.context_window

    @property
    def live_trigger_tokens(self) -> int:
        """The line a probe turn will actually be measured against — `ContextRollover`'s own
        derivation with `trigger_tokens` unset, mirrored so a mismatch is reportable before a turn
        is spent rather than surfacing as a rollover that never fired."""
        return self.context_window - RECOVERY_RESERVE_TOKENS - ROLLOVER_BUFFER_TOKENS

    def rollover(self, conversation_id: UUID, trigger_tokens: int | None = None) -> ContextRollover:
        return ContextRollover(
            serving=self.serving,
            blob=self.blob,
            conversation_id=conversation_id,
            trigger_tokens=trigger_tokens,
            journal=self.journal(conversation_id),
        )

    def journal(self, conversation_id: UUID) -> FileJournal:
        """The history file an artifact leaf's boundaries append to, under the conversation's
        workspace directory so the grader reads the same lines the record names."""
        return FileJournal(
            self.workspace_root / str(conversation_id) / history_filename(conversation_id)
        )

    async def materialize(
        self, conversation_id: UUID, messages: tuple[Message, ...], files: dict[str, str]
    ) -> None:
        """Replace the conversation's transcript with the fixture window at the seed turn's seq, so
        the next admitted turn loads it as prior context, and land each offloaded body under the
        conversation workspace where the sandbox file tools can re-read it."""
        await self.blob.put(
            transcript_key(conversation_id), encode(Conversation(seq=1, messages=messages))
        )
        for path, body in files.items():
            if not path.startswith(WORKSPACE_PREFIX):
                raise ValueError(f"offloaded file {path!r} is outside the workspace")
            target = (
                self.workspace_root / str(conversation_id) / path.removeprefix(WORKSPACE_PREFIX)
            )
            await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(target.write_text, body)
