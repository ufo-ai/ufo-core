import asyncio
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import grpc
import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_imessage.cloud as cloud
import ufo_ext_imessage.surface as surface_module
from cryptography.fernet import Fernet
from pydantic import ValidationError
from ufo_ext_imessage.cloud import (
    SpectrumCloudError,
    SpectrumProject,
)
from ufo_ext_imessage.local_line import LOCAL_LINE, LocalLine
from ufo_ext_imessage.provider import (
    InboundMessage,
    MessageAttachment,
    ProviderNotConfigured,
)
from ufo_ext_imessage.surface import (
    CODE_EXPIRED_TEXT,
    CODE_UNKNOWN_TEXT,
    CONNECTED_TEXT,
    CONTACT_CARD_FILENAME,
    IMESSAGE_EXTENSION,
    OPT_IN_CODE_ALPHABET,
    OPT_IN_CODE_LENGTH,
    OPT_OUT_REPLIES,
    SURFACE_IMESSAGE,
    ImessageSurface,
    PendingClaim,
    claim_key,
    contact_card,
    conversation_from_queue,
    queue_key,
)
from ufo_ext_imessage.tools import (
    PHONE_CLAIM_MINUTES,
    ImessageConnect,
    ImessageConnectInput,
    _display_phone,
    opt_in_link,
)
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
    EMPTY_TURN_STEPS,
    UNREACHED_AMBIENT_REPLY,
    UNREACHED_STOPPER,
    no_member_skills,
)

from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.harness.models.interface import ModelRequest
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ProxyEndpoint
from ufo.runtime.access.connectors import ConnectorRegistry
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.ext.context import ScopedStore, context_for
from ufo.runtime.ext.surface import (
    SharedArtifact,
    SurfaceAuth,
    SurfaceContext,
    SurfaceListenerContext,
    Writeback,
    member_message_text,
)
from ufo.runtime.hub import InProcessHub
from ufo.runtime.surfaces.admission import Admission, MemberAdmission
from ufo.runtime.surfaces.hub_tail import HubTailer
from ufo.runtime.tools.context import SpeakerRequired, ToolContext
from ufo.runtime.turns.audience import conversation_audience
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import (
    Agent,
    ConnectRequest,
    TerminalFrame,
    Turn,
)

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

CLAIM_CODE = "ABC234"


@dataclass
class StubDbos:
    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, _options: object, _workspace_id: str, turn_id: str) -> None:
        self.enqueued.append(turn_id)


@dataclass
class RecordingProvider:
    lines: list[tuple[str, str]] = field(default_factory=list)
    sends: list[tuple[str, str, str]] = field(default_factory=list)
    delivered: list[tuple[str, str]] = field(default_factory=list)
    attachments: list[tuple[str, str, bytes, str]] = field(default_factory=list)
    sent_keys: set[str] = field(default_factory=set)
    fail_once: set[str] = field(default_factory=set)
    downloads: dict[str, tuple[bytes, ...] | Exception] = field(default_factory=dict)

    @property
    def installation_id(self) -> str:
        return "project:project"

    async def assign_line(self, phone_number: str, idempotency_key: str) -> str:
        self.lines.append((phone_number, idempotency_key))
        return "+14085550123"

    async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str:
        self.sends.append((conversation_id, text, idempotency_key))
        if idempotency_key in self.fail_once:
            self.fail_once.remove(idempotency_key)
            raise httpx.ConnectError("send failed")
        if idempotency_key not in self.sent_keys:
            self.sent_keys.add(idempotency_key)
            self.delivered.append((conversation_id, text))
        return f"sent-{len(self.delivered)}"

    async def send_attachment(
        self, conversation_id: str, filename: str, data: bytes, idempotency_key: str
    ) -> str:
        self.attachments.append((conversation_id, filename, data, idempotency_key))
        return f"attachment-{len(self.attachments)}"

    async def download_attachment(self, attachment_id: str) -> AsyncGenerator[bytes, None]:
        value = self.downloads[attachment_id]
        if isinstance(value, Exception):
            raise value
        for chunk in value:
            yield chunk

    def external_error(self, error: Exception) -> bool:
        return isinstance(error, httpx.HTTPError)


@dataclass
class DecisionModel:
    answer: str
    model: str = "decision"
    requests: list[ModelRequest] = field(default_factory=list)

    async def complete(self, request: ModelRequest) -> str:
        self.requests.append(request)
        return self.answer


async def _seed() -> tuple[UUID, UUID]:
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
                prompt="Be brief.",
                model="claude-opus-4-8",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="member@example.com",
                is_admin=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, member_id


def _sandboxes(root: Path) -> ConversationSandbox:
    return ConversationSandbox(
        carrier=LocalCarrier(),
        backend="local",
        off_cluster=False,
        image_ref=SANDBOX_IMAGE_REF,
        proxy=ProxyEndpoint(port=1, ca_cert="test-ca"),
        workspace_root=root,
    )


def _context(workspace_id: UUID, tmp_path: Path, dbos: StubDbos) -> SurfaceContext:
    return SurfaceContext(
        workspace_id=workspace_id,
        surface=SURFACE_IMESSAGE,
        blob=WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path)),
        _sandboxes=_sandboxes(tmp_path / "workspaces"),
        _admitter=MemberAdmission(
            workspace_id=workspace_id,
            admission=Admission(dbos=dbos, durable_surfaces=frozenset({SURFACE_IMESSAGE})),
        ),
        _tailer=HubTailer(hub=InProcessHub()),
        _stopper=UNREACHED_STOPPER,
        _turn_steps=EMPTY_TURN_STEPS,
        _credentials=CredentialStore(fernet=Fernet(Fernet.generate_key())),
        _artifact_token_secret="artifact-secret",
        _public_base_url="https://ufo.example.test",
        _home_surface="web",
        _ingress_public_url=None,
        _deploy_sandbox_internet=False,
        _models=("auto", "claude-opus-4-8"),
        _connectors=ConnectorRegistry(entries={}),
        _skills=EMPTY_SKILL_REGISTRY,
        _member_skill_listing=no_member_skills,
        _declared_slots=(),
        _ambient_reply=UNREACHED_AMBIENT_REPLY,
    )


def _message(
    phone: str,
    text: str = "Please summarize this.",
    *,
    message_id: str = "message-1",
    conversation_id: str | None = None,
    direct: bool = True,
    attachments: tuple[MessageAttachment, ...] = (),
) -> InboundMessage:
    return InboundMessage(
        id=message_id,
        conversation_id=conversation_id or f"iMessage;-;{phone}",
        sender=phone,
        text=text,
        attachments=attachments,
        direct=direct,
    )


async def _tool_context(
    workspace_id: UUID, member_id: UUID | None, root: Path, public_base_url: str | None = None
) -> ToolContext:
    """A tool call inside a real turn: the connect tool shares the opt-in QR as an artifact of the
    turn it runs in, which is a row like any other."""
    turn = Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=uuid4(),
        agent_id=await _agent_of(workspace_id),
        seq=1,
        status="running",
        inbound="Connect my phone.",
        created_at=datetime.now(UTC),
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=turn.conversation_id,
                workspace_id=workspace_id,
                agent_id=turn.agent_id,
                surface="web",
                queue_key=str(turn.conversation_id),
                member_id=member_id,
                audience=str(conversation_audience(member_id)),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn.id,
                workspace_id=workspace_id,
                conversation_id=turn.conversation_id,
                agent_id=turn.agent_id,
                seq=turn.seq,
                status=turn.status,
                inbound=turn.inbound,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return ToolContext(
        sandbox=None,
        blob=WorkspaceBlobStore(backend=FilesystemBlobStore(root=root)),
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=member_id,
        audience=conversation_audience(member_id),
        artifact_token_secret="",
        ext=context_for(
            IMESSAGE_EXTENSION,
            frozenset(),
            surfaces=frozenset({SURFACE_IMESSAGE}),
            addressed_surfaces=frozenset({SURFACE_IMESSAGE}),
            public_base_url=public_base_url,
        ),
    )


async def _owned() -> bool:
    return True


async def _agent_of(workspace_id: UUID) -> UUID:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.agent.c.id).where(tables.agent.c.workspace_id == workspace_id)
            )
        ).scalar_one()


async def _shared_artifacts() -> list[tuple[str, str, str | None, str]]:
    async with workspace_tx() as connection:
        return [
            (row.filename, row.media_type, row.subject, row.blob_key)
            for row in await connection.execute(
                sa.select(
                    tables.shared_artifact.c.filename,
                    tables.shared_artifact.c.media_type,
                    tables.shared_artifact.c.subject,
                    tables.shared_artifact.c.blob_key,
                ).order_by(tables.shared_artifact.c.created_at)
            )
        ]


async def _claim(
    workspace_id: UUID,
    member_id: UUID,
    phone: str,
    *,
    expires_in: timedelta = timedelta(minutes=PHONE_CLAIM_MINUTES),
    code: str = CLAIM_CODE,
    line: str = "+14085550123",
) -> None:
    """One member's unproved claim on a phone: the fleet row that routes it, and the store row
    holding the code and the line they were given."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.surface_address).values(
                surface=SURFACE_IMESSAGE,
                address=phone,
                workspace_id=workspace_id,
                member_id=member_id,
                claim_expires_at=datetime.now(UTC) + expires_in,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(workspace_id):
        await ScopedStore(IMESSAGE_EXTENSION).put(
            claim_key(member_id, phone),
            PendingClaim(assigned_phone_number=line, opt_in_code=code).model_dump(mode="json"),
        )


async def _linked(workspace_id: UUID, member_id: UUID, phone: str) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.surface_address).values(
                surface=SURFACE_IMESSAGE,
                address=phone,
                workspace_id=workspace_id,
                member_id=member_id,
                proved_by="earlier-message",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


async def _claimed_phones() -> list[tuple[str, UUID, str | None]]:
    async with workspace_tx() as connection:
        return [
            (row.address, row.member_id, row.proved_by)
            for row in await connection.execute(
                sa.select(
                    tables.surface_address.c.address,
                    tables.surface_address.c.member_id,
                    tables.surface_address.c.proved_by,
                ).where(tables.surface_address.c.surface == SURFACE_IMESSAGE)
            )
        ]


async def _member(workspace_id: UUID, email: str) -> UUID:
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=email,
                is_admin=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return member_id


async def test_a_project_change_requires_an_admin_then_rebinds(db: None, tmp_path: Path) -> None:
    workspace_id, admin_id = await _seed()
    member_id = await _member(workspace_id, "other@example.com")

    class NewProjectProvider(RecordingProvider):
        @property
        def installation_id(self) -> str:
            return "project:new"

    provider = NewProjectProvider()
    admin = await _tool_context(workspace_id, admin_id, tmp_path)
    member = await _tool_context(workspace_id, member_id, tmp_path)
    with ws(workspace_id):
        assert admin.ext is not None
        await admin.ext.installations.bind(SURFACE_IMESSAGE, "project:old")
        refused = await ImessageConnect(provider=lambda _base: provider).run(
            member,
            ImessageConnectInput(phone_number="+14155550123"),
        )
        before = await admin.ext.installations.installation(SURFACE_IMESSAGE)
        connected = await ImessageConnect(provider=lambda _base: provider).run(
            admin,
            ImessageConnectInput(phone_number="+14155550123"),
        )
        after = await admin.ext.installations.installation(SURFACE_IMESSAGE)
    assert json.loads(refused.content[0].text) == {
        "state": "not_connected",
        "instruction": "Ask a workspace admin to connect the iMessage provider.",
    }
    assert before == "project:old"
    assert json.loads(connected.content[0].text)["state"] == "pending"
    assert after == "project:new"


async def test_connect_asks_for_the_member_before_it_asks_for_an_admin(
    db: None, tmp_path: Path
) -> None:
    """A call nobody is bound to reads the repair it can make — name the member — instead of a
    provider refusal it cannot act on."""
    workspace_id, _admin_id = await _seed()
    speakerless = await _tool_context(workspace_id, None, tmp_path)
    with ws(workspace_id), pytest.raises(SpeakerRequired, match="requested_by"):
        await ImessageConnect(provider=lambda _base: RecordingProvider()).run(
            speakerless,
            ImessageConnectInput(phone_number="+14155550123"),
        )


def test_spectrum_configured_needs_both_halves_of_the_project_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (cloud.SPECTRUM_PROJECT_ID_ENV, cloud.SPECTRUM_PROJECT_SECRET_ENV):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"UFO_{name}", raising=False)
    assert cloud.spectrum_configured() is False
    monkeypatch.setenv(f"UFO_{cloud.SPECTRUM_PROJECT_ID_ENV}", "project")
    assert cloud.spectrum_configured() is False
    monkeypatch.setenv(cloud.SPECTRUM_PROJECT_SECRET_ENV, "secret")
    assert cloud.spectrum_configured() is True


LOCAL_BASE = "http://ufo-3.localhost:18280"
PUBLIC_BASE = "https://ufo.example.test"


def _unset_spectrum_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (cloud.SPECTRUM_PROJECT_ID_ENV, cloud.SPECTRUM_PROJECT_SECRET_ENV):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"UFO_{name}", raising=False)


def test_line_provider_is_spectrum_then_the_local_line_then_a_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _unset_spectrum_pair(monkeypatch)
    for base in (PUBLIC_BASE, None):
        assert cloud.imessage_offered(base) is False
        with pytest.raises(ProviderNotConfigured, match="SPECTRUM_PROJECT_ID"):
            cloud.line_provider(base)
    assert cloud.imessage_offered(LOCAL_BASE) is True
    assert isinstance(cloud.line_provider(LOCAL_BASE), LocalLine)
    monkeypatch.setenv(f"UFO_{cloud.SPECTRUM_PROJECT_ID_ENV}", "project")
    monkeypatch.setenv(f"UFO_{cloud.SPECTRUM_PROJECT_SECRET_ENV}", "secret")
    cloud.spectrum_project.cache_clear()
    for base in (PUBLIC_BASE, LOCAL_BASE, None):
        assert cloud.imessage_offered(base) is True
        assert isinstance(cloud.line_provider(base), SpectrumProject)
    cloud.spectrum_project.cache_clear()


async def test_the_local_line_connects_a_phone_on_a_dev_deploy(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _unset_spectrum_pair(monkeypatch)
    workspace_id, member_id = await _seed()
    phone = "+14155550123"
    tool_context = await _tool_context(workspace_id, member_id, tmp_path, LOCAL_BASE)
    with ws(workspace_id):
        assert tool_context.ext is not None
        result = await ImessageConnect(provider=cloud.line_provider).run(
            tool_context, ImessageConnectInput(phone_number=phone)
        )
        bound = await tool_context.ext.installations.installation(SURFACE_IMESSAGE)
        stored = await tool_context.ext.store.get(claim_key(member_id, phone))
        shared = await _shared_artifacts()
    claim = PendingClaim.model_validate(stored)
    assert bound == LocalLine().installation_id == "local-line"
    assert json.loads(result.content[0].text) == {
        "state": "pending",
        "instruction": (
            f'Text "UFO {claim.opt_in_code}" to (555) 555-0100 from that phone within '
            f"{PHONE_CLAIM_MINUTES} minutes."
        ),
        "assigned_phone_number": LOCAL_LINE,
        "opt_in_text": f"UFO {claim.opt_in_code}",
        "opt_in_link": f"sms:{LOCAL_LINE}?&body=UFO%20{claim.opt_in_code}",
    }
    assert await _claimed_phones() == [(phone, member_id, None)]
    assert [(name, media, subject) for name, media, subject, _key in shared] == [
        ("opt-in.png", "image/png", "Scan with that phone to open the message.")
    ]


async def test_the_local_line_listens_forever_and_delivers_nothing() -> None:
    line = LocalLine()
    assert [event async for event in line.catch_up(None)] == []
    ready = asyncio.Event()

    async def first_frame() -> None:
        async for _frame in line.subscribe(ready):
            raise AssertionError("the local line spoke")

    task = asyncio.create_task(first_frame())
    await asyncio.wait_for(ready.wait(), timeout=1)
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    with pytest.raises(RuntimeError, match="delivers nothing"):
        await line.send_text("chat", "hi", "key")
    with pytest.raises(RuntimeError, match="delivers nothing"):
        await line.send_attachment("chat", "a.txt", b"a", "key")
    with pytest.raises(RuntimeError, match="delivers nothing"):
        async for _chunk in line.download_attachment("attachment"):
            raise AssertionError("the local line delivered")
    await line.invalidate()
    assert line.invalid_cursor(RuntimeError()) is False
    assert line.external_error(RuntimeError()) is False
    assert line.error_code(RuntimeError()) == "local_line"


def test_phone_and_queue_boundaries() -> None:
    for stated in (
        "5594259991",
        "(559) 425-9991",
        "559-425-9991",
        "559.425.9991",
        "559/425/9991",
        "559 _ 425 _ 9991",
        "+1 559 425 9991",
        "+1 (559) 425-9991",
        "1.559.425.9991",
    ):
        assert ImessageConnectInput(phone_number=stated).phone_number == "+15594259991"
    queue = queue_key("iMessage;-;+14155550123", direct=True)
    assert conversation_from_queue(queue).id == "iMessage;-;+14155550123"
    assert conversation_from_queue(queue).direct
    with pytest.raises(ValueError, match="10-digit US number"):
        ImessageConnectInput(phone_number="425-9991")
    with pytest.raises(ValueError, match="queue key"):
        conversation_from_queue('["other","chat"]')


def test_phone_display_uses_us_format() -> None:
    assert _display_phone("+14085550123") == "(408) 555-0123"


def test_connect_input_refuses_an_extra_key() -> None:
    with pytest.raises(ValidationError, match="surprise"):
        ImessageConnectInput.model_validate({"phone_number": "5594259991", "surprise": "x"})


def test_a_phone_that_states_no_readable_number_is_refused_not_rewritten() -> None:
    for stated in (
        "0612345678",
        "1234567890",
        "07911123456",
        "12 34 56 78",
        "+0123456789",
        "+1",
        "+10000000000",
        "+11415555012",
        "+4155550123456789",
        "+44 7911 123456",
        "+44 (0) 7911 123456",
        "+44 07911 123456",
        "(+45) 12 34 56 78",
        "call me",
    ):
        with pytest.raises(ValueError, match="10-digit US number"):
            ImessageConnectInput(phone_number=stated)


async def test_missing_provider_keeps_listener_inactive() -> None:
    def missing_provider(_base: str | None):
        raise ProviderNotConfigured("Set provider keys")

    task = asyncio.create_task(
        ImessageSurface(provider=missing_provider).listen(SimpleNamespace(public_base_url=None))
    )
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_direct_writeback_mints_the_requesting_members_connect_url() -> None:
    sent: list[str] = []
    requester = uuid4()

    class Provider:
        async def send_text(self, _conversation_id: str, text: str, _idempotency_key: str) -> str:
            sent.append(text)
            return "message"

    class Context:
        public_base_url = "https://ufo.example.test"

        async def connect_url(self, _turn_id: UUID, member_id: UUID) -> str:
            assert member_id == requester
            return "https://ufo.example.test/connect"

        def home_url(self) -> str:
            return "https://ufo.example.test"

    provider = Provider()
    surface = ImessageSurface(provider=lambda _base: provider)
    writeback = Writeback(
        turn_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        queue_key=queue_key("direct-chat", direct=True),
        terminal=TerminalFrame(
            status="done",
            connect_request=ConnectRequest(provider="github", requester_member_id=requester),
        ),
        artifacts=(),
    )

    assert await surface.post(Context(), writeback) == "message"
    assert sent == ["https://ufo.example.test/connect"]


async def test_a_file_the_reply_carried_is_a_link_in_the_reply_and_no_attachment() -> None:
    """A `details` file rides the reply as a link the way an over-cap file does, and `attach` sends
    only the files the turn shared."""
    sent: list[str] = []
    attached: list[str] = []

    class Provider:
        async def send_text(self, _conversation_id: str, text: str, _idempotency_key: str) -> str:
            sent.append(text)
            return "message"

        async def send_attachment(
            self, _conversation_id: str, filename: str, _data: bytes, _idempotency_key: str
        ) -> str:
            attached.append(filename)
            return "attachment"

    class Blob:
        async def get_stream(self, _blob_key: str) -> AsyncGenerator[bytes, None]:
            yield b"csv"

    class Context:
        public_base_url = "https://ufo.example.test"
        blob = Blob()

        def artifact_link(self, artifact: SharedArtifact) -> str:
            return f"https://ufo.example.test/artifacts/{artifact.filename}"

        async def report_url(self, conversation_id: UUID, artifact: SharedArtifact) -> str:
            return f"https://ufo.example.test/surface/web#/c/{conversation_id}?report={artifact.id}"

        def home_url(self) -> str:
            return "https://ufo.example.test"

    provider = Provider()
    surface = ImessageSurface(provider=lambda _base: provider)
    report_id = uuid4()
    writeback = Writeback(
        turn_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        queue_key=queue_key("direct-chat", direct=True),
        terminal=TerminalFrame(status="done", text="The answer."),
        artifacts=(
            SharedArtifact(
                id=report_id,
                blob_key="artifacts/a/plan.md",
                filename="plan.md",
                subject=None,
                media_type="text/markdown",
                size_bytes=3,
                role="details",
            ),
            SharedArtifact(
                id=uuid4(),
                blob_key="artifacts/b/data.csv",
                filename="data.csv",
                subject=None,
                media_type="text/csv",
                size_bytes=3,
                role="file",
            ),
        ),
    )

    assert await surface.post(Context(), writeback) == "message"
    await surface.attach(Context(), writeback, "message")
    assert sent == [
        "The answer.\n\nOpen detailed report: https://ufo.example.test/surface/web#/c/"
        f"{writeback.conversation_id}?report={report_id}"
    ]
    assert attached == ["data.csv"]


async def test_spectrum_invalid_payload_is_an_external_provider_error() -> None:
    project = SpectrumProject(
        project_id="project",
        project_secret="secret",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "succeed": True,
                        "data": {"type": "dedicated", "token": "bearer", "expiresIn": 900},
                    },
                )
            )
        ),
        lock=asyncio.Lock(),
        token_state={},
    )
    try:
        with pytest.raises(SpectrumCloudError) as raised:
            await project.line()
        assert project.external_error(raised.value)
    finally:
        await project.client.aclose()


def test_opt_in_link_carries_the_assigned_line() -> None:
    assert opt_in_link("+14085550123", CLAIM_CODE) == (f"sms:+14085550123?&body=UFO%20{CLAIM_CODE}")


async def test_spectrum_reports_a_refused_send_by_its_provider_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "succeed": True,
                "data": {"type": "shared", "token": "bearer", "expiresIn": 900},
            },
        )

    class Channel:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class RefusingMessageStub:
        def __init__(self, _channel: object) -> None:
            self.SendTextMessage = self.send

        async def send(self, _request: object, **_options: object) -> object:
            raise grpc.aio.AioRpcError(
                grpc.StatusCode.PERMISSION_DENIED,
                grpc.aio.Metadata(),
                grpc.aio.Metadata(),
                details="[spectrum-imessage] Target not allowed for this project",
            )

    monkeypatch.setattr(SpectrumProject, "channel", lambda _self: Channel())
    monkeypatch.setattr(cloud.message_service_pb2_grpc, "MessageServiceStub", RefusingMessageStub)

    project = SpectrumProject(
        project_id="project",
        project_secret="secret",
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        lock=asyncio.Lock(),
        token_state={},
    )
    try:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            await project.send_text("iMessage;-;+14155550123", "Connected.", "send-1")
        assert project.external_error(refusal.value)
        assert project.error_code(refusal.value) == "PERMISSION_DENIED"
    finally:
        await project.client.aclose()


async def test_inbound_opt_in_survives_restart_then_the_next_message_gets_writeback(
    db: None, tmp_path: Path
) -> None:
    workspace_id, member_id = await _seed()
    phone = "+14155550123"
    provider = RecordingProvider()
    tool = ImessageConnect(provider=lambda _base: provider)
    tool_context = await _tool_context(workspace_id, member_id, tmp_path)
    dbos = StubDbos()
    context = _context(workspace_id, tmp_path, dbos)
    args = ImessageConnectInput(phone_number=phone)
    with ws(workspace_id):
        first = await tool.run(tool_context, args)
        second = await tool.run(tool_context, args)
        stored = await ScopedStore(IMESSAGE_EXTENSION).get(claim_key(member_id, phone))
    claim = PendingClaim.model_validate(stored)
    opt_in_text = f"UFO {claim.opt_in_code}"
    assert json.loads(first.content[0].text) == {
        "state": "pending",
        "instruction": (
            f'Text "{opt_in_text}" to (408) 555-0123 from that phone within '
            f"{PHONE_CLAIM_MINUTES} minutes."
        ),
        "assigned_phone_number": "+14085550123",
        "opt_in_text": opt_in_text,
        "opt_in_link": f"sms:+14085550123?&body=UFO%20{claim.opt_in_code}",
    }
    assert second == first
    assert await _claimed_phones() == [(phone, member_id, None)]
    assert claim.assigned_phone_number == "+14085550123"
    assert len(claim.opt_in_code) == OPT_IN_CODE_LENGTH
    assert set(claim.opt_in_code) <= set(OPT_IN_CODE_ALPHABET)
    assert len(provider.lines) == 1
    assert provider.lines[0][0] == phone
    assert provider.lines[0][1].startswith("imessage-line:")
    assert provider.sends == []
    assert provider.delivered == []

    surface = ImessageSurface(provider=lambda _base: provider)
    with ws(workspace_id):
        await surface._admit_message(
            context,
            provider,
            _message(phone, opt_in_text, message_id="opt-in"),
        )
        assert await ScopedStore(IMESSAGE_EXTENSION).get(claim_key(member_id, phone)) is None
        await surface._admit_message(
            context,
            provider,
            _message(phone, message_id="request-1"),
        )
    with ws(workspace_id):
        await surface.post(
            context,
            Writeback(
                turn_id=uuid4(),
                conversation_id=uuid4(),
                agent_id=uuid4(),
                queue_key=queue_key(f"iMessage;-;{phone}", direct=True),
                terminal=TerminalFrame(status="done", text="Agent reply."),
                artifacts=(),
            ),
        )
    async with workspace_tx() as connection:
        conversation = (
            await connection.execute(
                sa.select(
                    tables.conversation.c.id,
                    tables.conversation.c.member_id,
                    tables.conversation.c.queue_key,
                    tables.conversation.c.surface_label,
                ).where(tables.conversation.c.surface == SURFACE_IMESSAGE)
            )
        ).one()
        turns = (
            await connection.execute(
                sa.select(
                    tables.turn.c.inbound,
                    tables.turn.c.speaker_member_id,
                    tables.turn.c.context,
                ).where(tables.turn.c.conversation_id == conversation.id)
            )
        ).all()
        writebacks = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.writeback))
        ).scalar_one()
    assert await _claimed_phones() == [(phone, member_id, "opt-in")]
    assert conversation.member_id == member_id
    assert conversation.queue_key == queue_key(f"iMessage;-;{phone}", direct=True)
    assert conversation.surface_label == "Direct message"
    assert len(turns) == 1
    assert member_message_text(turns[0].inbound) == "Please summarize this."
    assert turns[0].speaker_member_id == member_id
    assert turns[0].context["sender"] == phone
    assert writebacks == 1
    assert provider.delivered == [
        (f"iMessage;-;{phone}", CONNECTED_TEXT),
        (f"iMessage;-;{phone}", "Agent reply."),
    ]
    assert provider.sends[0][2] == "imessage-connected:opt-in"
    assert provider.attachments == [
        (
            f"iMessage;-;{phone}",
            CONTACT_CARD_FILENAME,
            contact_card("+14085550123"),
            "imessage-contact-card:opt-in",
        )
    ]


async def test_an_expired_claim_can_move_to_another_member(db: None, tmp_path: Path) -> None:
    workspace_id, first_member_id = await _seed()
    second_member_id = await _member(workspace_id, "second@example.com")
    phone = "+14155550123"
    provider = RecordingProvider()
    tool_context = await _tool_context(workspace_id, second_member_id, tmp_path)
    await _claim(workspace_id, first_member_id, phone, expires_in=timedelta(minutes=-1))
    with ws(workspace_id):
        assert tool_context.ext is not None
        await tool_context.ext.installations.bind(SURFACE_IMESSAGE, provider.installation_id)
        result = await ImessageConnect(provider=lambda _base: provider).run(
            tool_context,
            ImessageConnectInput(phone_number=phone),
        )
        stored = await tool_context.ext.store.get(claim_key(second_member_id, phone))
    replaced = PendingClaim.model_validate(stored)
    opt_in_text = f"UFO {replaced.opt_in_code}"
    assert json.loads(result.content[0].text) == {
        "state": "pending",
        "instruction": (
            f'Text "{opt_in_text}" to (408) 555-0123 from that phone within '
            f"{PHONE_CLAIM_MINUTES} minutes."
        ),
        "assigned_phone_number": "+14085550123",
        "opt_in_text": opt_in_text,
        "opt_in_link": f"sms:+14085550123?&body=UFO%20{replaced.opt_in_code}",
    }
    assert await _claimed_phones() == [(phone, second_member_id, None)]
    assert replaced.opt_in_code != CLAIM_CODE
    assert len(provider.lines) == 1
    assert provider.lines[0][0] == phone
    assert provider.lines[0][1].startswith("imessage-line:")
    assert provider.sends == []


async def test_connect_refuses_a_phone_another_member_is_connecting(
    db: None, tmp_path: Path
) -> None:
    workspace_id, first_member_id = await _seed()
    second_member_id = await _member(workspace_id, "second@example.com")
    phone = "+14155550123"
    provider = RecordingProvider()
    tool_context = await _tool_context(workspace_id, second_member_id, tmp_path)
    await _claim(workspace_id, first_member_id, phone)
    with ws(workspace_id):
        assert tool_context.ext is not None
        await tool_context.ext.installations.bind(SURFACE_IMESSAGE, provider.installation_id)
        result = await ImessageConnect(provider=lambda _base: provider).run(
            tool_context,
            ImessageConnectInput(phone_number=phone),
        )
        stored = await ScopedStore(IMESSAGE_EXTENSION).get(claim_key(first_member_id, phone))
    assert json.loads(result.content[0].text) == {
        "state": "not_connected",
        "instruction": "That phone belongs to another member.",
    }
    assert PendingClaim.model_validate(stored).opt_in_code == CLAIM_CODE
    assert await _claimed_phones() == [(phone, first_member_id, None)]
    assert provider.lines == []


async def test_an_unreadable_row_is_dropped_and_never_parks_the_surface(
    db: None, tmp_path: Path
) -> None:
    workspace_id, member_id = await _seed()
    phone = "+14155550123"
    provider = RecordingProvider()
    tool_context = await _tool_context(workspace_id, member_id, tmp_path)
    context = _context(workspace_id, tmp_path, StubDbos())
    surface = ImessageSurface(provider=lambda _base: provider)
    unreadable = {"member_id": str(member_id)}
    await _claim(workspace_id, member_id, phone)
    with ws(workspace_id):
        store = ScopedStore(IMESSAGE_EXTENSION)
        await store.put(claim_key(member_id, phone), unreadable)
        await surface._admit_message(
            context, provider, _message(phone, CLAIM_CODE, message_id="unreadable")
        )
        assert tool_context.ext is not None
        await tool_context.ext.installations.bind(SURFACE_IMESSAGE, provider.installation_id)
        result = await ImessageConnect(provider=lambda _base: provider).run(
            tool_context,
            ImessageConnectInput(phone_number=phone),
        )
        stored = await store.get(claim_key(member_id, phone))
    assert json.loads(result.content[0].text)["state"] == "pending"
    assert PendingClaim.model_validate(stored).opt_in_code != CLAIM_CODE
    assert await _claimed_phones() == [(phone, member_id, None)]
    assert provider.sends == []


async def test_a_code_stored_under_another_member_completes_nothing(
    db: None, tmp_path: Path
) -> None:
    """The fleet row names the member the phone reaches, and the code is read from that member's own
    row. A code sitting under anyone else's name is not the claim being proved."""
    workspace_id, member_id = await _seed()
    other_member_id = await _member(workspace_id, "second@example.com")
    phone = "+14155550123"
    provider = RecordingProvider()
    context = _context(workspace_id, tmp_path, StubDbos())
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.surface_address).values(
                surface=SURFACE_IMESSAGE,
                address=phone,
                workspace_id=workspace_id,
                member_id=member_id,
                claim_expires_at=datetime.now(UTC) + timedelta(minutes=PHONE_CLAIM_MINUTES),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(workspace_id):
        store = ScopedStore(IMESSAGE_EXTENSION)
        await store.put(
            claim_key(other_member_id, phone),
            PendingClaim(assigned_phone_number="+14085550123", opt_in_code=CLAIM_CODE).model_dump(
                mode="json"
            ),
        )
        await ImessageSurface(provider=lambda _base: provider)._admit_message(
            context, provider, _message(phone, f"UFO {CLAIM_CODE}", message_id="unmatched-opt-in")
        )
    assert await _claimed_phones() == [(phone, member_id, None)]
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0
    assert provider.sends == []


async def test_a_wrong_code_is_answered_and_an_expired_claim_says_so(
    db: None, tmp_path: Path
) -> None:
    workspace_id, member_id = await _seed()
    phone = "+14155550123"
    provider = RecordingProvider()
    context = _context(workspace_id, tmp_path, StubDbos())
    surface = ImessageSurface(provider=lambda _base: provider)
    await _claim(workspace_id, member_id, phone)
    with ws(workspace_id):
        for text, message_id in (
            ("UFO", "bare-ufo"),
            ("UFO BCD345", "other-code"),
            ("Driving Focus is on.", "automatic-reply"),
        ):
            await surface._admit_message(
                context, provider, _message(phone, text, message_id=message_id)
            )
        assert await ScopedStore(IMESSAGE_EXTENSION).get(claim_key(member_id, phone)) is not None
    assert await _claimed_phones() == [(phone, member_id, None)]
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.surface_address)
            .where(tables.surface_address.c.address == phone)
            .values(claim_expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
    with ws(workspace_id):
        await surface._admit_message(
            context, provider, _message(phone, f"UFO {CLAIM_CODE}", message_id="late")
        )
        assert await ScopedStore(IMESSAGE_EXTENSION).get(claim_key(member_id, phone)) is None
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert await _claimed_phones() == []
    assert turns == 0
    assert provider.delivered == [
        (f"iMessage;-;{phone}", CODE_UNKNOWN_TEXT),
        (f"iMessage;-;{phone}", CODE_UNKNOWN_TEXT),
        (f"iMessage;-;{phone}", CODE_UNKNOWN_TEXT),
        (f"iMessage;-;{phone}", CODE_EXPIRED_TEXT),
    ]
    assert [send[2] for send in provider.sends] == [
        "imessage-code-unknown:bare-ufo",
        "imessage-code-unknown:other-code",
        "imessage-code-unknown:automatic-reply",
        "imessage-code-expired:late",
    ]


async def test_an_opt_out_reply_cancels_the_claim_in_silence(db: None, tmp_path: Path) -> None:
    assert OPT_OUT_REPLIES == frozenset(
        {"cancel", "end", "optout", "quit", "revoke", "stop", "stopall", "unsubscribe"}
    )
    workspace_id, member_id = await _seed()
    phone = "+14155550123"
    provider = RecordingProvider()
    context = _context(workspace_id, tmp_path, StubDbos())
    await _claim(workspace_id, member_id, phone)
    with ws(workspace_id):
        await ImessageSurface(provider=lambda _base: provider)._admit_message(
            context, provider, _message(phone, " STOP ", message_id="stop")
        )
        assert await ScopedStore(IMESSAGE_EXTENSION).get(claim_key(member_id, phone)) is None
    assert await _claimed_phones() == []
    assert provider.sends == []


@pytest.mark.parametrize(
    "message",
    (
        _message("+16505550123", f"UFO {CLAIM_CODE}"),
        _message("+14155550123", f"UFO {CLAIM_CODE}", direct=False),
    ),
    ids=("other-phone", "group"),
)
async def test_only_a_direct_message_from_the_claimed_phone_completes_the_claim(
    db: None, tmp_path: Path, message: InboundMessage
) -> None:
    workspace_id, member_id = await _seed()
    phone = "+14155550123"
    provider = RecordingProvider()
    context = _context(workspace_id, tmp_path, StubDbos())
    surface = ImessageSurface(provider=lambda _base: provider)
    await _claim(workspace_id, member_id, phone)
    with ws(workspace_id):
        await surface._admit_message(context, provider, message)
        stored = await ScopedStore(IMESSAGE_EXTENSION).get(claim_key(member_id, phone))
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert await _claimed_phones() == [(phone, member_id, None)]
    assert turns == 0
    assert provider.sends == []
    assert stored is not None


async def test_bad_attachments_do_not_block_the_inbound_message(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(surface_module, "MAX_ATTACHMENT_BYTES", 4)
    workspace_id, member_id = await _seed()
    phone = "+14155550123"
    provider = RecordingProvider(
        downloads={
            "oversize": (b"123", b"45"),
            "missing": httpx.ConnectError("gone"),
        }
    )
    context = _context(workspace_id, tmp_path, StubDbos())
    surface = ImessageSurface(provider=lambda _base: provider)
    attachments = (
        MessageAttachment(id="oversize", filename="large.txt", size_bytes=0),
        MessageAttachment(id="missing", filename="gone.txt", size_bytes=0),
    )
    await _linked(workspace_id, member_id, phone)
    with ws(workspace_id):
        await surface._admit_message(
            context,
            provider,
            _message(phone, attachments=attachments),
        )
    async with workspace_tx() as connection:
        turn = (await connection.execute(sa.select(tables.turn.c.inbound))).scalar_one()
    assert "Skipped files, too large to download: large.txt" in turn
    assert "Skipped files, unavailable to download: gone.txt" in turn


async def test_connection_acknowledgement_replays_without_admitting_the_opt_in(
    db: None, tmp_path: Path
) -> None:
    """The acknowledgement is sent before the link lands, so a refused send replays the whole proof
    and the provider's idempotency key keeps one message. Once the link lands, the message that
    proved it is recognised on replay and founds no turn."""
    workspace_id, member_id = await _seed()
    phone = "+14155550123"
    message = _message(phone, f"UFO {CLAIM_CODE}", message_id="opt-in")
    acknowledgement_key = "imessage-connected:opt-in"
    provider = RecordingProvider(fail_once={acknowledgement_key})
    context = _context(workspace_id, tmp_path, StubDbos())
    surface = ImessageSurface(provider=lambda _base: provider)
    await _claim(workspace_id, member_id, phone)
    with ws(workspace_id):
        with pytest.raises(httpx.ConnectError, match="send failed"):
            await surface._admit_message(context, provider, message)
        assert await _claimed_phones() == [(phone, member_id, None)]
        await surface._admit_message(context, provider, message)
        await surface._admit_message(context, provider, message)
        assert await ScopedStore(IMESSAGE_EXTENSION).get(claim_key(member_id, phone)) is None
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert await _claimed_phones() == [(phone, member_id, "opt-in")]
    assert turns == 0
    assert provider.delivered == [(message.conversation_id, CONNECTED_TEXT)]
    assert [key for _, _, key in provider.sends] == [acknowledgement_key] * 2


async def test_a_refused_contact_card_leaves_the_phone_connected(db: None, tmp_path: Path) -> None:
    """The card drops the Report Junk banner; it carries no member traffic. A provider that refuses
    it is logged under its own name, and the stream neither drops nor replays the proof."""
    workspace_id, member_id = await _seed()
    phone = "+14155550123"
    message = _message(phone, f"UFO {CLAIM_CODE}", message_id="opt-in")
    provider = RecordingProvider(fail_once={"imessage-contact-card:opt-in"})
    context = _context(workspace_id, tmp_path, StubDbos())
    await _claim(workspace_id, member_id, phone)
    with ws(workspace_id):
        await ImessageSurface(provider=lambda _base: provider)._admit_message(
            context, provider, message
        )
        await ImessageSurface(provider=lambda _base: provider)._admit_message(
            context, provider, _message(phone, message_id="request-1")
        )
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert await _claimed_phones() == [(phone, member_id, "opt-in")]
    assert turns == 1
    assert provider.delivered == [(message.conversation_id, CONNECTED_TEXT)]


async def test_one_shared_line_serves_every_workspace_and_member(db: None, tmp_path: Path) -> None:
    """The provider is the deploy's, so one project and one line serve the whole fleet. Each
    inbound message reaches the workspace and member its sender's phone names, and the stream
    position belongs to the listener rather than to whichever workspace it last delivered to."""
    first_workspace, first_member = await _seed()
    second_workspace, second_member = await _seed()
    first_colleague = await _member(first_workspace, "colleague@example.com")
    provider = RecordingProvider()
    dbos = StubDbos()
    listener = SurfaceListenerContext(
        surface=SURFACE_IMESSAGE,
        public_base_url=None,
        _auth=SurfaceAuth(
            _credentials=None, _declared=frozenset({SURFACE_IMESSAGE}), _surface=SURFACE_IMESSAGE
        ),
        _context_for=lambda workspace_id, _surface: _context(workspace_id, tmp_path, dbos),
        _owned=_owned,
    )
    phones = {
        "+14155550001": (first_workspace, first_member),
        "+14155550002": (first_workspace, first_colleague),
        "+14155550003": (second_workspace, second_member),
    }
    for phone, (workspace_id, member_id) in phones.items():
        await _linked(workspace_id, member_id, phone)
    surface = ImessageSurface(provider=lambda _base: provider)
    for sequence, phone in enumerate(phones, start=1):
        await surface._process_event(
            listener,
            provider,
            provider.installation_id,
            sequence,
            _message(phone, "Please summarize this.", message_id=f"message-{sequence}"),
        )
    await surface._process_event(
        listener, provider, provider.installation_id, 4, _message("+16505559999")
    )
    async with workspace_tx() as connection:
        turns = [
            (row.workspace_id, row.speaker_member_id)
            for row in await connection.execute(
                sa.select(tables.turn.c.workspace_id, tables.turn.c.speaker_member_id).order_by(
                    tables.turn.c.created_at, tables.turn.c.id
                )
            )
        ]
        cursors = [
            (row.surface, row.installation_id, row.sequence, row.workspace_id)
            for row in await connection.execute(sa.select(tables.surface_stream_cursor))
        ]
    assert sorted(turns) == sorted(phones.values())
    assert cursors == [(SURFACE_IMESSAGE, provider.installation_id, 4, None)]
    assert await listener.cursor(provider.installation_id) == 4
    assert await listener.cursor("project:other") is None


async def test_a_second_workspace_connects_on_the_same_project(db: None, tmp_path: Path) -> None:
    """One project per deploy is not one workspace per deploy: the installation the workspaces share
    routes nobody, so each admin binds it and each member claims their own phone."""
    first_workspace, first_admin = await _seed()
    second_workspace, second_admin = await _seed()
    provider = RecordingProvider()
    tool = ImessageConnect(provider=lambda _base: provider)
    results = []
    for workspace_id, admin_id, phone in (
        (first_workspace, first_admin, "+14155550001"),
        (second_workspace, second_admin, "+14155550002"),
    ):
        with ws(workspace_id):
            results.append(
                json.loads(
                    (
                        await tool.run(
                            await _tool_context(workspace_id, admin_id, tmp_path),
                            ImessageConnectInput(phone_number=phone),
                        )
                    )
                    .content[0]
                    .text
                )["state"]
            )
    async with workspace_tx() as connection:
        bound = [
            (row.workspace_id, row.installation_id, row.routes_ingress)
            for row in await connection.execute(sa.select(tables.surface_installation))
        ]
    assert results == ["pending", "pending"]
    assert sorted(bound) == sorted(
        [
            (first_workspace, provider.installation_id, False),
            (second_workspace, provider.installation_id, False),
        ]
    )


async def test_inbound_message_from_an_unclaimed_phone_is_ignored(db: None, tmp_path: Path) -> None:
    workspace_id, _ = await _seed()
    dbos = StubDbos()
    context = _context(workspace_id, tmp_path, dbos)
    project = SpectrumProject(
        project_id="project",
        project_secret="secret",
        client=httpx.AsyncClient(),
        lock=asyncio.Lock(),
        token_state={},
    )
    surface = ImessageSurface(provider=lambda _base: project)
    try:
        with ws(workspace_id):
            await surface._admit_message(context, project, _message("+14155550123"))
        async with workspace_tx() as connection:
            conversations = (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(tables.conversation)
                )
            ).scalar_one()
            turns = (
                await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
            ).scalar_one()
        assert conversations == 0
        assert turns == 0
        assert dbos.enqueued == []
    finally:
        await project.client.aclose()
