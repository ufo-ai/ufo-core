"""The core seam that keeps a durable per-conversation sandbox handle on the conversation row.

`_open_sandbox` reads the row's stored handle, seeds the carrier's resume path from it, and persists
the returned `<backend>:<id>` — so a serve restart resumes the same sandbox and the reaper reclaims
one a prior process created. Persist-on-create is proven against the real local carrier; the
resume-read (the id core seeds and the write it skips when nothing changed) is asserted through the
conversation row, with a stand-in carrier recording the spec core built for it."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.loop.queue import _open_sandbox
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint, SandboxHandle, SandboxSpec
from ufo.schema import tables
from ufo.schema.records import Turn

PROXY = ProxyEndpoint(port=8080, ca_cert="ca-pem")


async def _conversation(handle: str | None = None) -> tuple[UUID, UUID]:
    workspace_id, conversation_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=None,
                sandbox_handle=handle,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, conversation_id


async def _stored_handle(conversation_id: UUID) -> str | None:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.conversation.c.sandbox_handle).where(
                    tables.conversation.c.id == conversation_id
                )
            )
        ).scalar_one()


def _turn(workspace_id: UUID, conversation_id: UUID) -> Turn:
    return Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        agent_id=uuid4(),
        seq=1,
        status="running",
        inbound="hi",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
    )


@dataclass
class _ResumeRecordingCarrier:
    """Stands in for the carrier to record the SandboxSpec core builds — so the resume_id core
    derives from the row is asserted — and returns a handle whose id the test picks. Every assertion
    is on core's read-and-persist, read back through the conversation row, never this carrier's own
    behavior."""

    container_id: str
    specs: list[SandboxSpec] = field(default_factory=list)

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        self.specs.append(spec)
        return SandboxHandle(conversation_id=spec.conversation_id, container_id=self.container_id)

    async def exec(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("open_sandbox never execs")

    async def export(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("open_sandbox never exports")

    async def destroy(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("open_sandbox never destroys")


async def test_open_sandbox_persists_the_backend_prefixed_handle(db: None, tmp_path: Path) -> None:
    """A fresh create against the real local carrier persists `<backend>:<id>` on the row — the
    durable pointer the reaper and the next process read."""
    workspace_id, conversation_id = await _conversation()
    blob = FilesystemBlobStore(root=tmp_path)

    handle = await _open_sandbox(
        LocalCarrier(), "local", blob, None, PROXY, _turn(workspace_id, conversation_id)
    )

    assert handle.container_id == "local"
    assert await _stored_handle(conversation_id) == "local:local"


async def test_open_sandbox_resumes_from_the_stored_handle_without_rewriting(
    db: None, tmp_path: Path
) -> None:
    """A row that already holds this backend's handle seeds the carrier's resume_id and, since the
    returned id is unchanged, costs no write — resume, not a fresh create."""
    workspace_id, conversation_id = await _conversation(handle="e2b:sbx-1")
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")
    blob = FilesystemBlobStore(root=tmp_path)

    await _open_sandbox(carrier, "e2b", blob, None, PROXY, _turn(workspace_id, conversation_id))

    assert carrier.specs[0].resume_id == "sbx-1"
    assert await _stored_handle(conversation_id) == "e2b:sbx-1"


async def test_open_sandbox_ignores_a_handle_another_backend_wrote_and_overwrites_it(
    db: None, tmp_path: Path
) -> None:
    """A handle another backend wrote is not this carrier's to resume: resume_id is None (create
    fresh) and the fresh id overwrites the row under this backend's prefix."""
    workspace_id, conversation_id = await _conversation(handle="docker:cid-1")
    carrier = _ResumeRecordingCarrier(container_id="sbx-9")
    blob = FilesystemBlobStore(root=tmp_path)

    await _open_sandbox(carrier, "e2b", blob, None, PROXY, _turn(workspace_id, conversation_id))

    assert carrier.specs[0].resume_id is None
    assert await _stored_handle(conversation_id) == "e2b:sbx-9"
