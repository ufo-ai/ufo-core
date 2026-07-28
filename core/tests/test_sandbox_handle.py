"""The core seam that keeps a durable per-conversation sandbox handle on the conversation row.

`_open_sandbox` reads the row's stored handle, seeds the carrier's resume path from it, and persists
the returned `<backend>:<id>` — so a serve restart resumes the same sandbox and the reaper reclaims
one a prior process created. Persist-on-create is proven against the real local carrier; the
resume-read (the id core seeds and the write it skips when nothing changed) is asserted through the
conversation row, with a stand-in carrier recording the spec core built for it."""

import base64
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet

from ufo.agent_scope import agent
from ufo.blob import FilesystemBlobStore, S3BlobStore
from ufo.connectors import CliCredential, ForwardedResponse
from ufo.credentials import CredentialStore, HostChoice
from ufo.db import workspace_tx
from ufo.ext.manifest import CredentialSlot, InjectionTarget
from ufo.grants import GrantStore, grant_sentinel
from ufo.loop.queue import (
    GIT_PROXY_AUTH_CONFIG,
    SandboxAuthorizer,
    _git_config_env,
    _grant_cli_env,
    _open_sandbox,
)
from ufo.sandbox.fs_creds import SandboxFsCredentialMinter
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import (
    ProxyEndpoint,
    RunToken,
    RunTokenCodec,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
)
from ufo.schema import tables
from ufo.schema.records import Turn
from ufo.workspace import ws

PROXY = ProxyEndpoint(port=8080, ca_cert="ca-pem")
RUN_TOKENS = RunTokenCodec(b"sandbox-handle-test-secret")
GIT_PROXY_AUTH_ENV = _git_config_env(GIT_PROXY_AUTH_CONFIG)


async def _conversation(handle: str | None = None) -> tuple[UUID, UUID]:
    workspace_id, conversation_id, agent_id = uuid4(), uuid4(), uuid4()
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
                name=agent_id.hex[:8],
                prompt="be brief",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
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

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

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
        LocalCarrier(),
        "local",
        blob,
        None,
        PROXY,
        RUN_TOKENS,
        _turn(workspace_id, conversation_id),
        None,
        {},
        None,
        (),
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
    turn = _turn(workspace_id, conversation_id)

    await _open_sandbox(
        carrier,
        "e2b",
        blob,
        None,
        PROXY,
        RUN_TOKENS,
        turn,
        None,
        {},
        None,
        (),
    )

    assert carrier.specs[0].resume_id == "sbx-1"
    basic = "Basic " + base64.b64encode(f"{carrier.specs[0].run_token}:".encode()).decode()
    assert RUN_TOKENS.from_proxy_auth(basic) == RunToken(workspace_id, turn.id)
    assert await _stored_handle(conversation_id) == "e2b:sbx-1"


@dataclass(frozen=True)
class _MarkerRecordingS3BlobStore(S3BlobStore):
    """The S3 store with its network put recorded: the marker write is asserted as
    `_open_sandbox`'s own act, keyed off the stored handle it read."""

    marker_puts: list[str] = field(default_factory=list, compare=False)

    async def put(self, key: str, data: bytes) -> None:
        self.marker_puts.append(key)


class _NeverSts:
    async def assume_role(self, **kwargs: object) -> object:
        raise AssertionError("open_sandbox never redeems credentials")


async def test_open_sandbox_writes_the_workspace_marker_only_on_first_create(db: None) -> None:
    """`fresh_sandbox` is wired from the stored handle: the first create (no row handle) writes the
    workspace directory marker, and the resume (row handle present) skips it — the prefix invariant
    rides sandbox creation, never the per-turn path."""
    workspace_id, conversation_id = await _conversation()
    carrier = _ResumeRecordingCarrier(container_id="sbx-9")
    blob = _MarkerRecordingS3BlobStore(bucket="ufo-blobs")
    minter = SandboxFsCredentialMinter(
        sts=_NeverSts(),
        role_arn="arn:aws:iam::0:role/sbxfs",
        bucket="ufo-blobs",
        s3_url="https://s3.example:9000",
        region="us-east-1",
        path_style=False,
        token_secret=b"mount-secret",
    )

    await _open_sandbox(
        carrier,
        "e2b",
        blob,
        minter,
        PROXY,
        RUN_TOKENS,
        _turn(workspace_id, conversation_id),
        None,
        {},
        None,
        (),
    )

    assert carrier.specs[0].resume_id is None
    assert blob.marker_puts == [f"conversations/{conversation_id}/workspace/"]

    await _open_sandbox(
        carrier,
        "e2b",
        blob,
        minter,
        PROXY,
        RUN_TOKENS,
        _turn(workspace_id, conversation_id),
        None,
        {},
        None,
        (),
    )

    assert carrier.specs[1].resume_id == "sbx-9"
    assert blob.marker_puts == [f"conversations/{conversation_id}/workspace/"]


async def test_open_sandbox_writes_the_marker_when_taking_over_a_foreign_backend_handle(
    db: None,
) -> None:
    """The other path to `resume_id is None`: a stored handle another backend wrote is ignored and
    the sandbox is created fresh — on the S3 backend that fresh create must also write the marker
    (idempotent when a prior backend's mount already did)."""
    workspace_id, conversation_id = await _conversation(handle="local:elsewhere")
    carrier = _ResumeRecordingCarrier(container_id="sbx-2")
    blob = _MarkerRecordingS3BlobStore(bucket="ufo-blobs")
    minter = SandboxFsCredentialMinter(
        sts=_NeverSts(),
        role_arn="arn:aws:iam::0:role/sbxfs",
        bucket="ufo-blobs",
        s3_url="https://s3.example:9000",
        region="us-east-1",
        path_style=False,
        token_secret=b"mount-secret",
    )

    await _open_sandbox(
        carrier,
        "e2b",
        blob,
        minter,
        PROXY,
        RUN_TOKENS,
        _turn(workspace_id, conversation_id),
        None,
        {},
        None,
        (),
    )

    assert carrier.specs[0].resume_id is None
    assert blob.marker_puts == [f"conversations/{conversation_id}/workspace/"]
    assert await _stored_handle(conversation_id) == "e2b:sbx-2"


async def test_open_sandbox_ignores_a_handle_another_backend_wrote_and_overwrites_it(
    db: None, tmp_path: Path
) -> None:
    """A handle another backend wrote is not this carrier's to resume: resume_id is None (create
    fresh) and the fresh id overwrites the row under this backend's prefix."""
    workspace_id, conversation_id = await _conversation(handle="docker:cid-1")
    carrier = _ResumeRecordingCarrier(container_id="sbx-9")
    blob = FilesystemBlobStore(root=tmp_path)

    await _open_sandbox(
        carrier,
        "e2b",
        blob,
        None,
        PROXY,
        RUN_TOKENS,
        _turn(workspace_id, conversation_id),
        None,
        {},
        None,
        (),
    )

    assert carrier.specs[0].resume_id is None
    assert await _stored_handle(conversation_id) == "e2b:sbx-9"


@dataclass(frozen=True)
class _NeverForwarder:
    async def forward(
        self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes
    ) -> ForwardedResponse:
        raise AssertionError("open_sandbox never forwards")


HUB_CLI = CliCredential(env="HUB_TOKEN", header="authorization", forward=_NeverForwarder())


async def _seed_grant(workspace_id: UUID, conversation_id: UUID, shared: bool) -> tuple[UUID, UUID]:
    agent_id, member_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="a@b.c",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(workspace_id), agent(agent_id):
        await GrantStore().record(
            provider="hub",
            account_id="acct-1",
            host="api.hub.test",
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=shared,
        )
    return agent_id, member_id


async def test_open_sandbox_exports_the_acting_members_grant_sentinels(
    db: None, tmp_path: Path
) -> None:
    """The base sandbox carries no first-speaker grant; a message-bound call derives it."""
    workspace_id, conversation_id = await _conversation()
    agent_id, member_id = await _seed_grant(workspace_id, conversation_id, shared=False)
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")
    blob = FilesystemBlobStore(root=tmp_path)
    turn = _turn(workspace_id, conversation_id).model_copy(
        update={"agent_id": agent_id, "speaker_member_id": member_id}
    )

    with ws(workspace_id), agent(agent_id):
        await _open_sandbox(
            carrier,
            "e2b",
            blob,
            None,
            PROXY,
            RUN_TOKENS,
            turn,
            GrantStore(),
            {"hub": HUB_CLI},
            None,
            (),
        )
        scoped = await _grant_cli_env(GrantStore(), {"hub": HUB_CLI}, member_id, turn.id)

    assert carrier.specs[0].env == GIT_PROXY_AUTH_ENV
    assert scoped == {"HUB_TOKEN": grant_sentinel("acct-1")}


async def test_sandbox_authorizer_binds_run_token_and_cli_grants_to_the_acting_member(
    db: None,
) -> None:
    workspace_id, conversation_id = await _conversation()
    agent_id, member_id = await _seed_grant(workspace_id, conversation_id, shared=False)
    turn = _turn(workspace_id, conversation_id).model_copy(update={"agent_id": agent_id})
    common_token = RUN_TOKENS.encode(RunToken(workspace_id, turn.id))
    proxy = f"http://{common_token}:@proxy:8080"
    base = SandboxSession(
        carrier=_ResumeRecordingCarrier(container_id="sbx-1"),
        handle=SandboxHandle(
            conversation_id=conversation_id,
            container_id="sbx-1",
            run_token=common_token,
            egress_env={
                "HTTP_PROXY": proxy,
                "HTTPS_PROXY": proxy,
                "http_proxy": proxy,
                "https_proxy": proxy,
            },
        ),
    )
    authorizer = SandboxAuthorizer(
        sandbox=base,
        run_tokens=RUN_TOKENS,
        grants=GrantStore(),
        clis={"hub": HUB_CLI},
        turn=turn,
    )

    with ws(workspace_id), agent(agent_id):
        authorized = await authorizer.authorize(member_id)

    basic = "Basic " + base64.b64encode(f"{authorized.handle.run_token}:".encode()).decode()
    assert RUN_TOKENS.from_proxy_auth(basic) == RunToken(
        workspace_id=workspace_id,
        turn_id=turn.id,
        acting_member_id=member_id,
    )
    assert authorized.handle.egress_env["HUB_TOKEN"] == grant_sentinel("acct-1")
    assert "HUB_TOKEN" not in base.handle.egress_env


async def test_open_sandbox_exports_nothing_for_a_foreign_private_grant(
    db: None, tmp_path: Path
) -> None:
    """A private grant of another member is not the turn's to use: a speakerless turn with no
    on-behalf-of member exports no sentinel, so the CLI runs unauthenticated rather than drawing a
    foreign account."""
    workspace_id, conversation_id = await _conversation()
    agent_id, _ = await _seed_grant(workspace_id, conversation_id, shared=False)
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")
    blob = FilesystemBlobStore(root=tmp_path)
    turn = _turn(workspace_id, conversation_id).model_copy(update={"agent_id": agent_id})

    with ws(workspace_id), agent(agent_id):
        await _open_sandbox(
            carrier,
            "e2b",
            blob,
            None,
            PROXY,
            RUN_TOKENS,
            turn,
            GrantStore(),
            {"hub": HUB_CLI},
            None,
            (),
        )

    assert carrier.specs[0].env == GIT_PROXY_AUTH_ENV


async def test_open_sandbox_exports_the_private_sentinel_over_the_shared_one(
    db: None, tmp_path: Path
) -> None:
    """Common execution gets the shared grant; a message-bound call prefers its private grant."""
    workspace_id, conversation_id = await _conversation()
    agent_id, member_id = await _seed_grant(workspace_id, conversation_id, shared=False)
    with ws(workspace_id), agent(agent_id):
        await GrantStore().record(
            provider="hub",
            account_id="acct-shared",
            host="api.hub.test",
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=True,
        )
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")
    blob = FilesystemBlobStore(root=tmp_path)
    turn = _turn(workspace_id, conversation_id).model_copy(
        update={"agent_id": agent_id, "speaker_member_id": member_id}
    )

    with ws(workspace_id), agent(agent_id):
        await _open_sandbox(
            carrier,
            "e2b",
            blob,
            None,
            PROXY,
            RUN_TOKENS,
            turn,
            GrantStore(),
            {"hub": HUB_CLI},
            None,
            (),
        )
        scoped = await _grant_cli_env(GrantStore(), {"hub": HUB_CLI}, member_id, turn.id)

    assert carrier.specs[0].env == {
        **GIT_PROXY_AUTH_ENV,
        "HUB_TOKEN": grant_sentinel("acct-shared"),
    }
    assert scoped == {"HUB_TOKEN": grant_sentinel("acct-1")}


async def test_open_sandbox_exports_nothing_when_the_shared_tier_is_ambiguous(
    db: None, tmp_path: Path
) -> None:
    """An acting member with no private grant falls back to the shared tier — and two shared
    accounts for one provider are just as indistinguishable to a static env var, so the export is
    skipped there too, not only when the member's own tier is ambiguous."""
    workspace_id, conversation_id = await _conversation()
    agent_id, member_id = await _seed_grant(workspace_id, conversation_id, shared=True)
    with ws(workspace_id), agent(agent_id):
        await GrantStore().record(
            provider="hub",
            account_id="acct-shared-2",
            host="api.hub.test",
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=True,
        )
    other_member = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=other_member,
                workspace_id=workspace_id,
                email="other@x.test",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")
    blob = FilesystemBlobStore(root=tmp_path)
    turn = _turn(workspace_id, conversation_id).model_copy(
        update={"agent_id": agent_id, "speaker_member_id": other_member}
    )

    with ws(workspace_id), agent(agent_id):
        await _open_sandbox(
            carrier,
            "e2b",
            blob,
            None,
            PROXY,
            RUN_TOKENS,
            turn,
            GrantStore(),
            {"hub": HUB_CLI},
            None,
            (),
        )

    assert carrier.specs[0].env == GIT_PROXY_AUTH_ENV


async def test_open_sandbox_exports_nothing_when_the_account_is_ambiguous(
    db: None, tmp_path: Path
) -> None:
    """A static env var names no account, so two usable accounts in one tier cannot be
    disambiguated per request. Rather than silently pick one — diverging from `connector_account`,
    which fails loud on the same ambiguity — the export is skipped, so the CLI fails visibly to
    authenticate instead of acting as whichever account sorts first."""
    workspace_id, conversation_id = await _conversation()
    agent_id, member_id = await _seed_grant(workspace_id, conversation_id, shared=False)
    with ws(workspace_id), agent(agent_id):
        await GrantStore().record(
            provider="hub",
            account_id="acct-2",
            host="api.hub.test",
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=False,
        )
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")
    blob = FilesystemBlobStore(root=tmp_path)
    turn = _turn(workspace_id, conversation_id).model_copy(
        update={"agent_id": agent_id, "speaker_member_id": member_id}
    )

    with ws(workspace_id), agent(agent_id):
        await _open_sandbox(
            carrier,
            "e2b",
            blob,
            None,
            PROXY,
            RUN_TOKENS,
            turn,
            GrantStore(),
            {"hub": HUB_CLI},
            None,
            (),
        )

    assert carrier.specs[0].env == GIT_PROXY_AUTH_ENV


DATADOG_SITES = HostChoice(
    slot="datadog_api_host",
    description="Datadog site for this org.",
    hosts=("api.datadoghq.com", "api.us5.datadoghq.com"),
    default="api.datadoghq.com",
    env="DD_HOST",
)

DATADOG_SLOTS = (
    CredentialSlot(
        name="datadog_api_key",
        description="api key",
        injection=InjectionTarget(
            host=DATADOG_SITES,
            header="DD-API-KEY",
            sentinel="SENTINEL_DD_API",
            env="DD_API_KEY",
        ),
    ),
    CredentialSlot(
        name="datadog_application_key",
        description="application key",
        injection=InjectionTarget(
            host=DATADOG_SITES,
            header="DD-APPLICATION-KEY",
            sentinel="SENTINEL_DD_APP",
            env="DD_APP_KEY",
        ),
    ),
    CredentialSlot(name="datadog_api_host", description="site host"),
)


async def test_open_sandbox_exports_keyed_provider_sentinels_not_secrets(
    db: None, tmp_path: Path
) -> None:
    """A keyed provider the workspace has filled reaches the sandbox as sentinels and a host — never
    the secret, which only the egress proxy swaps in on the wire. The host is this workspace's own,
    so the agent addresses the site its key is valid for."""
    workspace_id, conversation_id = await _conversation()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(workspace_id, "datadog_api_key", "dd-api-real")
    await store.put(workspace_id, "datadog_application_key", "dd-app-real")
    await store.put(workspace_id, "datadog_api_host", "api.us5.datadoghq.com")
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")

    await _open_sandbox(
        carrier,
        "e2b",
        FilesystemBlobStore(root=tmp_path),
        None,
        PROXY,
        RUN_TOKENS,
        _turn(workspace_id, conversation_id),
        None,
        {},
        store,
        DATADOG_SLOTS,
    )

    assert carrier.specs[0].env == {
        **GIT_PROXY_AUTH_ENV,
        "DD_API_KEY": "SENTINEL_DD_API",
        "DD_APP_KEY": "SENTINEL_DD_APP",
        "DD_HOST": "api.us5.datadoghq.com",
    }


async def test_open_sandbox_exports_nothing_for_an_unfilled_keyed_slot(
    db: None, tmp_path: Path
) -> None:
    """An empty slot exports no variable at all, so the agent finds nothing half-usable for a
    provider the member has not keyed and asks them to fill it instead of guessing."""
    workspace_id, conversation_id = await _conversation()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(workspace_id, "datadog_api_key", "dd-api-real")
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")

    await _open_sandbox(
        carrier,
        "e2b",
        FilesystemBlobStore(root=tmp_path),
        None,
        PROXY,
        RUN_TOKENS,
        _turn(workspace_id, conversation_id),
        None,
        {},
        store,
        DATADOG_SLOTS,
    )

    assert carrier.specs[0].env == {
        **GIT_PROXY_AUTH_ENV,
        "DD_API_KEY": "SENTINEL_DD_API",
        "DD_HOST": "api.datadoghq.com",
    }


async def test_open_sandbox_withholds_and_warns_on_a_selection_the_row_does_not_offer(
    db: None, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A stored selection the declaration does not offer is refused by both roles alike, so the
    sandbox gets no half-usable credential — not the sentinel, not the host. The engine warns as the
    proxy does: the export is withheld when the sandbox opens, well before any request would fail,
    and the member would otherwise see only a variable that never appeared."""
    workspace_id, conversation_id = await _conversation()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(workspace_id, "datadog_api_key", "dd-api-real")
    await store.put(workspace_id, "datadog_application_key", "dd-app-real")
    await store.put(workspace_id, "datadog_api_host", "169.254.169.254")
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")

    with caplog.at_level(logging.WARNING, logger="ufo"):
        await _open_sandbox(
            carrier,
            "e2b",
            FilesystemBlobStore(root=tmp_path),
            None,
            PROXY,
            RUN_TOKENS,
            _turn(workspace_id, conversation_id),
            None,
            {},
            store,
            DATADOG_SLOTS,
        )

    assert carrier.specs[0].env == GIT_PROXY_AUTH_ENV
    warned = [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "sandbox.keyed_host_unavailable"
    ]
    assert {entry["slot"] for entry in warned} == {"datadog_api_key", "datadog_application_key"}
    assert not any("169.254" in str(entry) for entry in warned)


async def test_open_sandbox_survives_a_keyed_slot_whose_source_raises(
    db: None, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The export half of the isolation the wire already has. A source that cannot answer withholds
    its own slot and nothing more: the sandbox still opens, so a turn that never touches that
    provider runs, and the slot's env is simply absent rather than present and unusable.

    Isolating here matters as much as at the proxy, because this call is on the path of every
    sandbox open — an escaping fault would fail every turn in the workspace, which is the failure
    this whole change exists to remove rather than relocate."""
    workspace_id, conversation_id = await _conversation()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(workspace_id, "datadog_api_key", "dd-api-real")
    await store.put(workspace_id, "datadog_application_key", "dd-app-real")

    class _Unusable:
        async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None:
            raise ValueError("stored binding does not open")

        async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool:
            raise ValueError("stored binding does not open")

    slots = (
        replace(DATADOG_SLOTS[0], source=_Unusable()),
        DATADOG_SLOTS[1],
        DATADOG_SLOTS[2],
    )
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")

    with caplog.at_level(logging.WARNING, logger="ufo"):
        await _open_sandbox(
            carrier,
            "e2b",
            FilesystemBlobStore(root=tmp_path),
            None,
            PROXY,
            RUN_TOKENS,
            _turn(workspace_id, conversation_id),
            None,
            {},
            store,
            slots,
        )

    env = carrier.specs[0].env
    assert "DD_API_KEY" not in env
    assert env["DD_APP_KEY"] == "SENTINEL_DD_APP"
    withheld = [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "sandbox.credential_slot_failed"
    ]
    assert [entry["slot"] for entry in withheld] == ["datadog_api_key"]
    assert withheld[0]["error_class"] == "ValueError"


async def test_open_sandbox_configures_git_to_authenticate_to_the_proxy(
    db: None, tmp_path: Path
) -> None:
    """git is the one sandbox client that will not present the run token unprompted: its default
    `anyauth` waits for a `407` challenge the proxy never sends, so its CONNECT arrives unattributed
    and resolves to the base rules. Every turn — holding a grant or a key or neither — gets
    `http.proxyAuthMethod=basic`, so git presents the token on the first CONNECT."""
    workspace_id, conversation_id = await _conversation()
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")
    blob = FilesystemBlobStore(root=tmp_path)
    turn = _turn(workspace_id, conversation_id)

    await _open_sandbox(carrier, "e2b", blob, None, PROXY, RUN_TOKENS, turn, None, {}, None, ())

    assert carrier.specs[0].env == {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.proxyAuthMethod",
        "GIT_CONFIG_VALUE_0": "basic",
    }


GIT_SLOTS = (
    CredentialSlot(
        name="github_git_token",
        description="git token",
        injection=InjectionTarget(
            host="github.com",
            header="Authorization",
            sentinel="SENTINEL_GIT",
            dimension="requests",
            git_basic_user="x-access-token",
        ),
    ),
)


async def test_open_sandbox_configures_git_to_present_the_credential_sentinel(
    db: None, tmp_path: Path
) -> None:
    """A workspace holding a git credential gets the host's `extraheader` alongside the proxy-auth
    setting, both through git's own config env. The sandbox sees the sentinel — the secret is
    swapped in at the proxy — and git sends it on every request to that host, which is what makes
    `git clone` and `git push` of a private repository authenticate."""
    workspace_id, conversation_id = await _conversation()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(workspace_id, "github_git_token", "ghp-real")
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")

    await _open_sandbox(
        carrier,
        "e2b",
        FilesystemBlobStore(root=tmp_path),
        None,
        PROXY,
        RUN_TOKENS,
        _turn(workspace_id, conversation_id),
        None,
        {},
        store,
        GIT_SLOTS,
    )

    assert carrier.specs[0].env == {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "http.proxyAuthMethod",
        "GIT_CONFIG_VALUE_0": "basic",
        "GIT_CONFIG_KEY_1": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_1": "Authorization: SENTINEL_GIT",
    }
    assert "ghp-real" not in str(carrier.specs[0].env)


async def test_open_sandbox_configures_no_extraheader_without_a_git_credential(
    db: None, tmp_path: Path
) -> None:
    """An unfilled slot configures nothing: the proxy derives no rule for the host either, so git
    reaches it as an opaque tunnel and an anonymous clone of a public repository still works. A
    sentinel sent to a host nothing swaps on would fail a request that needs no credential."""
    workspace_id, conversation_id = await _conversation()
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")

    await _open_sandbox(
        carrier,
        "e2b",
        FilesystemBlobStore(root=tmp_path),
        None,
        PROXY,
        RUN_TOKENS,
        _turn(workspace_id, conversation_id),
        None,
        {},
        CredentialStore(fernet=Fernet(Fernet.generate_key())),
        GIT_SLOTS,
    )

    assert carrier.specs[0].env == GIT_PROXY_AUTH_ENV


GIT_HOST_CHOICE_SLOTS = (
    CredentialSlot(
        name="github_git_token",
        description="git token",
        injection=InjectionTarget(
            host=HostChoice(
                slot="github_git_host",
                description="GitHub host for this org",
                hosts=("github.com", "github.example.com"),
                default="github.com",
                env="GITHUB_HOST",
            ),
            header="Authorization",
            sentinel="SENTINEL_GIT",
            dimension="requests",
            git_basic_user="x-access-token",
        ),
    ),
    CredentialSlot(name="github_git_host", description="GitHub host"),
)


async def test_open_sandbox_configures_no_git_host_the_declaration_does_not_offer(
    db: None, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A git host resolving to nothing configures nothing, and says so. The extraheader names the
    host in its own config key, so an unresolved one has no key to write — and writing the turn's
    sentinel under a host the declaration never offered would send it somewhere no proxy rule swaps
    it, failing a clone that would otherwise have worked anonymously. Warned for the same reason
    the keyed export warns: the withholding happens when the sandbox opens, not when git runs."""
    workspace_id, conversation_id = await _conversation()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(workspace_id, "github_git_token", "ghp-real")
    await store.put(workspace_id, "github_git_host", "github.evil.test")
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")

    with caplog.at_level(logging.WARNING, logger="ufo"):
        await _open_sandbox(
            carrier,
            "e2b",
            FilesystemBlobStore(root=tmp_path),
            None,
            PROXY,
            RUN_TOKENS,
            _turn(workspace_id, conversation_id),
            None,
            {},
            store,
            GIT_HOST_CHOICE_SLOTS,
        )

    assert carrier.specs[0].env == GIT_PROXY_AUTH_ENV
    warned = [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "sandbox.git_host_unavailable"
    ]
    assert [entry["slot"] for entry in warned] == ["github_git_token"]
    assert not any("evil" in str(entry) for entry in warned)


class _BoundSource:
    """A minting source that records which question it was asked: presence or value."""

    def __init__(self) -> None:
        self.mints = 0

    async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None:
        self.mints += 1
        return "minted-installation-token"

    async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool:
        return True


async def test_open_sandbox_exports_a_keyed_sentinel_from_a_source_without_minting(
    db: None, tmp_path: Path
) -> None:
    """The keyed-provider export asks the same presence question as the git config, and for the same
    reason: both run on every sandbox open. A provider whose key is minted rather than stored must
    reach the sandbox as its sentinel without the export touching the provider to find out."""
    workspace_id, conversation_id = await _conversation()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(workspace_id, "datadog_api_host", "api.datadoghq.com")
    source = _BoundSource()
    slots = (replace(DATADOG_SLOTS[0], source=source), DATADOG_SLOTS[2])
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")

    await _open_sandbox(
        carrier,
        "e2b",
        FilesystemBlobStore(root=tmp_path),
        None,
        PROXY,
        RUN_TOKENS,
        _turn(workspace_id, conversation_id),
        None,
        {},
        store,
        slots,
    )

    assert carrier.specs[0].env["DD_API_KEY"] == "SENTINEL_DD_API"
    assert source.mints == 0


async def test_open_sandbox_configures_git_from_a_source_without_minting(
    db: None, tmp_path: Path
) -> None:
    """Every turn in every workspace opens a sandbox, so the question asked here is whether the slot
    is filled — never what it holds. A source mints against a provider, so asking it for the value
    would put a network call on sandbox startup for turns that never touch git."""
    workspace_id, conversation_id = await _conversation()
    source = _BoundSource()
    slots = (replace(GIT_SLOTS[0], source=source),)
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")

    await _open_sandbox(
        carrier,
        "e2b",
        FilesystemBlobStore(root=tmp_path),
        None,
        PROXY,
        RUN_TOKENS,
        _turn(workspace_id, conversation_id),
        None,
        {},
        CredentialStore(fernet=Fernet(Fernet.generate_key())),
        slots,
    )

    assert carrier.specs[0].env["GIT_CONFIG_VALUE_1"] == "Authorization: SENTINEL_GIT"
    assert source.mints == 0
