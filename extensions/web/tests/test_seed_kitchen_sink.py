"""The kitchen-sink seed as the portal reads it, and as a re-run leaves the workspace.

The verb writes rows the engine would otherwise write — a settled conversation, its turns, the runs
they spawned, the files they shared — so what it produced is asserted through the portal's own
transcript projection and rail, over a real database and a real blob store. What it destroys is
asserted too: a re-run replaces its own last run and nothing else, and the rows and blobs it drops
leave none behind.
"""

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import click
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from ufo_ext_web.manifest import manifest as web_manifest
from ufo_testsupport.surfaces import UNREACHED_AMBIENT_REPLY, no_member_skills

from ufo.blob import BlobNotFound, FilesystemBlobStore
from ufo.cli import _seed_target
from ufo.db import workspace_tx
from ufo.harness.auth.bearer import mint_token
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ProxyEndpoint
from ufo.host.ext.loader import member_object_registry, skill_registry
from ufo.onboard.seed import (
    CHAT_ROW_PREFIX,
    KITCHEN_SINK_TITLE,
    WEB_EXTENSION,
    KitchenSink,
)
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.ext.context import ScopedStore
from ufo.runtime.hub import InProcessHub
from ufo.runtime.turns.audience import conversation_audience
from ufo.runtime.turns.transcript import transcript_key
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import TerminalFrame
from ufo.serve import _mount_shared_surfaces

TOKEN_SECRET = "web-token-secret"
SESSION_COOKIE = "ufo_session"
MEMBER_EMAIL = "member@example.com"
OTHER_EXTENSION = "todos"
ARTIFACT_PREFIX = "artifacts/"
TRANSCRIPT_PREFIX = "conversations/"


class StubDbos:
    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        raise AssertionError("the seed admits nothing")


async def _seed_workspace() -> tuple[UUID, UUID, UUID]:
    workspace_id, agent_id, member_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="be brief",
                model="claude-opus-4-8",
                is_main=True,
                visibility="workspace",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=MEMBER_EMAIL,
                is_admin=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id, member_id


def _mount(blob: FilesystemBlobStore, tmp_path: Path) -> FastAPI:
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (web_manifest(),),
        CredentialStore(fernet=Fernet(Fernet.generate_key())),
        blob,
        ConversationSandbox(
            carrier=LocalCarrier(),
            backend="local",
            off_cluster=False,
            image_ref=SANDBOX_IMAGE_REF,
            proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
            workspace_root=tmp_path / "workspaces",
        ),
        InProcessHub(),
        StubDbos(),
        "",
        None,
        None,
        ("auto", "claude-opus-4-8"),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        skills=skill_registry((web_manifest(),)),
        member_skill_listing=no_member_skills,
        objects=member_object_registry((web_manifest(),)),
    )
    return app


@dataclass(frozen=True)
class Seeded:
    client: AsyncClient
    blob: FilesystemBlobStore
    workspace_id: UUID
    agent_id: UUID
    member_id: UUID
    headers: dict[str, str]

    async def write(self) -> UUID:
        with ws(self.workspace_id):
            return await KitchenSink(
                blob=self.blob,
                workspace_id=self.workspace_id,
                agent_id=self.agent_id,
                member_id=self.member_id,
                email=MEMBER_EMAIL,
            ).write()

    async def transcript(self, conversation_id: UUID) -> tuple[int, dict]:
        answer = await self.client.get(
            f"/surface/web/agents/{self.agent_id}/transcript?conversation={conversation_id}",
            headers=self.headers,
        )
        return answer.status_code, (answer.json() if answer.status_code == 200 else {})

    async def rail(self) -> list[dict]:
        answer = await self.client.get(
            "/surface/web/objects/conversation?order_by=last_at&order=desc",
            headers=self.headers,
        )
        assert answer.status_code == 200
        return answer.json()["objects"]

    async def chat_rows(self, extension: str = WEB_EXTENSION) -> tuple[str, ...]:
        with ws(self.workspace_id):
            rows = await ScopedStore(extension=extension).list(CHAT_ROW_PREFIX)
        return tuple(key for key, _ in rows)

    async def conversations(self) -> tuple[tuple[UUID, str, str | None], ...]:
        with ws(self.workspace_id):
            async with workspace_tx() as connection:
                rows = (
                    await connection.execute(
                        sa.select(
                            tables.conversation.c.id,
                            tables.conversation.c.surface,
                            tables.conversation.c.title,
                        ).where(tables.conversation.c.workspace_id == self.workspace_id)
                    )
                ).all()
        return tuple((row.id, row.surface, row.title) for row in rows)

    async def turn_ids(self) -> tuple[UUID, ...]:
        with ws(self.workspace_id):
            async with workspace_tx() as connection:
                rows = (
                    await connection.execute(
                        sa.select(tables.turn.c.id).where(
                            tables.turn.c.workspace_id == self.workspace_id
                        )
                    )
                ).scalars()
        return tuple(rows)

    async def artifact_keys(self) -> tuple[str, ...]:
        with ws(self.workspace_id):
            async with workspace_tx() as connection:
                rows = (
                    await connection.execute(
                        sa.select(tables.shared_artifact.c.blob_key).where(
                            tables.shared_artifact.c.workspace_id == self.workspace_id
                        )
                    )
                ).scalars()
        return tuple(rows)

    async def stored(self, prefix: str) -> tuple[str, ...]:
        return tuple(entry.key for entry in await self.blob.list(prefix))


@pytest.fixture
async def seeded(db: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    workspace_id, agent_id, member_id = await _seed_workspace()
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    app = _mount(blob, tmp_path)
    token = mint_token(TOKEN_SECRET, str(workspace_id), MEMBER_EMAIL, timedelta(hours=1))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://web") as client:
        yield Seeded(
            client=client,
            blob=blob,
            workspace_id=workspace_id,
            agent_id=agent_id,
            member_id=member_id,
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )


async def test_the_verb_seeds_the_workspace_named_and_refuses_to_guess(seeded: Seeded) -> None:
    """A hosted deploy serves many workspaces, so a bare `ufoctl seed kitchen-sink` there names the
    count and the option that fixes it instead of writing a demo into an arbitrary tenant; named,
    it seeds exactly that workspace through the same owner-scoped resolution every operator verb
    uses."""
    await _seed_workspace()

    with pytest.raises(click.ClickException, match="serves 2 workspaces"):
        await _seed_target(seeded.blob, "")

    conversation_id = await _seed_target(seeded.blob, str(seeded.workspace_id))
    status, _body = await seeded.transcript(conversation_id)
    assert status == 200


async def test_the_seeded_conversation_reads_back_through_the_portal(seeded: Seeded) -> None:
    """What the verb writes is what the portal draws: the conversation opens through the member's
    own transcript projection with the three exchanges in order, the runs the middle turn spawned
    under it, and the question the last reply still asks; the rail names it."""
    conversation_id = await seeded.write()
    status, body = await seeded.transcript(conversation_id)
    assert status == 200
    said = [message["text"] for message in body["messages"] if message["role"] == "user"]
    assert said == [
        "Where does the chat pane decide to stay at the bottom?",
        "Check what else touches it, then write it up.",
        "Good. Open a pull request for it.",
    ]
    replies = [message for message in body["messages"] if message["role"] == "assistant"]
    assert "MutationObserver" in replies[0]["text"]
    runs = replies[1]["subagents"]
    assert [run["profile"] for run in runs] == ["general_purpose"]
    assert [spawned["profile"] for spawned in runs[0]["subagents"]] == ["deep_research"]
    assert replies[2]["question"]["title"] == "Two things before I open the pull request."
    assert [row["title"] for row in await seeded.rail()] == [KITCHEN_SINK_TITLE]


async def test_a_second_run_replaces_the_first(seeded: Seeded) -> None:
    """The verb is re-runnable: the second run mints a new conversation and drops the first with
    everything hanging off it — its chat row, its turns, and the subagent conversations its turns
    spawned — so an operator reads one run's output and nothing accumulates."""
    first = await seeded.write()
    assert len(await seeded.turn_ids()) == 5
    second = await seeded.write()

    assert second != first
    assert await seeded.chat_rows() == (f"{CHAT_ROW_PREFIX}{second}",)
    assert len(await seeded.turn_ids()) == 5
    surfaces = [surface for _id, surface, _title in await seeded.conversations()]
    assert sorted(surfaces) == ["subagent", "subagent", "web"]
    assert (await seeded.transcript(first))[0] == 404
    assert (await seeded.transcript(second))[0] == 200


async def test_a_conversation_a_member_named_kitchen_sink_survives(seeded: Seeded) -> None:
    """The seed destroys what it opened, never what a member named. A member's own chat titled
    exactly like the demo keeps its conversation, its chat row and its turn across a run."""
    theirs = uuid4()
    turn_id = uuid4()
    with ws(seeded.workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=theirs,
                    workspace_id=seeded.workspace_id,
                    agent_id=seeded.agent_id,
                    surface="web",
                    queue_key=f"{seeded.agent_id}/{MEMBER_EMAIL}/{uuid4().hex}",
                    member_id=seeded.member_id,
                    title=KITCHEN_SINK_TITLE,
                    audience=str(conversation_audience(seeded.member_id)),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=turn_id,
                    workspace_id=seeded.workspace_id,
                    conversation_id=theirs,
                    agent_id=seeded.agent_id,
                    seq=1,
                    status="done",
                    inbound="what does the scroll pane do?",
                    admission_source="member",
                    speaker_member_id=seeded.member_id,
                    terminal=TerminalFrame(status="done", text="It observes mutations.").model_dump(
                        mode="json"
                    ),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        await ScopedStore(extension=WEB_EXTENSION).put(
            f"{CHAT_ROW_PREFIX}{theirs}",
            {"agent_id": str(seeded.agent_id), "email": MEMBER_EMAIL},
        )

    await seeded.write()
    await seeded.write()

    assert theirs in [held for held, _surface, _title in await seeded.conversations()]
    assert f"{CHAT_ROW_PREFIX}{theirs}" in await seeded.chat_rows()
    assert turn_id in await seeded.turn_ids()


async def test_a_run_a_member_spoke_in_survives_a_reseed(seeded: Seeded) -> None:
    """Answering the standing question makes the run the member's: their turn, the spend it priced,
    and the account they connected in it are real workspace history, so the reseed leaves that run
    whole — rows, chat row and blobs — and still writes a fresh one. The turn is written exactly as
    admission founds one on a settled conversation — a `turn` row alone, no arrival queue row."""
    first = await seeded.write()
    answered_turn = uuid4()
    with ws(seeded.workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=answered_turn,
                    workspace_id=seeded.workspace_id,
                    conversation_id=first,
                    agent_id=seeded.agent_id,
                    seq=4,
                    status="done",
                    inbound="main",
                    admission_source="member",
                    speaker_member_id=seeded.member_id,
                    idempotency_key=f"{first}:{uuid4()}:answer:0",
                    terminal=TerminalFrame(status="done", text="Opened it.").model_dump(
                        mode="json"
                    ),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.ledger).values(
                    id=uuid4(),
                    workspace_id=seeded.workspace_id,
                    turn_id=answered_turn,
                    dimension="tokens",
                    amount=512,
                    prompt_tokens=512,
                    input_tokens=512,
                    priced_micro_usd=900,
                    model="claude-opus-4-8",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.connection).values(
                    id=uuid4(),
                    workspace_id=seeded.workspace_id,
                    provider="github",
                    account_id="octocat",
                    host="github.com",
                    owner_member_id=seeded.member_id,
                    conversation_id=first,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )

    second = await seeded.write()

    held = [conversation for conversation, _surface, _title in await seeded.conversations()]
    assert first in held
    assert second in held
    assert answered_turn in await seeded.turn_ids()
    with ws(seeded.workspace_id):
        async with workspace_tx() as connection:
            spend = (
                await connection.execute(
                    sa.select(sa.func.count())
                    .select_from(tables.ledger)
                    .where(tables.ledger.c.turn_id == answered_turn)
                )
            ).scalar_one()
            connected = (
                await connection.execute(
                    sa.select(sa.func.count())
                    .select_from(tables.connection)
                    .where(tables.connection.c.conversation_id == first)
                )
            ).scalar_one()
    assert spend == 1
    assert connected == 1
    assert set(await seeded.chat_rows()) == {
        f"{CHAT_ROW_PREFIX}{first}",
        f"{CHAT_ROW_PREFIX}{second}",
    }
    assert transcript_key(first) in await seeded.stored(TRANSCRIPT_PREFIX)
    assert transcript_key(second) in await seeded.stored(TRANSCRIPT_PREFIX)


async def test_a_run_an_admin_disclosed_survives_a_reseed(seeded: Seeded) -> None:
    """An admin acknowledging the seeded member's private transcript writes a disclosure record
    naming the conversation and nothing else. That record is audit history, so the run it names
    stands across a reseed — with its chat row — and a fresh run is still written."""
    first = await seeded.write()
    admin_id = uuid4()
    with ws(seeded.workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.member).values(
                    id=admin_id,
                    workspace_id=seeded.workspace_id,
                    email="admin@example.com",
                    is_admin=True,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.transcript_access).values(
                    id=uuid4(),
                    workspace_id=seeded.workspace_id,
                    conversation_id=first,
                    reader_member_id=admin_id,
                    subject_member_id=seeded.member_id,
                    created_at=sa.func.now(),
                )
            )

    second = await seeded.write()

    held = [conversation for conversation, _surface, _title in await seeded.conversations()]
    assert first in held
    assert second in held
    assert set(await seeded.chat_rows()) == {
        f"{CHAT_ROW_PREFIX}{first}",
        f"{CHAT_ROW_PREFIX}{second}",
    }
    assert transcript_key(first) in await seeded.stored(TRANSCRIPT_PREFIX)


async def test_the_chat_row_drop_is_the_web_extensions_alone(seeded: Seeded) -> None:
    """`ext_store` is keyed by extension as well as key, so another extension holding the same key
    owns a different row: the seed drops its own surface's chat row and leaves that one."""
    first = await seeded.write()
    with ws(seeded.workspace_id):
        await ScopedStore(extension=OTHER_EXTENSION).put(
            f"{CHAT_ROW_PREFIX}{first}", {"kept": True}
        )

    await seeded.write()

    assert f"{CHAT_ROW_PREFIX}{first}" not in await seeded.chat_rows()
    assert await seeded.chat_rows(OTHER_EXTENSION) == (f"{CHAT_ROW_PREFIX}{first}",)


async def test_a_replaced_run_leaves_no_blob_behind(seeded: Seeded) -> None:
    """A shared file is a row naming a blob, and the transcript is a blob alone. Dropping the rows
    drops both, so a workspace seeded twice holds one run's blobs, not two."""
    first = await seeded.write()
    dropped = await seeded.artifact_keys()
    assert len(dropped) == 2

    second = await seeded.write()

    kept = await seeded.artifact_keys()
    assert len(kept) == 2
    assert not set(kept) & set(dropped)
    assert await seeded.stored(ARTIFACT_PREFIX) == tuple(sorted(kept))
    assert transcript_key(first) not in await seeded.stored(TRANSCRIPT_PREFIX)
    assert transcript_key(second) in await seeded.stored(TRANSCRIPT_PREFIX)
    for key in dropped:
        with pytest.raises(BlobNotFound):
            await seeded.blob.get(key)
