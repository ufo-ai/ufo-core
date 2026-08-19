import asyncio
import hashlib
import json
import threading
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import grpc
import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_imessage.cloud as cloud
import ufo_ext_imessage.surface as surface_module
from cryptography.fernet import Fernet
from ufo_ext_imessage.cloud import (
    SpectrumCloudError,
    SpectrumProject,
    _inbound_message,
)
from ufo_ext_imessage.manifest import manifest
from ufo_ext_imessage.proto.photon.imessage.v1 import message_types_pb2
from ufo_ext_imessage.provider import (
    InboundMessage,
    MessageAttachment,
    ProviderEvent,
    ProviderNotConfigured,
)
from ufo_ext_imessage.surface import (
    CODE_EXPIRED_TEXT,
    CODE_UNKNOWN_TEXT,
    CONFIRMATION_REPLY_PREFIX,
    CONNECTED_TEXT,
    CONTACT_CARD_FILENAME,
    IMESSAGE_EXTENSION,
    OPT_IN_CODE_ALPHABET,
    OPT_IN_CODE_LENGTH,
    OPT_IN_TEXT,
    OPT_OUT_REPLIES,
    SURFACE_IMESSAGE,
    ImessageSurface,
    MessageStreamDisconnected,
    PendingClaim,
    contact_card,
    conversation_from_queue,
    phone_key,
    queue_key,
)
from ufo_ext_imessage.tools import (
    PHONE_CLAIM_MINUTES,
    ImessageConnect,
    ImessageConnectInput,
    opt_in_link,
)
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
    UNREACHED_AMBIENT_REPLY,
    UNREACHED_STOPPER,
    no_user_skills,
)

from ufo.ambient_reply import AmbientReplyClassifier
from ufo.audience import conversation_audience
from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import ScopedStore, context_for
from ufo.ext.surface import SurfaceContext, Writeback, member_message_text
from ufo.hub import InProcessHub
from ufo.models.interface import ModelRequest
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint
from ufo.schema import tables
from ufo.schema.records import (
    Agent,
    AskQuestion,
    AskUserInput,
    ConnectRequest,
    QuestionOption,
    TerminalFrame,
    Turn,
)
from ufo.surfaces.admission import Admission, MemberAdmission
from ufo.surfaces.hub_tail import HubTailer
from ufo.tools.context import ToolContext
from ufo.workspace import ws

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
        _credentials=CredentialStore(fernet=Fernet(Fernet.generate_key())),
        _artifact_token_secret="artifact-secret",
        _public_base_url="https://ufo.example.test",
        _home_surface="web",
        _ingress_public_url=None,
        _deploy_sandbox_internet=False,
        _models=("auto", "claude-opus-4-8"),
        _skills=EMPTY_SKILL_REGISTRY,
        _user_skills=no_user_skills,
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


def _tool_context(workspace_id: UUID, member_id: UUID) -> ToolContext:
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="Connect my phone.",
            created_at=datetime.now(UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=member_id,
        audience=conversation_audience(member_id),
        artifact_token_secret="",
        ext=context_for(
            IMESSAGE_EXTENSION,
            frozenset(),
            surfaces=frozenset({SURFACE_IMESSAGE}),
        ),
    )


async def test_a_project_change_requires_an_admin_then_rebinds(db: None) -> None:
    workspace_id, admin_id = await _seed()
    member_id = uuid4()

    class NewProjectProvider(RecordingProvider):
        @property
        def installation_id(self) -> str:
            return "project:new"

    provider = NewProjectProvider()
    admin = _tool_context(workspace_id, admin_id)
    member = _tool_context(workspace_id, member_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="other@example.com",
                is_admin=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(workspace_id):
        assert admin.ext is not None
        await admin.ext.installations.bind(SURFACE_IMESSAGE, "project:old")
        refused = await ImessageConnect(provider=lambda: provider).run(
            member,
            ImessageConnectInput(phone_number="+14155550123", user_description="Connect my phone."),
        )
        before = await admin.ext.installations.installation(SURFACE_IMESSAGE)
        connected = await ImessageConnect(provider=lambda: provider).run(
            admin,
            ImessageConnectInput(phone_number="+14155550123", user_description="Connect my phone."),
        )
        after = await admin.ext.installations.installation(SURFACE_IMESSAGE)
    assert json.loads(refused.content[0].text) == {
        "state": "not_connected",
        "instruction": "Ask a workspace admin to connect the iMessage provider.",
    }
    assert before == "project:old"
    assert json.loads(connected.content[0].text)["state"] == "pending"
    assert after == "project:new"


def test_manifest_declares_complete_durable_surface() -> None:
    loaded = manifest()
    assert loaded.deploy_keys == ("SPECTRUM_PROJECT_ID", "SPECTRUM_PROJECT_SECRET")
    assert tuple(tool.name for tool in loaded.tools) == ("imessage_connect",)
    surface = loaded.surfaces[0]
    assert surface.listen is not None
    assert surface.post is not None
    assert surface.attach is not None
    assert surface.speak is not None
    assert surface.routes == ()


def test_phone_and_queue_boundaries() -> None:
    parsed = ImessageConnectInput(
        phone_number="+1 415 555 0123", user_description="Connect my phone."
    )
    assert parsed.phone_number == "+14155550123"
    queue = queue_key("iMessage;-;+14155550123", direct=True)
    assert conversation_from_queue(queue).id == "iMessage;-;+14155550123"
    assert conversation_from_queue(queue).direct
    with pytest.raises(ValueError, match=r"E\.164"):
        ImessageConnectInput(phone_number="4155550123", user_description="Connect my phone.")
    with pytest.raises(ValueError, match="queue key"):
        conversation_from_queue('["other","chat"]')


async def test_missing_provider_keeps_listener_inactive() -> None:
    def missing_provider():
        raise ProviderNotConfigured("Set provider keys")

    task = asyncio.create_task(ImessageSurface(provider=missing_provider).listen(object()))
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_group_writeback_does_not_mint_a_connect_url() -> None:
    sent: list[str] = []

    class Provider:
        async def send_text(self, _conversation_id: str, text: str, _idempotency_key: str) -> str:
            sent.append(text)
            return "message"

    class Context:
        async def connect_url(self, _turn_id: UUID, _member_id: UUID) -> str:
            raise AssertionError("group writeback minted a connect URL")

        def home_url(self) -> str:
            return "https://ufo.example.test"

    provider = Provider()
    surface = ImessageSurface(provider=lambda: provider)
    writeback = Writeback(
        turn_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        queue_key=queue_key("group-chat", direct=False),
        terminal=TerminalFrame(
            status="done",
            connect_request=ConnectRequest(provider="github", requester_member_id=uuid4()),
        ),
        artifacts=(),
    )

    assert await surface.post(Context(), writeback) == "message"
    assert sent == ["Continue in a direct message to connect the account."]


async def test_a_question_writeback_states_the_answer_already_settled() -> None:
    """iMessage renders an ask as lines the member answers in words, so an answer the agent read
    off their own words is stated with them — the read-back they correct by replying."""
    sent: list[str] = []

    class Provider:
        async def send_text(self, _conversation_id: str, text: str, _idempotency_key: str) -> str:
            sent.append(text)
            return "message"

    class Context:
        def home_url(self) -> str:
            return "https://ufo.example.test"

    surface = ImessageSurface(provider=lambda: Provider())
    writeback = Writeback(
        turn_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        queue_key=queue_key("direct-chat", direct=True),
        terminal=TerminalFrame(
            status="done",
            text="One thing to settle.",
            question=AskUserInput(
                title="Create new app",
                questions=(
                    AskQuestion(
                        question="Who else uses it?",
                        options=(QuestionOption(label="Just me"), QuestionOption(label="Everyone")),
                        chosen="Just me",
                    ),
                ),
            ),
        ),
        artifacts=(),
    )

    assert await surface.post(Context(), writeback) == "message"
    assert sent == [
        "One thing to settle.\n\n"
        "Create new app\n"
        "Who else uses it?\n"
        "Options: Just me, Everyone\n"
        "Current answer: Just me"
    ]


async def test_direct_writeback_mints_the_requesting_members_connect_url() -> None:
    sent: list[str] = []
    requester = uuid4()

    class Provider:
        async def send_text(self, _conversation_id: str, text: str, _idempotency_key: str) -> str:
            sent.append(text)
            return "message"

    class Context:
        async def connect_url(self, _turn_id: UUID, member_id: UUID) -> str:
            assert member_id == requester
            return "https://ufo.example.test/connect"

        def home_url(self) -> str:
            return "https://ufo.example.test"

    provider = Provider()
    surface = ImessageSurface(provider=lambda: provider)
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


async def test_spectrum_cloud_mints_one_cached_shared_token() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "succeed": True,
                "data": {"type": "shared", "token": "bearer", "expiresIn": 900},
            },
        )

    project = SpectrumProject(
        project_id="project",
        project_secret="secret",
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        lock=asyncio.Lock(),
        token_state={},
    )
    try:
        assert (await project.line()).id == "shared"
        assert (await project.line()).token == "bearer"
        assert len(requests) == 1
        assert requests[0].method == "POST"
        assert requests[0].url.path == "/projects/project/imessage/tokens"
        assert "authorization" in requests[0].headers
    finally:
        await project.client.aclose()


async def test_spectrum_project_uses_one_client_per_event_loop() -> None:
    project = SpectrumProject(
        project_id="project",
        project_secret="secret",
        client=httpx.AsyncClient(),
        lock=asyncio.Lock(),
        token_state={},
    )
    main_state = project._loop()
    foreign_states: list[cloud.SpectrumLoop] = []
    done = asyncio.Event()
    loop = asyncio.get_running_loop()

    def capture() -> None:
        async def read() -> None:
            state = project._loop()
            foreign_states.append(state)
            await state.client.aclose()

        try:
            asyncio.run(read())
        finally:
            loop.call_soon_threadsafe(done.set)

    threading.Thread(target=capture).start()
    await asyncio.wait_for(done.wait(), timeout=5)
    assert len(foreign_states) == 1
    assert foreign_states[0].client is not main_state.client
    assert foreign_states[0].lock is not main_state.lock
    await main_state.client.aclose()


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


def test_spectrum_provider_normalizes_a_photon_message() -> None:
    event = message_types_pb2.MessageChangeEvent(chat_guid="iMessage;-;+14155550123")
    event.actor.address = "+14155550123"
    event.message_received.message.guid = "message-1"
    event.message_received.message.content.text = "Please summarize this."

    assert _inbound_message(event) == _message("+14155550123")


def test_opt_in_link_and_contact_card_carry_the_assigned_line() -> None:
    assert OPT_IN_TEXT == "UFO"
    assert opt_in_link("+14085550123", CLAIM_CODE) == (f"sms:+14085550123?&body=UFO%20{CLAIM_CODE}")
    assert contact_card("+14085550123") == (
        b"BEGIN:VCARD\r\nVERSION:3.0\r\nN:ufo;;;;\r\nFN:ufo\r\n"
        b"TEL;TYPE=CELL:+14085550123\r\nEND:VCARD\r\n"
    )


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


async def test_replay_head_filters_the_buffered_live_overlap() -> None:
    processed: list[int] = []

    class ReplayProvider:
        async def subscribe(self, ready: asyncio.Event):
            ready.set()
            for sequence in (6, 8):
                yield ProviderEvent(sequence=sequence)

    class ReplaySurface(ImessageSurface):
        async def _catch_up(self, _context, _provider, _installation_id, cursor):
            assert cursor == 5
            return 7

        async def _process_event(
            self, _context, _provider, _installation_id, sequence, _message
        ) -> None:
            processed.append(sequence)

    provider = ReplayProvider()
    surface = ReplaySurface(provider=lambda: provider)
    with pytest.raises(MessageStreamDisconnected) as raised:
        await surface._consume_connected(object(), provider, "project:project", 5)
    assert processed == [8]
    assert raised.value.cursor == 8


async def test_spectrum_cloud_registers_an_absent_phone_and_answers_its_assigned_line() -> None:
    requests: list[httpx.Request] = []
    users: list[dict[str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200, json={"succeed": True, "data": {"users": users, "total": len(users)}}
            )
        users.append(
            {
                "id": "user",
                "phoneNumber": "+14155550123",
                "assignedPhoneNumber": "+14085550123",
            }
        )
        return httpx.Response(200, json={"succeed": True, "data": users[0]})

    project = SpectrumProject(
        project_id="project",
        project_secret="secret",
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        lock=asyncio.Lock(),
        token_state={},
    )
    try:
        assert await project.assign_line("+14155550123", "line-1") == "+14085550123"
        assert [request.method for request in requests] == ["GET", "POST"]
        assert requests[1].url.path == "/projects/project/users/"
        assert requests[1].content == b'{"type":"shared","phoneNumber":"+14155550123"}'
        assert requests[1].headers["x-idempotency-key"] == "line-1:user"
        assert await project.assign_line("+14155550123", "line-2") == "+14085550123"
        assert [request.method for request in requests] == ["GET", "POST", "GET"]
    finally:
        await project.client.aclose()


async def test_inbound_opt_in_survives_restart_then_the_next_message_gets_writeback(
    db: None, tmp_path: Path
) -> None:
    workspace_id, member_id = await _seed()
    phone = "+14155550123"
    provider = RecordingProvider()
    tool = ImessageConnect(provider=lambda: provider)
    tool_context = _tool_context(workspace_id, member_id)
    dbos = StubDbos()
    context = _context(workspace_id, tmp_path, dbos)
    args = ImessageConnectInput(phone_number=phone, user_description="Connect my phone.")
    with ws(workspace_id):
        first = await tool.run(tool_context, args)
        second = await tool.run(tool_context, args)
        stored = await ScopedStore(IMESSAGE_EXTENSION).get(phone_key(phone))
    claim = PendingClaim.model_validate(stored)
    opt_in_text = f"UFO {claim.opt_in_code}"
    assert json.loads(first.content[0].text) == {
        "state": "pending",
        "instruction": (
            f'Text "{opt_in_text}" to +14085550123 from that phone within '
            f"{PHONE_CLAIM_MINUTES} minutes. Case, spaces and punctuation do not matter."
        ),
        "assigned_phone_number": "+14085550123",
        "opt_in_text": opt_in_text,
        "opt_in_link": f"sms:+14085550123?&body=UFO%20{claim.opt_in_code}",
    }
    assert second == first
    assert claim.member_id == member_id
    assert claim.phone_number == phone
    assert claim.assigned_phone_number == "+14085550123"
    assert len(claim.opt_in_code) == OPT_IN_CODE_LENGTH
    assert set(claim.opt_in_code) <= set(OPT_IN_CODE_ALPHABET)
    assert len(provider.lines) == 1
    assert provider.lines[0][0] == phone
    assert provider.lines[0][1].startswith("imessage-line:")
    assert provider.sends == []
    assert provider.delivered == []

    surface = ImessageSurface(provider=lambda: provider)
    with ws(workspace_id):
        await surface._admit_message(
            context,
            provider,
            _message(phone, opt_in_text, message_id="opt-in"),
        )
        assert await ScopedStore(IMESSAGE_EXTENSION).get(phone_key(phone)) is None
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
        identity = (
            await connection.execute(
                sa.select(tables.surface_identity.c.member_id).where(
                    tables.surface_identity.c.surface == SURFACE_IMESSAGE,
                    tables.surface_identity.c.external_id == phone,
                )
            )
        ).one()
        conversation = (
            await connection.execute(
                sa.select(
                    tables.conversation.c.member_id,
                    tables.conversation.c.queue_key,
                    tables.conversation.c.surface_label,
                )
            )
        ).one()
        turns = (
            await connection.execute(
                sa.select(
                    tables.turn.c.inbound,
                    tables.turn.c.speaker_member_id,
                    tables.turn.c.context,
                )
            )
        ).all()
        writebacks = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.writeback))
        ).scalar_one()
    assert identity.member_id == member_id
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


async def test_an_expired_claim_can_move_to_another_member(db: None) -> None:
    workspace_id, first_member_id = await _seed()
    second_member_id = uuid4()
    phone = "+14155550123"
    provider = RecordingProvider()
    tool_context = _tool_context(workspace_id, second_member_id)
    claim = PendingClaim(
        member_id=first_member_id,
        phone_number=phone,
        assigned_phone_number="+14085550123",
        opt_in_code=CLAIM_CODE,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=second_member_id,
                workspace_id=workspace_id,
                email="second@example.com",
                is_admin=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(workspace_id):
        assert tool_context.ext is not None
        await tool_context.ext.installations.bind(SURFACE_IMESSAGE, provider.installation_id)
        await tool_context.ext.store.put(phone_key(phone), claim.model_dump(mode="json"))
        result = await ImessageConnect(provider=lambda: provider).run(
            tool_context,
            ImessageConnectInput(phone_number=phone, user_description="Connect my phone."),
        )
        stored = await tool_context.ext.store.get(phone_key(phone))
    replaced = PendingClaim.model_validate(stored)
    opt_in_text = f"UFO {replaced.opt_in_code}"
    assert json.loads(result.content[0].text) == {
        "state": "pending",
        "instruction": (
            f'Text "{opt_in_text}" to +14085550123 from that phone within '
            f"{PHONE_CLAIM_MINUTES} minutes. Case, spaces and punctuation do not matter."
        ),
        "assigned_phone_number": "+14085550123",
        "opt_in_text": opt_in_text,
        "opt_in_link": f"sms:+14085550123?&body=UFO%20{replaced.opt_in_code}",
    }
    assert replaced.member_id == second_member_id
    assert replaced.opt_in_code != CLAIM_CODE
    assert replaced.expires_at > datetime.now(UTC)
    assert len(provider.lines) == 1
    assert provider.lines[0][0] == phone
    assert provider.lines[0][1].startswith("imessage-line:")
    assert provider.sends == []


async def test_an_expired_claim_takeover_cannot_overwrite_a_concurrent_claim(db: None) -> None:
    workspace_id, first_member_id = await _seed()
    second_member_id = uuid4()
    third_member_id = uuid4()
    phone = "+14155550123"
    old_claim = PendingClaim(
        member_id=first_member_id,
        phone_number=phone,
        assigned_phone_number="+14085550123",
        opt_in_code=CLAIM_CODE,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    concurrent_claim = PendingClaim(
        member_id=third_member_id,
        phone_number=phone,
        assigned_phone_number="+14085550123",
        opt_in_code="BCD345",
        expires_at=datetime.now(UTC) + timedelta(minutes=PHONE_CLAIM_MINUTES),
    )

    class RacingProvider(RecordingProvider):
        async def assign_line(self, phone_number: str, idempotency_key: str) -> str:
            await ScopedStore(IMESSAGE_EXTENSION).put(
                phone_key(phone_number), concurrent_claim.model_dump(mode="json")
            )
            return await super().assign_line(phone_number, idempotency_key)

    provider = RacingProvider()
    tool_context = _tool_context(workspace_id, second_member_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=second_member_id,
                workspace_id=workspace_id,
                email="second@example.com",
                is_admin=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(workspace_id):
        assert tool_context.ext is not None
        await tool_context.ext.installations.bind(SURFACE_IMESSAGE, provider.installation_id)
        await tool_context.ext.store.put(phone_key(phone), old_claim.model_dump(mode="json"))
        result = await ImessageConnect(provider=lambda: provider).run(
            tool_context,
            ImessageConnectInput(phone_number=phone, user_description="Connect my phone."),
        )
        stored = await tool_context.ext.store.get(phone_key(phone))
    assert json.loads(result.content[0].text) == {
        "state": "not_connected",
        "instruction": "The phone connection changed. Ask again.",
    }
    assert PendingClaim.model_validate(stored) == concurrent_claim
    assert provider.sends == []


async def test_connect_answers_a_phone_the_surface_already_knows(db: None) -> None:
    workspace_id, member_id = await _seed()
    other_member_id = uuid4()
    phone = "+14155550123"
    other_phone = "+16505550123"
    provider = RecordingProvider()
    tool_context = _tool_context(workspace_id, member_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=other_member_id,
                workspace_id=workspace_id,
                email="second@example.com",
                is_admin=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        for external_id, owner in ((phone, member_id), (other_phone, other_member_id)):
            await connection.execute(
                sa.insert(tables.surface_identity).values(
                    workspace_id=workspace_id,
                    member_id=owner,
                    surface=SURFACE_IMESSAGE,
                    external_id=external_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    tool = ImessageConnect(provider=lambda: provider)
    with ws(workspace_id):
        assert tool_context.ext is not None
        await tool_context.ext.installations.bind(SURFACE_IMESSAGE, provider.installation_id)
        mine = await tool.run(
            tool_context,
            ImessageConnectInput(phone_number=phone, user_description="Connect my phone."),
        )
        theirs = await tool.run(
            tool_context,
            ImessageConnectInput(phone_number=other_phone, user_description="Connect my phone."),
        )
        assert await ScopedStore(IMESSAGE_EXTENSION).get(phone_key(phone)) is None
    assert json.loads(mine.content[0].text) == {
        "state": "connected",
        "instruction": "That phone is connected. Text +14085550123 from it.",
        "assigned_phone_number": "+14085550123",
    }
    assert json.loads(theirs.content[0].text) == {
        "state": "not_connected",
        "instruction": "That phone belongs to another workspace member.",
    }
    assert provider.sends == []


async def test_connect_refuses_a_phone_another_member_is_connecting(db: None) -> None:
    workspace_id, first_member_id = await _seed()
    second_member_id = uuid4()
    phone = "+14155550123"
    provider = RecordingProvider()
    tool_context = _tool_context(workspace_id, second_member_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=second_member_id,
                workspace_id=workspace_id,
                email="second@example.com",
                is_admin=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    claim = PendingClaim(
        member_id=first_member_id,
        phone_number=phone,
        assigned_phone_number="+14085550123",
        opt_in_code=CLAIM_CODE,
        expires_at=datetime.now(UTC) + timedelta(minutes=PHONE_CLAIM_MINUTES),
    )
    with ws(workspace_id):
        assert tool_context.ext is not None
        await tool_context.ext.installations.bind(SURFACE_IMESSAGE, provider.installation_id)
        await tool_context.ext.store.put(phone_key(phone), claim.model_dump(mode="json"))
        result = await ImessageConnect(provider=lambda: provider).run(
            tool_context,
            ImessageConnectInput(phone_number=phone, user_description="Connect my phone."),
        )
        stored = await tool_context.ext.store.get(phone_key(phone))
    assert json.loads(result.content[0].text) == {
        "state": "not_connected",
        "instruction": "Another workspace member is connecting that phone.",
    }
    assert PendingClaim.model_validate(stored) == claim
    assert provider.lines == []


async def test_an_unreadable_row_is_dropped_and_never_parks_the_surface(
    db: None, tmp_path: Path
) -> None:
    workspace_id, member_id = await _seed()
    phone = "+14155550123"
    provider = RecordingProvider()
    tool_context = _tool_context(workspace_id, member_id)
    context = _context(workspace_id, tmp_path, StubDbos())
    surface = ImessageSurface(provider=lambda: provider)
    receipt_key = f"{CONFIRMATION_REPLY_PREFIX}{hashlib.sha256(b'receipt-replay').hexdigest()}"
    unreadable = {"member_id": str(member_id)}
    with ws(workspace_id):
        store = ScopedStore(IMESSAGE_EXTENSION)
        await store.put(phone_key(phone), unreadable)
        await store.put(receipt_key, unreadable)
        assert (
            await surface._admit_message(
                context, provider, _message(phone, CLAIM_CODE, message_id="receipt-replay")
            )
            is None
        )
        assert await store.get(receipt_key) is None
        assert (
            await surface._admit_message(
                context, provider, _message(phone, CLAIM_CODE, message_id="fresh")
            )
            is None
        )
        assert await store.get(phone_key(phone)) is None
        assert tool_context.ext is not None
        await tool_context.ext.installations.bind(SURFACE_IMESSAGE, provider.installation_id)
        await store.put(phone_key(phone), unreadable)
        result = await ImessageConnect(provider=lambda: provider).run(
            tool_context,
            ImessageConnectInput(phone_number=phone, user_description="Connect my phone."),
        )
        stored = await store.get(phone_key(phone))
    assert json.loads(result.content[0].text)["state"] == "pending"
    assert PendingClaim.model_validate(stored).member_id == member_id
    assert provider.sends == []


async def test_other_store_names_cannot_enter_the_opt_in_flow(db: None, tmp_path: Path) -> None:
    workspace_id, _ = await _seed()
    phone = "+14155550123"
    message = _message(phone, f"UFO {CLAIM_CODE}", message_id="unmatched-opt-in")
    claim_key = f"claim:{hashlib.sha256(phone.encode()).hexdigest()}"
    receipt_key = f"confirmation-reply:{hashlib.sha256(message.id.encode()).hexdigest()}"
    provider = RecordingProvider()
    context = _context(workspace_id, tmp_path, StubDbos())
    with ws(workspace_id):
        store = ScopedStore(IMESSAGE_EXTENSION)
        await store.put(claim_key, {"value": "claim"})
        await store.put(receipt_key, {"value": "receipt"})
        await ImessageSurface(provider=lambda: provider)._admit_message(context, provider, message)
        assert await store.get(claim_key) == {"value": "claim"}
        assert await store.get(receipt_key) == {"value": "receipt"}
    async with workspace_tx() as connection:
        identities = (
            await connection.execute(
                sa.select(sa.func.count()).select_from(tables.surface_identity)
            )
        ).scalar_one()
    assert identities == 0
    assert provider.sends == []


@pytest.mark.parametrize(
    "text",
    (
        f"UFO {CLAIM_CODE}",
        CLAIM_CODE,
        f"  ufo {CLAIM_CODE.lower()}  ",
        f"Ufo {CLAIM_CODE[:3]}-{CLAIM_CODE[3:]}!",
        f"Sent from my iPhone: {CLAIM_CODE.lower()}.",
    ),
    ids=("as-asked", "bare-code", "lowercase", "punctuated", "surrounded"),
)
async def test_the_code_reads_through_case_spacing_and_punctuation(
    db: None, tmp_path: Path, text: str
) -> None:
    workspace_id, member_id = await _seed()
    phone = "+14155550123"
    provider = RecordingProvider()
    context = _context(workspace_id, tmp_path, StubDbos())
    with ws(workspace_id):
        await ScopedStore(IMESSAGE_EXTENSION).put(
            phone_key(phone),
            PendingClaim(
                member_id=member_id,
                phone_number=phone,
                assigned_phone_number="+14085550123",
                opt_in_code=CLAIM_CODE,
                expires_at=datetime.now(UTC) + timedelta(minutes=PHONE_CLAIM_MINUTES),
            ).model_dump(mode="json"),
        )
        await ImessageSurface(provider=lambda: provider)._admit_message(
            context,
            provider,
            _message(
                phone,
                text,
                message_id="opt-in",
                attachments=(
                    MessageAttachment(id="attachment-1", filename="photo.jpg", size_bytes=4),
                ),
            ),
        )
        assert await ScopedStore(IMESSAGE_EXTENSION).get(phone_key(phone)) is None
    async with workspace_tx() as connection:
        linked_member_id = (
            await connection.execute(
                sa.select(tables.surface_identity.c.member_id).where(
                    tables.surface_identity.c.surface == SURFACE_IMESSAGE,
                    tables.surface_identity.c.external_id == phone,
                )
            )
        ).scalar_one()
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert linked_member_id == member_id
    assert turns == 0
    assert provider.delivered == [(f"iMessage;-;{phone}", CONNECTED_TEXT)]


async def test_a_wrong_code_is_answered_and_an_expired_claim_says_so(
    db: None, tmp_path: Path
) -> None:
    workspace_id, member_id = await _seed()
    phone = "+14155550123"
    provider = RecordingProvider()
    context = _context(workspace_id, tmp_path, StubDbos())
    surface = ImessageSurface(provider=lambda: provider)

    def claim(expires_in: timedelta) -> dict[str, object]:
        return PendingClaim(
            member_id=member_id,
            phone_number=phone,
            assigned_phone_number="+14085550123",
            opt_in_code=CLAIM_CODE,
            expires_at=datetime.now(UTC) + expires_in,
        ).model_dump(mode="json")

    with ws(workspace_id):
        store = ScopedStore(IMESSAGE_EXTENSION)
        await store.put(phone_key(phone), claim(timedelta(minutes=PHONE_CLAIM_MINUTES)))
        for text, message_id in (
            ("UFO", "bare-ufo"),
            ("UFO BCD345", "other-code"),
            ("Driving Focus is on.", "automatic-reply"),
        ):
            await surface._admit_message(
                context, provider, _message(phone, text, message_id=message_id)
            )
        assert await store.get(phone_key(phone)) is not None
        await store.put(phone_key(phone), claim(timedelta(minutes=-1)))
        await surface._admit_message(
            context, provider, _message(phone, f"UFO {CLAIM_CODE}", message_id="late")
        )
        assert await store.get(phone_key(phone)) is None
    async with workspace_tx() as connection:
        identities = (
            await connection.execute(
                sa.select(sa.func.count()).select_from(tables.surface_identity)
            )
        ).scalar_one()
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert identities == 0
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
    with ws(workspace_id):
        await ScopedStore(IMESSAGE_EXTENSION).put(
            phone_key(phone),
            PendingClaim(
                member_id=member_id,
                phone_number=phone,
                assigned_phone_number="+14085550123",
                opt_in_code=CLAIM_CODE,
                expires_at=datetime.now(UTC) + timedelta(minutes=PHONE_CLAIM_MINUTES),
            ).model_dump(mode="json"),
        )
        await ImessageSurface(provider=lambda: provider)._admit_message(
            context, provider, _message(phone, " STOP ", message_id="stop")
        )
        assert await ScopedStore(IMESSAGE_EXTENSION).get(phone_key(phone)) is None
    async with workspace_tx() as connection:
        identities = (
            await connection.execute(
                sa.select(sa.func.count()).select_from(tables.surface_identity)
            )
        ).scalar_one()
    assert identities == 0
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
    surface = ImessageSurface(provider=lambda: provider)
    with ws(workspace_id):
        await ScopedStore(IMESSAGE_EXTENSION).put(
            phone_key(phone),
            PendingClaim(
                member_id=member_id,
                phone_number=phone,
                assigned_phone_number="+14085550123",
                opt_in_code=CLAIM_CODE,
                expires_at=datetime.now(UTC) + timedelta(minutes=PHONE_CLAIM_MINUTES),
            ).model_dump(mode="json"),
        )
        await surface._admit_message(context, provider, message)
        stored = await ScopedStore(IMESSAGE_EXTENSION).get(phone_key(phone))
    async with workspace_tx() as connection:
        identities = (
            await connection.execute(
                sa.select(sa.func.count()).select_from(tables.surface_identity)
            )
        ).scalar_one()
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert identities == 0
    assert turns == 0
    assert provider.sends == []
    assert stored is not None


@pytest.mark.parametrize(
    "attachments",
    [(), (MessageAttachment(id="attachment-1", filename="note.txt", size_bytes=4),)],
    ids=("text", "attachment"),
)
async def test_group_message_uses_the_ambient_reply_gate(
    db: None, tmp_path: Path, attachments: tuple[MessageAttachment, ...]
) -> None:
    workspace_id, member_id = await _seed()
    phone = "+14155550123"
    provider = RecordingProvider()
    context = _context(workspace_id, tmp_path, StubDbos())
    decision = DecisionModel("NO_REPLY")
    context = dataclass_replace(context, _ambient_reply=AmbientReplyClassifier(model=decision))
    surface = ImessageSurface(provider=lambda: provider)
    with ws(workspace_id):
        assert await context.link_member_id(phone, member_id) == member_id
        await surface._admit_message(
            context,
            provider,
            _message(
                phone,
                "" if attachments else "Thanks",
                conversation_id="iMessage;+;group",
                direct=False,
                attachments=attachments,
            ),
        )
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0
    assert len(decision.requests) == 1
    payload = cast(str, decision.requests[0].messages[0].content)
    if attachments:
        assert "Attachments: note.txt" in payload


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
    surface = ImessageSurface(provider=lambda: provider)
    attachments = (
        MessageAttachment(id="oversize", filename="large.txt", size_bytes=0),
        MessageAttachment(id="missing", filename="gone.txt", size_bytes=0),
    )
    with ws(workspace_id):
        assert await context.link_member_id(phone, member_id) == member_id
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
    workspace_id, member_id = await _seed()
    phone = "+14155550123"
    message = _message(phone, f"UFO {CLAIM_CODE}", message_id="opt-in")
    acknowledgement_key = "imessage-connected:opt-in"
    provider = RecordingProvider(fail_once={acknowledgement_key})
    context = _context(workspace_id, tmp_path, StubDbos())
    surface = ImessageSurface(provider=lambda: provider)
    with ws(workspace_id):
        await ScopedStore(IMESSAGE_EXTENSION).put(
            phone_key(phone),
            PendingClaim(
                member_id=member_id,
                phone_number=phone,
                assigned_phone_number="+14085550123",
                opt_in_code=CLAIM_CODE,
                expires_at=datetime.now(UTC) + timedelta(minutes=PHONE_CLAIM_MINUTES),
            ).model_dump(mode="json"),
        )
        with pytest.raises(httpx.ConnectError, match="send failed"):
            await surface._admit_message(context, provider, message)
        await surface._admit_message(context, provider, message)
        await surface._admit_message(context, provider, message)
        assert await ScopedStore(IMESSAGE_EXTENSION).get(phone_key(phone)) is None
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0
    assert provider.delivered == [(message.conversation_id, CONNECTED_TEXT)]
    assert [key for _, _, key in provider.sends] == [acknowledgement_key] * 3


async def test_opt_in_for_a_removed_member_is_discarded(db: None, tmp_path: Path) -> None:
    workspace_id, member_id = await _seed()
    phone = "+14155550123"
    message = _message(phone, f"UFO {CLAIM_CODE}", message_id="removed-member-opt-in")
    receipt_key = f"{CONFIRMATION_REPLY_PREFIX}{hashlib.sha256(message.id.encode()).hexdigest()}"
    provider = RecordingProvider()
    context = _context(workspace_id, tmp_path, StubDbos())
    surface = ImessageSurface(provider=lambda: provider)
    with ws(workspace_id):
        await ScopedStore(IMESSAGE_EXTENSION).put(
            phone_key(phone),
            PendingClaim(
                member_id=member_id,
                phone_number=phone,
                assigned_phone_number="+14085550123",
                opt_in_code=CLAIM_CODE,
                expires_at=datetime.now(UTC) + timedelta(minutes=PHONE_CLAIM_MINUTES),
            ).model_dump(mode="json"),
        )
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(tables.member).where(tables.member.c.id == member_id)
            )
        await surface._admit_message(context, provider, message)
        assert await ScopedStore(IMESSAGE_EXTENSION).get(phone_key(phone)) is None
        assert await ScopedStore(IMESSAGE_EXTENSION).get(receipt_key) is None
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0
    assert provider.sends == []


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
    surface = ImessageSurface(provider=lambda: project)
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
