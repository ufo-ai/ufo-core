"""The core seam that keeps a durable per-conversation sandbox handle on the conversation row.

`_open_sandbox` opens the turn's sandbox through `ConversationSandbox`: the row's stored handle
seeds the carrier's resume path, and the returned `<backend>:<id>` is persisted — so a serve
restart resumes the same sandbox. Persist-on-create is proven against the real local carrier; the
resume-read and the env exports the turn derives are asserted through the conversation row and the
spec a stand-in carrier records."""

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from dbos import DBOSClient

from ufo.db import workspace_tx
from ufo.harness.containment import LocationEscape, NonDirectoryAncestor
from ufo.harness.sandbox import terminal
from ufo.harness.sandbox.conversation import (
    SANDBOX_IMAGE_REF,
    WORKSPACE_ROOT_SETTING,
    WORKSPACE_WRITE_MAX_BYTES,
    ConversationSandbox,
)
from ufo.harness.sandbox.exec_env import (
    CONVERSATION_ID_ENV,
    GIT_IDENTITY_ENV,
    GIT_PROXY_AUTH_CONFIG,
    _git_config_env,
    _grant_cli_env,
)
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import (
    PROXY_PASSWORD,
    Carrier,
    DialTarget,
    ExecResult,
    ProxyEndpoint,
    RunToken,
    RunTokenCodec,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
    _LateSandbox,
    ufo_fs_file_op,
)
from ufo.harness.sandbox.terminal import TerminalCarrier, TerminalGone, Terminals
from ufo.runtime.access.connectors import CliCredential, GitWire
from ufo.runtime.access.credentials import CredentialStore, HostChoice
from ufo.runtime.access.grants import CommitIdentity, GrantStore, grant_sentinel
from ufo.runtime.agent_scope import agent
from ufo.runtime.authority import WORKSPACE_AUTHORITY, MemberAuthority
from ufo.runtime.ext.manifest import CarrierSpec, CredentialSlot, InjectionTarget
from ufo.runtime.profiles import CORE_SUBAGENT_PROFILES, GENERAL_PURPOSE
from ufo.runtime.queue import (
    SandboxAuthorizer,
    _open_sandbox,
)
from ufo.runtime.subagents import SubagentRegistry, Subagents
from ufo.runtime.tools.bridge import TOOL_BRIDGE_URL, TOOL_BRIDGE_URL_ENV
from ufo.runtime.turns.audience import SHARED_AUDIENCE
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Turn

PROXY = ProxyEndpoint(port=8080, ca_cert="ca-pem")
RUN_TOKENS = RunTokenCodec(b"sandbox-handle-test-secret")
GIT_PROXY_AUTH_ENV = _git_config_env(GIT_PROXY_AUTH_CONFIG)
pytestmark = pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)


def _derived_env(spec: SandboxSpec) -> Mapping[str, str]:
    """The env a turn's credentials derive, without the fixed exports every open carries."""
    return {
        key: value
        for key, value in spec.env.items()
        if key not in {CONVERSATION_ID_ENV, TOOL_BRIDGE_URL_ENV}
    }


async def _conversation(
    handle: str | None = None, sandbox_size: str = "small"
) -> tuple[UUID, UUID]:
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
                sandbox_size=sandbox_size,
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


def _sandboxes(
    carrier: Carrier,
    backend: str,
    tmp_path: Path,
    resume: Mapping[str, tuple[Carrier, CarrierSpec]] | None = None,
) -> ConversationSandbox:
    return ConversationSandbox(
        carrier=carrier,
        backend=backend,
        off_cluster=backend == "e2b",
        image_ref=SANDBOX_IMAGE_REF,
        proxy=PROXY,
        workspace_root=tmp_path / "workspaces",
        resume_carriers=resume if resume is not None else {},
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

    async def attach(self, spec: SandboxSpec) -> SandboxHandle | None:
        self.specs.append(spec)
        return SandboxHandle(conversation_id=spec.conversation_id, container_id=self.container_id)

    async def exec(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
    ) -> ExecResult:
        raise AssertionError("open_sandbox never execs")

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        raise AssertionError("open_sandbox never writes")

    def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]:
        raise AssertionError("open_sandbox never reads")

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        raise AssertionError("open_sandbox never dials a port")


async def test_open_sandbox_persists_the_backend_prefixed_handle(db: None, tmp_path: Path) -> None:
    """A fresh create against the real local carrier persists `<backend>:<id>` on the row — the
    durable pointer the next process resumes from — and roots the sandbox's workspace under this
    conversation's own directory."""
    workspace_id, conversation_id = await _conversation()

    with ws(workspace_id):
        handle = (
            await _open_sandbox(
                _sandboxes(LocalCarrier(), "local", tmp_path),
                RUN_TOKENS,
                _turn(workspace_id, conversation_id),
                None,
                {},
                None,
                (),
            )
        ).handle

    assert handle.container_id == "local"
    assert handle.workspace_host_path == str(
        (tmp_path / "workspaces" / str(conversation_id)).resolve()
    )
    assert await _stored_handle(conversation_id) == "local:local"


async def test_open_sandbox_follows_a_symlinked_workspace_root(db: None, tmp_path: Path) -> None:
    """`workspace_root` is deploy config, and a root pointing at the volume the workspaces live on
    is an ordinary compose or k8s layout — so the link is followed once and canonicalized, and a
    turn runs. Refusing it would be an outage of every turn and every browse on that layout.

    What the canonical root buys is asserted here too: the carrier is handed the resolved path, so
    nothing downstream resolves the link a second time."""
    workspace_id, conversation_id = await _conversation()
    volume = tmp_path / "volume"
    volume.mkdir()
    (tmp_path / "workspaces").symlink_to(volume, target_is_directory=True)

    with ws(workspace_id):
        handle = (
            await _open_sandbox(
                _sandboxes(LocalCarrier(), "local", tmp_path),
                RUN_TOKENS,
                _turn(workspace_id, conversation_id),
                None,
                {},
                None,
                (),
            )
        ).handle

    assert handle.workspace_host_path == str(volume / str(conversation_id))
    assert (volume / str(conversation_id)).is_dir()
    assert await _stored_handle(conversation_id) == "local:local"


async def test_open_sandbox_refuses_a_workspace_root_that_is_not_a_directory(
    db: None, tmp_path: Path
) -> None:
    """An operator who pointed the key at a file reads the key back in the message, and no sandbox
    is opened on it."""
    workspace_id, conversation_id = await _conversation()
    (tmp_path / "workspaces").write_bytes(b"not a directory")

    with ws(workspace_id), pytest.raises(NonDirectoryAncestor, match=WORKSPACE_ROOT_SETTING):
        await _open_sandbox(
            _sandboxes(LocalCarrier(), "local", tmp_path),
            RUN_TOKENS,
            _turn(workspace_id, conversation_id),
            None,
            {},
            None,
            (),
        )

    assert await _stored_handle(conversation_id) is None


async def test_open_sandbox_refuses_a_symlinked_conversation_directory(
    db: None, tmp_path: Path
) -> None:
    """A link already holding the conversation's own name is the same escape one level down: the
    directory is re-opened without following it, so the workspace is never pointed out of the
    root."""
    workspace_id, conversation_id = await _conversation()
    root = tmp_path / "workspaces"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / str(conversation_id)).symlink_to(outside, target_is_directory=True)

    with ws(workspace_id), pytest.raises(LocationEscape):
        await _open_sandbox(
            _sandboxes(LocalCarrier(), "local", tmp_path),
            RUN_TOKENS,
            _turn(workspace_id, conversation_id),
            None,
            {},
            None,
            (),
        )

    assert list(outside.iterdir()) == []
    assert await _stored_handle(conversation_id) is None


async def test_open_sandbox_provisions_a_traversing_root_at_its_canonical_place(
    db: None, tmp_path: Path
) -> None:
    """The carrier is handed the canonical directory, never the configured spelling of it: a
    `workspace_root` carrying a `..` segment lands its conversation directories at the one place it
    resolves to, so what a later check compares against and what holds the files are one path."""
    workspace_id, conversation_id = await _conversation()
    sandboxes = ConversationSandbox(
        carrier=LocalCarrier(),
        backend="local",
        off_cluster=False,
        image_ref=SANDBOX_IMAGE_REF,
        proxy=PROXY,
        workspace_root=tmp_path / "roots" / ".." / "workspaces",
    )

    with ws(workspace_id):
        handle = (
            await _open_sandbox(
                sandboxes, RUN_TOKENS, _turn(workspace_id, conversation_id), None, {}, None, ()
            )
        ).handle

    assert handle.workspace_host_path == str(tmp_path / "workspaces" / str(conversation_id))
    assert (tmp_path / "workspaces" / str(conversation_id)).is_dir()


async def test_open_sandbox_resumes_from_the_stored_handle_without_rewriting(
    db: None, tmp_path: Path
) -> None:
    """A row that already holds this backend's handle seeds the carrier's resume_id — resume, not a
    fresh create — under the turn's signed run token."""
    workspace_id, conversation_id = await _conversation(handle="e2b:sbx-1")
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")
    turn = _turn(workspace_id, conversation_id)

    with ws(workspace_id):
        await _open_sandbox(
            _sandboxes(carrier, "e2b", tmp_path), RUN_TOKENS, turn, None, {}, None, ()
        )

    assert carrier.specs[0].resume_id == "sbx-1"
    basic = "Basic " + base64.b64encode(f"{carrier.specs[0].run_token}:".encode()).decode()
    assert RUN_TOKENS.from_proxy_auth(basic) == RunToken(workspace_id, turn.id, WORKSPACE_AUTHORITY)
    assert await _stored_handle(conversation_id) == "e2b:sbx-1"


async def test_open_states_the_turn_a_carrier_scopes_its_running_commands_to(
    db: None, tmp_path: Path
) -> None:
    """One container serves every turn of its conversation, plus every subagent turn that inherited
    it, so the turn is the only thing that tells one caller's running command from another's. The
    spec states it, which is what keeps a cancelled turn's stop off a sibling turn's in-flight
    command. An open no turn owns — an off-turn attachment write, a probe — states none, and nothing
    ever stops what it launched."""
    workspace_id, conversation_id = await _conversation()
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")
    sandboxes = _sandboxes(carrier, "e2b", tmp_path)
    turn = _turn(workspace_id, conversation_id)

    with ws(workspace_id):
        await _open_sandbox(sandboxes, RUN_TOKENS, turn, None, {}, None, ())
        await sandboxes.open(conversation_id, None, "run-off-turn", {})

    assert [spec.turn_id for spec in carrier.specs] == [turn.id, None]


async def test_open_carries_the_owning_agents_sandbox_size_on_the_spec(
    db: None, tmp_path: Path
) -> None:
    """Every open — a turn's, or an off-turn attachment write racing it — resolves the size from
    the owning conversation's agent row, so whichever caller creates first creates at the size the
    agent names."""
    workspace_id, conversation_id = await _conversation(sandbox_size="large")
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")

    with ws(workspace_id):
        await _conversation_sandboxes(carrier, tmp_path, "e2b").open(
            conversation_id, None, "run-a", {}
        )

    assert carrier.specs[0].size == "large"


async def test_open_routes_a_resume_backends_handle_to_its_own_carrier(
    db: None, tmp_path: Path
) -> None:
    """Coexistence: a stored handle whose scheme names a `resume_backends` carrier opens on that
    carrier with its resume_id — the conversation's workspace lives on the provider that wrote the
    handle — and the row keeps that backend's prefix, while the deploy's default carrier is never
    touched."""
    workspace_id, conversation_id = await _conversation(handle="e2b:sbx-old")
    resumed = _ResumeRecordingCarrier(container_id="sbx-old")
    fresh = _ResumeRecordingCarrier(container_id="never-created")
    sandboxes = _sandboxes(
        fresh,
        "docker",
        tmp_path,
        resume={
            "e2b": (
                resumed,
                CarrierSpec(name="e2b", factory=lambda: resumed, off_cluster=True),
            )
        },
    )

    with ws(workspace_id):
        await _open_sandbox(
            sandboxes, RUN_TOKENS, _turn(workspace_id, conversation_id), None, {}, None, ()
        )

    assert resumed.specs[0].resume_id == "sbx-old"
    assert fresh.specs == []
    assert await _stored_handle(conversation_id) == "e2b:sbx-old"


async def test_existing_routes_a_resume_backends_handle_to_its_own_carrier(
    db: None, tmp_path: Path
) -> None:
    """The read path routes the same way: a resume backend's stored handle attaches through that
    carrier, never the default's, and never provisions."""
    workspace_id, conversation_id = await _conversation(handle="e2b:sbx-old")
    resumed = _ResumeRecordingCarrier(container_id="sbx-old")
    fresh = _ResumeRecordingCarrier(container_id="never-created")
    sandboxes = _sandboxes(
        fresh,
        "docker",
        tmp_path,
        resume={
            "e2b": (
                resumed,
                CarrierSpec(name="e2b", factory=lambda: resumed, off_cluster=True),
            )
        },
    )

    with ws(workspace_id):
        session = await sandboxes.existing(conversation_id)

    assert session is not None
    assert session.handle.container_id == "sbx-old"
    assert resumed.specs[0].resume_id == "sbx-old"
    assert fresh.specs == []


async def test_open_sandbox_ignores_a_handle_another_backend_wrote_and_overwrites_it(
    db: None, tmp_path: Path
) -> None:
    """A handle from a backend this deploy keeps neither as default nor in `resume_backends` is not
    resumable anywhere: resume_id is None (create fresh) and the fresh id overwrites the row under
    the default backend's prefix."""
    workspace_id, conversation_id = await _conversation(handle="docker:cid-1")
    carrier = _ResumeRecordingCarrier(container_id="sbx-9")

    with ws(workspace_id):
        await _open_sandbox(
            _sandboxes(carrier, "e2b", tmp_path),
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
class _NeverSecret:
    async def secret(self, workspace_id: UUID, account_id: str) -> str:
        raise AssertionError("open_sandbox never reads a token")


HUB_CLI = CliCredential(env="HUB_TOKEN", header="authorization", secret=_NeverSecret())
GIT_CLI = CliCredential(
    env="GH_TOKEN",
    header="authorization",
    secret=_NeverSecret(),
    git=GitWire(host="github.com", basic_user="x-access-token", helper="!gh auth git-credential"),
)


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
    turn = _turn(workspace_id, conversation_id).model_copy(
        update={"agent_id": agent_id, "speaker_member_id": member_id}
    )

    with ws(workspace_id), agent(agent_id):
        await _open_sandbox(
            _sandboxes(carrier, "e2b", tmp_path),
            RUN_TOKENS,
            turn,
            GrantStore(),
            {"hub": HUB_CLI},
            None,
            (),
        )
        scoped = await _grant_cli_env(
            GrantStore(), {"hub": HUB_CLI}, MemberAuthority(member_id), turn.id
        )

    assert _derived_env(carrier.specs[0]) == GIT_PROXY_AUTH_ENV
    assert scoped == {"HUB_TOKEN": grant_sentinel("acct-1")}


async def test_sandbox_authorizer_binds_run_token_and_cli_grants_to_the_acting_member(
    db: None,
) -> None:
    workspace_id, conversation_id = await _conversation()
    agent_id, member_id = await _seed_grant(workspace_id, conversation_id, shared=False)
    turn = _turn(workspace_id, conversation_id).model_copy(update={"agent_id": agent_id})
    common_token = RUN_TOKENS.encode(RunToken(workspace_id, turn.id, WORKSPACE_AUTHORITY))
    proxy = f"http://{common_token}:{PROXY_PASSWORD}@proxy:8080"
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
        authorized = await authorizer.authorize(MemberAuthority(member_id))

    basic = (
        "Basic "
        + base64.b64encode(f"{authorized.handle.run_token}:{PROXY_PASSWORD}".encode()).decode()
    )
    assert RUN_TOKENS.from_proxy_auth(basic) == RunToken(
        workspace_id=workspace_id,
        turn_id=turn.id,
        authority=MemberAuthority(member_id),
    )
    assert authorized.handle.egress_env["HUB_TOKEN"] == grant_sentinel("acct-1")
    assert "HUB_TOKEN" not in base.handle.egress_env


async def test_open_sandbox_exports_the_conversation_identity_stable_across_turns(
    db: None, tmp_path: Path
) -> None:
    """`UFO_CONVERSATION_ID` is what states the conversation to a process in the container: the run
    token carries the workspace, the turn and the acting member, but no conversation id. What a
    branch or a PR trailer stamped with it buys is that it does not move — a follow-up turn resumes
    the same container and the same clone, and opens under the same value, so the name the first
    turn pushed still holds.

    Re-authorization holds it too: a message-bound call re-signs the run token per acting member,
    and `SandboxSession.authorize` rewrites only the proxy vars and drops only the connector CLI
    vars it is handed, whichever member it acts as."""
    workspace_id, conversation_id = await _conversation()
    agent_id, member_id = await _seed_grant(workspace_id, conversation_id, shared=False)
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")
    turn = _turn(workspace_id, conversation_id).model_copy(update={"agent_id": agent_id})
    common_token = RUN_TOKENS.encode(RunToken(workspace_id, turn.id, WORKSPACE_AUTHORITY))
    proxy = f"http://{common_token}:{PROXY_PASSWORD}@proxy:8080"

    with ws(workspace_id), agent(agent_id):
        await _open_sandbox(
            _sandboxes(carrier, "e2b", tmp_path), RUN_TOKENS, turn, None, {}, None, ()
        )
        authorized = await SandboxAuthorizer(
            sandbox=SandboxSession(
                carrier=carrier,
                handle=SandboxHandle(
                    conversation_id=conversation_id,
                    container_id="sbx-1",
                    run_token=common_token,
                    egress_env={
                        **carrier.specs[0].env,
                        "HTTP_PROXY": proxy,
                        "HTTPS_PROXY": proxy,
                        "http_proxy": proxy,
                        "https_proxy": proxy,
                    },
                ),
            ),
            run_tokens=RUN_TOKENS,
            grants=GrantStore(),
            clis={"hub": HUB_CLI},
            turn=turn,
        ).authorize(MemberAuthority(member_id))

    followup = _turn(workspace_id, conversation_id).model_copy(update={"agent_id": agent_id})
    with ws(workspace_id), agent(agent_id):
        await _open_sandbox(
            _sandboxes(carrier, "e2b", tmp_path), RUN_TOKENS, followup, None, {}, None, ()
        )

    assert followup.id != turn.id
    assert carrier.specs[0].env["UFO_CONVERSATION_ID"] == str(conversation_id)
    assert carrier.specs[1].env["UFO_CONVERSATION_ID"] == str(conversation_id)
    assert carrier.specs[0].env[TOOL_BRIDGE_URL_ENV] == TOOL_BRIDGE_URL
    assert carrier.specs[1].env[TOOL_BRIDGE_URL_ENV] == TOOL_BRIDGE_URL
    assert authorized.handle.run_token != common_token
    assert authorized.handle.egress_env["UFO_CONVERSATION_ID"] == str(conversation_id)
    assert authorized.handle.egress_env[TOOL_BRIDGE_URL_ENV] == TOOL_BRIDGE_URL


async def test_open_sandbox_exports_nothing_for_a_foreign_private_grant(
    db: None, tmp_path: Path
) -> None:
    """A private grant of another member is not the turn's to use: a speakerless turn with no
    on-behalf-of member exports no sentinel, so the CLI runs unauthenticated rather than drawing a
    foreign account."""
    workspace_id, conversation_id = await _conversation()
    agent_id, _ = await _seed_grant(workspace_id, conversation_id, shared=False)
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")
    turn = _turn(workspace_id, conversation_id).model_copy(update={"agent_id": agent_id})

    with ws(workspace_id), agent(agent_id):
        await _open_sandbox(
            _sandboxes(carrier, "e2b", tmp_path),
            RUN_TOKENS,
            turn,
            GrantStore(),
            {"hub": HUB_CLI},
            None,
            (),
        )

    assert _derived_env(carrier.specs[0]) == GIT_PROXY_AUTH_ENV


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
            shared=True,
        )
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")
    turn = _turn(workspace_id, conversation_id).model_copy(
        update={"agent_id": agent_id, "speaker_member_id": member_id}
    )

    with ws(workspace_id), agent(agent_id):
        await _open_sandbox(
            _sandboxes(carrier, "e2b", tmp_path),
            RUN_TOKENS,
            turn,
            GrantStore(),
            {"hub": HUB_CLI},
            None,
            (),
        )
        scoped = await _grant_cli_env(
            GrantStore(), {"hub": HUB_CLI}, MemberAuthority(member_id), turn.id
        )

    assert _derived_env(carrier.specs[0]) == {
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
    turn = _turn(workspace_id, conversation_id).model_copy(
        update={"agent_id": agent_id, "speaker_member_id": other_member}
    )

    with ws(workspace_id), agent(agent_id):
        await _open_sandbox(
            _sandboxes(carrier, "e2b", tmp_path),
            RUN_TOKENS,
            turn,
            GrantStore(),
            {"hub": HUB_CLI},
            None,
            (),
        )

    assert _derived_env(carrier.specs[0]) == GIT_PROXY_AUTH_ENV


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
            shared=False,
        )
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")
    turn = _turn(workspace_id, conversation_id).model_copy(
        update={"agent_id": agent_id, "speaker_member_id": member_id}
    )

    with ws(workspace_id), agent(agent_id):
        await _open_sandbox(
            _sandboxes(carrier, "e2b", tmp_path),
            RUN_TOKENS,
            turn,
            GrantStore(),
            {"hub": HUB_CLI},
            None,
            (),
        )

    assert _derived_env(carrier.specs[0]) == GIT_PROXY_AUTH_ENV


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

    with ws(workspace_id):
        await _open_sandbox(
            _sandboxes(carrier, "e2b", tmp_path),
            RUN_TOKENS,
            _turn(workspace_id, conversation_id),
            None,
            {},
            store,
            DATADOG_SLOTS,
        )

    assert _derived_env(carrier.specs[0]) == {
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

    with ws(workspace_id):
        await _open_sandbox(
            _sandboxes(carrier, "e2b", tmp_path),
            RUN_TOKENS,
            _turn(workspace_id, conversation_id),
            None,
            {},
            store,
            DATADOG_SLOTS,
        )

    assert _derived_env(carrier.specs[0]) == {
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

    with caplog.at_level(logging.WARNING, logger="ufo"), ws(workspace_id):
        await _open_sandbox(
            _sandboxes(carrier, "e2b", tmp_path),
            RUN_TOKENS,
            _turn(workspace_id, conversation_id),
            None,
            {},
            store,
            DATADOG_SLOTS,
        )

    assert _derived_env(carrier.specs[0]) == GIT_PROXY_AUTH_ENV
    warned = [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "sandbox.keyed_host_unavailable"
    ]
    assert {entry["slot"] for entry in warned} == {"datadog_api_key", "datadog_application_key"}
    assert not any("169.254" in str(entry) for entry in warned)


PERPLEXITY_SLOT = CredentialSlot(
    name="perplexity_api_key",
    description="api key",
    injection=InjectionTarget(
        host="api.perplexity.ai",
        header="authorization",
        sentinel="SENTINEL_PPLX",
        env="PERPLEXITY_API_KEY",
    ),
)


async def test_open_sandbox_survives_a_keyed_slot_whose_host_will_not_decrypt(
    db: None, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The export half of the isolation the wire already has. A slot whose host selection this
    deploy cannot read — a row another key wrote — withholds its own slot and nothing more: the
    sandbox still opens, so a turn that never touches that provider runs, and the slot's env is
    simply absent rather than present and unusable.

    Isolating here matters as much as at the proxy, because this call is on the path of every
    sandbox open — an escaping fault would fail every turn in the workspace."""
    workspace_id, conversation_id = await _conversation()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(workspace_id, "datadog_api_key", "dd-api-real")
    await store.put(workspace_id, "datadog_application_key", "dd-app-real")
    await store.put(workspace_id, "perplexity_api_key", "pplx-real")
    foreign = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await foreign.put(workspace_id, "datadog_api_host", "api.datadoghq.com")
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")

    with caplog.at_level(logging.WARNING, logger="ufo"), ws(workspace_id):
        await _open_sandbox(
            _sandboxes(carrier, "e2b", tmp_path),
            RUN_TOKENS,
            _turn(workspace_id, conversation_id),
            None,
            {},
            store,
            (*DATADOG_SLOTS, PERPLEXITY_SLOT),
        )

    env = carrier.specs[0].env
    assert "DD_API_KEY" not in env
    assert "DD_APP_KEY" not in env
    assert env["PERPLEXITY_API_KEY"] == "SENTINEL_PPLX"
    withheld = [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "sandbox.credential_slot_failed"
    ]
    assert [entry["slot"] for entry in withheld] == ["datadog_api_key", "datadog_application_key"]
    assert {entry["error_class"] for entry in withheld} == {"InvalidToken"}


async def test_open_sandbox_survives_a_keyed_slot_whose_secret_will_not_decrypt(
    db: None, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The same isolation over the slot's own secret rather than its host selection — the row a key
    rotation leaves unreadable in every workspace that filled the slot. The open reads it before it
    resolves the host, so a fault there fails `_open_sandbox` itself: every turn in the workspace
    would stop, including the ones that touch no keyed provider at all."""
    workspace_id, conversation_id = await _conversation()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    foreign = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await foreign.put(workspace_id, "datadog_api_key", "dd-api-real")
    await foreign.put(workspace_id, "datadog_application_key", "dd-app-real")
    await store.put(workspace_id, "datadog_api_host", "api.datadoghq.com")
    await store.put(workspace_id, "perplexity_api_key", "pplx-real")
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")

    with caplog.at_level(logging.WARNING, logger="ufo"), ws(workspace_id):
        await _open_sandbox(
            _sandboxes(carrier, "e2b", tmp_path),
            RUN_TOKENS,
            _turn(workspace_id, conversation_id),
            None,
            {},
            store,
            (*DATADOG_SLOTS, PERPLEXITY_SLOT),
        )

    env = carrier.specs[0].env
    assert "DD_API_KEY" not in env
    assert "DD_APP_KEY" not in env
    assert env["PERPLEXITY_API_KEY"] == "SENTINEL_PPLX"
    withheld = [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "sandbox.credential_slot_failed"
    ]
    assert [entry["slot"] for entry in withheld] == ["datadog_api_key", "datadog_application_key"]
    assert {entry["error_class"] for entry in withheld} == {"InvalidToken"}


async def test_open_sandbox_configures_git_to_authenticate_to_the_proxy(
    db: None, tmp_path: Path
) -> None:
    """git is the one sandbox client that will not present the run token unprompted: its default
    `anyauth` waits for a `407` challenge the proxy never sends, so its CONNECT arrives unattributed
    and resolves to the base rules. Every turn — holding a grant or a key or neither — gets
    `http.proxyAuthMethod=basic`, so git presents the token on the first CONNECT."""
    workspace_id, conversation_id = await _conversation()
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")
    turn = _turn(workspace_id, conversation_id)

    with ws(workspace_id):
        await _open_sandbox(
            _sandboxes(carrier, "e2b", tmp_path), RUN_TOKENS, turn, None, {}, None, ()
        )

    assert _derived_env(carrier.specs[0]) == {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.proxyAuthMethod",
        "GIT_CONFIG_VALUE_0": "basic",
    }


async def test_open_sandbox_configures_the_connector_git_hosts_credential_helper(
    db: None, tmp_path: Path
) -> None:
    """A connector whose CLI credential names a git host wires that host to the CLI's own credential
    helper, after clearing whatever helper the image set, so a plain `git clone` there authenticates
    exactly as the CLI's clone does: git asks the helper, the helper answers with the sentinel the
    CLI's variable carries, and the proxy swaps the token in. The config names no account and holds
    no secret — with no usable grant the variable is absent and the helper answers nothing."""
    workspace_id, conversation_id = await _conversation()
    carrier = _ResumeRecordingCarrier(container_id="sbx-1")

    with ws(workspace_id):
        await _open_sandbox(
            _sandboxes(carrier, "e2b", tmp_path),
            RUN_TOKENS,
            _turn(workspace_id, conversation_id),
            None,
            {"github": GIT_CLI},
            None,
            (),
        )

    assert _derived_env(carrier.specs[0]) == {
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": "http.proxyAuthMethod",
        "GIT_CONFIG_VALUE_0": "basic",
        "GIT_CONFIG_KEY_1": "credential.https://github.com.helper",
        "GIT_CONFIG_VALUE_1": "",
        "GIT_CONFIG_KEY_2": "credential.https://github.com.helper",
        "GIT_CONFIG_VALUE_2": "!gh auth git-credential",
    }


async def test_open_sandbox_exports_the_committer_identity_of_the_pushing_account(
    db: None, tmp_path: Path
) -> None:
    """A container configures no git identity, so a turn that clones and pushes could not commit at
    all until one is exported. It is the identity the provider attributes to the account the push
    authenticates as, recorded when that account's consent completed — never the member's own
    address, which belongs to another namespace and would attribute the commit to nobody. A CLI
    that clones through no git host exports none, so the identity never outlives the grant that
    pushes."""
    workspace_id, conversation_id = await _conversation()
    agent_id, member_id = await _seed_grant(workspace_id, conversation_id, shared=False)
    with ws(workspace_id), agent(agent_id):
        await GrantStore().record(
            provider="github",
            account_id="acct-gh",
            host="api.github.com",
            grantor_member_id=member_id,
            shared=False,
            account_label="alexg-ufo",
            commit=CommitIdentity(
                name="Alex Graveley",
                email="12345+alexg-ufo@users.noreply.github.com",
            ),
        )
        scoped = await _grant_cli_env(
            GrantStore(),
            {"github": GIT_CLI, "hub": HUB_CLI},
            MemberAuthority(member_id),
            uuid4(),
        )

    assert scoped == {
        "GH_TOKEN": grant_sentinel("acct-gh"),
        "HUB_TOKEN": grant_sentinel("acct-1"),
        "GIT_AUTHOR_NAME": "Alex Graveley",
        "GIT_AUTHOR_EMAIL": "12345+alexg-ufo@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "Alex Graveley",
        "GIT_COMMITTER_EMAIL": "12345+alexg-ufo@users.noreply.github.com",
    }
    assert set(scoped) - {"GH_TOKEN", "HUB_TOKEN"} == GIT_IDENTITY_ENV


async def test_open_sandbox_exports_no_identity_for_a_connection_that_recorded_none(
    db: None, tmp_path: Path
) -> None:
    """A connection carrying no commit identity exports none, so git refuses the commit and says
    so. The alternative — falling back to the member's ufo address — pushes as the account and
    attributes the commit to nobody, and writes that address into public history for good."""
    workspace_id, conversation_id = await _conversation()
    agent_id, member_id = await _seed_grant(workspace_id, conversation_id, shared=False)
    with ws(workspace_id), agent(agent_id):
        await GrantStore().record(
            provider="github",
            account_id="acct-gh",
            host="api.github.com",
            grantor_member_id=member_id,
            shared=False,
            account_label="alexg-ufo",
        )
        scoped = await _grant_cli_env(
            GrantStore(), {"github": GIT_CLI}, MemberAuthority(member_id), uuid4()
        )

    assert scoped == {"GH_TOKEN": grant_sentinel("acct-gh")}


async def test_reauthorization_drops_the_committer_identity_of_another_authority(
    db: None,
) -> None:
    """Re-authorization rewrites the CLI variables per acting member, and the identity goes with
    them: a member holding no GitHub grant commits under no identity rather than under the identity
    the open exported for somebody else."""
    workspace_id, conversation_id = await _conversation()
    agent_id, _ = await _seed_grant(workspace_id, conversation_id, shared=False)
    turn = _turn(workspace_id, conversation_id).model_copy(update={"agent_id": agent_id})
    common_token = RUN_TOKENS.encode(RunToken(workspace_id, turn.id, WORKSPACE_AUTHORITY))
    proxy = f"http://{common_token}:{PROXY_PASSWORD}@proxy:8080"
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
                "GH_TOKEN": grant_sentinel("acct-gh"),
                "GIT_AUTHOR_NAME": "Alex Graveley",
                "GIT_AUTHOR_EMAIL": "12345+alexg-ufo@users.noreply.github.com",
                "GIT_COMMITTER_NAME": "Alex Graveley",
                "GIT_COMMITTER_EMAIL": "12345+alexg-ufo@users.noreply.github.com",
            },
        ),
    )

    with ws(workspace_id), agent(agent_id):
        authorized = await SandboxAuthorizer(
            sandbox=base,
            run_tokens=RUN_TOKENS,
            grants=GrantStore(),
            clis={"github": GIT_CLI},
            turn=turn,
        ).authorize(MemberAuthority(uuid4()))

    assert "GH_TOKEN" not in authorized.handle.egress_env
    assert GIT_IDENTITY_ENV.isdisjoint(authorized.handle.egress_env)


@dataclass
class _UniqueIdCarrier:
    """Stands in for a provider that mints a fresh sandbox per create (e2b's shape): each create
    returns a new id unless the spec resumes one, and attach answers only what a resume names —
    what the claim logic under test arbitrates over. Every assertion is on core's persistence and
    convergence, never this stand-in's own behavior."""

    created: int = 0
    held: asyncio.Event | None = None
    execs: list[tuple[str, str | None]] = field(default_factory=list)
    stops: list[SandboxHandle] = field(default_factory=list)

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        if spec.resume_id is None:
            self.created += 1
        if self.held is not None:
            await self.held.wait()
        proxy = f"http://{spec.run_token}:{PROXY_PASSWORD}@proxy:8080"
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=spec.resume_id or f"sbx-{self.created}",
            run_token=spec.run_token,
            egress_env={
                "HTTP_PROXY": proxy,
                "HTTPS_PROXY": proxy,
                "http_proxy": proxy,
                "https_proxy": proxy,
                **spec.env,
            },
            turn_id=spec.turn_id,
        )

    async def attach(self, spec: SandboxSpec) -> SandboxHandle | None:
        if spec.resume_id is None:
            return None
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=spec.resume_id,
            turn_id=spec.turn_id,
        )

    async def stop_commands(self, handle: SandboxHandle) -> None:
        self.stops.append(handle)

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
    ) -> ExecResult:
        self.execs.append((handle.container_id, handle.run_token))
        return ExecResult(stdout="", stderr="", exit_code=0)

    def read(self, *args: object, **kwargs: object) -> AsyncIterator[bytes]:
        raise AssertionError("these opens never read")

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        raise AssertionError("these opens never dial a port")


def _conversation_sandboxes(carrier: object, tmp_path: Path, backend: str) -> ConversationSandbox:
    return ConversationSandbox(
        carrier=cast(Carrier, carrier),
        backend=backend,
        off_cluster=False,
        image_ref=SANDBOX_IMAGE_REF,
        proxy=PROXY,
        workspace_root=tmp_path / "workspaces",
    )


async def test_concurrent_first_opens_converge_on_one_persisted_sandbox(
    db: None, tmp_path: Path
) -> None:
    """Nothing serializes an off-turn open against a turn's own, so two first opens can both create
    on a provider that mints per call. The compare-and-swap persist arbitrates: exactly one id lands
    on the row, and the loser adopts it — both callers end on the persisted sandbox, so nothing is
    ever written into a sandbox no row references."""
    workspace_id, conversation_id = await _conversation()
    carrier = _UniqueIdCarrier()
    sandboxes = _conversation_sandboxes(carrier, tmp_path, "e2b")

    with ws(workspace_id):
        first, second = await asyncio.gather(
            sandboxes.open(conversation_id, None, "run-a", {}),
            sandboxes.open(conversation_id, None, "run-b", {}),
        )
        stored = await _stored_handle(conversation_id)

    assert stored is not None
    assert first.handle.container_id == second.handle.container_id == stored.removeprefix("e2b:")


def _late(
    carrier: _UniqueIdCarrier, turn: Turn, tmp_path: Path, grants: GrantStore | None = None
) -> _LateSandbox:
    sandboxes = _sandboxes(cast(Carrier, carrier), "e2b", tmp_path)
    return _LateSandbox(
        conversation_id=turn.sandbox_conversation_id or turn.conversation_id,
        turn_id=turn.id,
        open=lambda: _open_sandbox(
            sandboxes,
            RUN_TOKENS,
            turn,
            grants,
            {},
            None,
            (),
        ),
        existing=lambda: sandboxes.existing(turn.sandbox_conversation_id or turn.conversation_id),
    )


async def test_racing_first_operations_create_the_shared_sandbox_once(
    db: None, tmp_path: Path
) -> None:
    """A subagent names its parent's workspace without creating it; racing operations then wait on
    one create and all run in the persisted sandbox."""
    workspace_id, member_conversation = await _conversation()
    child = _turn(workspace_id, uuid4()).model_copy(
        update={"sandbox_conversation_id": member_conversation}
    )
    carrier = _UniqueIdCarrier(held=asyncio.Event())

    with ws(workspace_id):
        sandbox = _late(carrier, child, tmp_path)
        assert sandbox.conversation_id == member_conversation
        assert sandbox.created is False
        assert carrier.created == 0
        assert await _stored_handle(member_conversation) is None
        racing = [asyncio.ensure_future(sandbox.bash("true")) for _ in range(3)]
        await asyncio.sleep(0)
        assert carrier.held is not None
        carrier.held.set()
        await asyncio.gather(*racing)

    assert sandbox.created is True
    assert carrier.created == 1
    assert [container_id for container_id, _ in carrier.execs] == ["sbx-1"] * 3
    assert await _stored_handle(member_conversation) == "e2b:sbx-1"


async def test_an_acting_members_view_binds_the_turns_one_create(db: None, tmp_path: Path) -> None:
    """A message-bound tool call runs under the acting member's own run token, which the authorizer
    derives before anything is created. The view carries that token onto the turn's single sandbox:
    the create is the turn's, the token on the command is the member's."""
    workspace_id, conversation_id = await _conversation()
    agent_id, member_id = await _seed_grant(workspace_id, conversation_id, shared=False)
    turn = _turn(workspace_id, conversation_id).model_copy(update={"agent_id": agent_id})
    carrier = _UniqueIdCarrier()

    with ws(workspace_id), agent(agent_id):
        sandbox = _late(carrier, turn, tmp_path, grants=GrantStore())
        authorizer = SandboxAuthorizer(
            sandbox=sandbox,
            run_tokens=RUN_TOKENS,
            grants=GrantStore(),
            clis={"hub": HUB_CLI},
            turn=turn,
        )
        authorized = await authorizer.authorize(MemberAuthority(member_id))
        assert carrier.created == 0
        await authorized.bash("true")
        await sandbox.bash("true")

    assert carrier.created == 1
    member_token, turn_token = (run_token for _, run_token in carrier.execs)
    assert member_token is not None and turn_token is not None
    basic = "Basic " + base64.b64encode(f"{member_token}:".encode()).decode()
    assert RUN_TOKENS.from_proxy_auth(basic) == RunToken(
        workspace_id=workspace_id,
        turn_id=turn.id,
        authority=MemberAuthority(member_id),
    )
    assert turn_token == RUN_TOKENS.encode(
        RunToken(
            workspace_id=workspace_id,
            turn_id=turn.id,
            authority=WORKSPACE_AUTHORITY,
        )
    )


async def test_a_recovered_cancel_stops_commands_without_creating_a_sandbox(
    db: None, tmp_path: Path
) -> None:
    workspace_id, conversation_id = await _conversation(handle="e2b:sbx-1")
    carrier = _UniqueIdCarrier()
    turn = _turn(workspace_id, conversation_id)

    with ws(workspace_id):
        sandbox = _late(carrier, turn, tmp_path)
        await sandbox.stop_commands()

    assert sandbox.created is False
    assert carrier.created == 0
    assert [handle.turn_id for handle in carrier.stops] == [turn.id]
    assert await _stored_handle(conversation_id) == "e2b:sbx-1"


async def test_a_read_never_creates_and_answers_absent_for_a_gone_sandbox(
    db: None, tmp_path: Path
) -> None:
    """The read path must not answer by provisioning: a conversation whose stored handle names a
    workspace directory the carrier no longer serves reads as absent, and one whose handle another
    backend wrote reads as absent too — in both cases the row keeps exactly the handle it had."""
    workspace_id, conversation_id = await _conversation(handle="local:local")
    sandboxes = _conversation_sandboxes(LocalCarrier(), tmp_path, "local")

    with ws(workspace_id):
        assert await sandboxes.existing(conversation_id) is None
        assert await _stored_handle(conversation_id) == "local:local"

    workspace_id, conversation_id = await _conversation(handle="e2b:sbx-9")
    with ws(workspace_id):
        assert await sandboxes.existing(conversation_id) is None
        assert await _stored_handle(conversation_id) == "e2b:sbx-9"


async def test_workspace_write_refuses_a_body_over_the_cap(db: None, tmp_path: Path) -> None:
    """The off-turn copy-in crosses whole, so the bound is what keeps a producer with no cap of its
    own from sizing this process's memory — refused before any sandbox opens, leaving no handle."""
    workspace_id, conversation_id = await _conversation()
    sandboxes = _conversation_sandboxes(LocalCarrier(), tmp_path, "local")
    oversized = b"x" * (WORKSPACE_WRITE_MAX_BYTES + 1)

    with ws(workspace_id):
        with pytest.raises(ValueError, match="byte limit"):
            await sandboxes.write(conversation_id, "big.bin", oversized)
        assert await _stored_handle(conversation_id) is None


async def test_workspace_listing_warns_when_the_walk_truncates(
    db: None, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A capped walk must not read as complete: the truncation is named in the log, so an operator
    browsing a huge workspace knows the listing is a prefix."""
    workspace_id, conversation_id = await _conversation()
    sandboxes = _conversation_sandboxes(_TruncatingCarrier(), tmp_path, "local")

    with ws(workspace_id):
        await sandboxes.open(conversation_id, None, "run-a", {})
        with caplog.at_level(logging.WARNING, logger="ufo"):
            entries = await sandboxes.entries(conversation_id)

    assert [entry.path for entry in entries] == ["a.txt"]
    assert any("workspace.listing_truncated" in record.message for record in caplog.records)


@pytest.mark.integration
async def test_workspace_listing_omits_git_metadata(
    db: None, tmp_path: Path, sandbox_client: Path
) -> None:
    """The listing is a real `ufo fs` glob through the local carrier, so this needs a built client.
    Every other listing test drives a fake carrier and needs none."""
    workspace_id, conversation_id = await _conversation()
    sandboxes = _conversation_sandboxes(LocalCarrier(), tmp_path, "local")

    with ws(workspace_id):
        await sandboxes.write(conversation_id, "repo/.git/objects/record", b"metadata")
        await sandboxes.write(conversation_id, "repo/src/app.py", b"print('ready')\n")
        entries = await sandboxes.entries(conversation_id)

    assert [entry.path for entry in entries] == ["repo/src/app.py"]


@dataclass
class _TruncatingCarrier:
    """Answers the `ufo fs` glob with a truncated listing — the fake stands in for the container
    walk alone; the warn asserted is core's own."""

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id="local",
            workspace_host_path=spec.workspace_host_path,
        )

    async def attach(self, spec: SandboxSpec) -> SandboxHandle | None:
        return await self.create(spec)

    async def exec(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
    ) -> object:
        listing = {
            "files": [{"path": "/workspace/a.txt", "size": 2, "modified": 1700000000.0}],
            "count": 1,
            "truncated": True,
        }
        return ExecResult(stdout=json.dumps(listing), stderr="", exit_code=0)

    async def file_op(
        self, handle: SandboxHandle, op: str, params: dict[str, object]
    ) -> dict[str, object]:
        return await ufo_fs_file_op(cast(Carrier, self), handle, op, params)

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    def read(self, *args: object, **kwargs: object) -> AsyncIterator[bytes]:
        raise AssertionError("the listing never reads")

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        raise AssertionError("the listing never dials a port")


async def test_a_subagent_turn_opens_the_sandbox_of_the_member_conversation(
    db: None, tmp_path: Path
) -> None:
    """One sandbox per member conversation, not one per delegation: a subagent inherits its
    spawner's `sandbox_conversation_id` at admission, so the sandbox it opens is the member's — the
    files it writes are the ones the parent reads, and a port it brings up is served by a sandbox
    that outlives the child turn."""
    workspace_id, member_conversation = await _conversation()
    child = _turn(workspace_id, uuid4()).model_copy(
        update={"sandbox_conversation_id": member_conversation}
    )

    with ws(workspace_id):
        handle = (
            await _open_sandbox(
                _sandboxes(LocalCarrier(), "local", tmp_path), RUN_TOKENS, child, None, {}, None, ()
            )
        ).handle

    assert handle.conversation_id == member_conversation
    assert handle.workspace_host_path == str(
        (tmp_path / "workspaces" / str(member_conversation)).resolve()
    )
    assert await _stored_handle(member_conversation) == "local:local"


async def test_a_spawned_child_inherits_the_sandbox_conversation(db: None) -> None:
    """Admission is where the sharing is decided, so it is asserted here rather than inferred from
    the field's presence. A child spawned by a member's own turn inherits that conversation; a child
    spawned by a subagent inherits what the subagent already carries, which is still the member's —
    that is what makes the resolution one field read instead of a walk up the spawn chain."""
    workspace_id, member_conversation = await _conversation()
    async with workspace_tx() as connection:
        agent_id = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(tables.agent.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    member_turn = _turn(workspace_id, member_conversation).model_copy(update={"agent_id": agent_id})
    assert member_turn.sandbox_conversation_id is None

    child_conversation = await _admitted_child(workspace_id, member_turn)
    assert child_conversation == member_conversation

    subagent_turn = member_turn.model_copy(
        update={
            "id": uuid4(),
            "conversation_id": uuid4(),
            "sandbox_conversation_id": member_conversation,
        }
    )
    assert await _admitted_child(workspace_id, subagent_turn) == member_conversation


async def _admitted_child(workspace_id: UUID, parent: Turn) -> UUID | None:
    """Spawn one child through the real `_admit` and read back the sandbox conversation it wrote."""
    spawner = Subagents(
        client=cast(DBOSClient, None),
        registry=SubagentRegistry(CORE_SUBAGENT_PROFILES),
        parent=parent,
        audience=SHARED_AUDIENCE,
        authority=parent.authority,
    )
    conversation_id, turn_id = uuid4(), uuid4()
    with ws(workspace_id):
        await spawner._admit(
            conversation_id,
            turn_id,
            agent_id=parent.agent_id,
            profile=GENERAL_PURPOSE,
            inherits_sandbox=True,
            inbound="{}",
            request_fingerprint="sandbox-inheritance",
        )
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(tables.conversation.c.sandbox_conversation_id).where(
                        tables.conversation.c.id == conversation_id
                    )
                )
            ).scalar_one()


def _terminal_sandboxes(tmp_path: Path, terminals: Terminals) -> ConversationSandbox:
    return ConversationSandbox(
        carrier=LocalCarrier(),
        backend="local",
        off_cluster=False,
        image_ref=SANDBOX_IMAGE_REF,
        proxy=PROXY,
        workspace_root=tmp_path / "workspaces",
        terminals=terminals,
    )


async def test_a_fresh_conversation_binds_to_the_connected_terminal(
    db: None, tmp_path: Path
) -> None:
    """First open with a terminal connected claims `client:<directory>` on the row, the session's
    carrier is the terminal's, and no server-side workspace directory is provisioned."""
    workspace_id, conversation_id = await _conversation()
    terminals = Terminals()
    terminals.connect(conversation_id, "/Users/member/proj", None)
    sandboxes = _terminal_sandboxes(tmp_path, terminals)

    with ws(workspace_id):
        session = await sandboxes.open(conversation_id, None, "run-a", {})

    assert isinstance(session.carrier, TerminalCarrier)
    assert session.handle.workspace_host_path == "/Users/member/proj"
    assert await _stored_handle(conversation_id) == "client:/Users/member/proj"
    assert not (tmp_path / "workspaces" / str(conversation_id)).exists()


async def test_claim_binds_whenever_the_handle_is_empty(db: None, tmp_path: Path) -> None:
    """The transport, not the process, owns whether a terminal is served, so a claim always fills an
    empty handle: it writes `client:<directory>` and reports it made the bind. A second claim finds
    the handle set and reports False — the binding is the row's, made once."""
    workspace_id, conversation_id = await _conversation()
    terminals = Terminals()
    terminals.connect(conversation_id, "/Users/member/proj", None)
    sandboxes = _terminal_sandboxes(tmp_path, terminals)

    with ws(workspace_id):
        assert await sandboxes.claim_terminal(conversation_id, "/Users/member/proj") is True
        assert await sandboxes.claim_terminal(conversation_id, "/Users/member/proj") is False

    assert await _stored_handle(conversation_id) == "client:/Users/member/proj"


async def test_a_bound_conversation_refuses_a_terminal_standing_elsewhere(
    db: None, tmp_path: Path
) -> None:
    workspace_id, conversation_id = await _conversation(handle="client:/Users/member/proj")
    terminals = Terminals()
    terminals.connect(conversation_id, "/Users/member/other", None)
    sandboxes = _terminal_sandboxes(tmp_path, terminals)

    with ws(workspace_id):
        with pytest.raises(TerminalGone) as refusal:
            await sandboxes.open(conversation_id, None, "run-a", {})

    assert "/Users/member/proj" in str(refusal.value)
    assert "/Users/member/other" in str(refusal.value)


async def test_a_bound_conversation_refuses_when_no_terminal_is_connected(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(terminal, "ARRIVAL_GRACE_SECONDS", 0.05)
    workspace_id, conversation_id = await _conversation(handle="client:/Users/member/proj")
    sandboxes = _terminal_sandboxes(tmp_path, Terminals())

    with ws(workspace_id):
        with pytest.raises(TerminalGone):
            await sandboxes.open(conversation_id, None, "run-a", {})
        assert await sandboxes.existing(conversation_id) is None


async def test_a_deploy_conversation_keeps_its_carrier_beside_a_connected_terminal(
    db: None, tmp_path: Path
) -> None:
    """A stored handle from the deploy's own backend means the workspace already lives there; a
    terminal arriving later never captures it."""
    workspace_id, conversation_id = await _conversation(handle="local:local")
    (tmp_path / "workspaces" / str(conversation_id)).mkdir(parents=True)
    terminals = Terminals()
    terminals.connect(conversation_id, "/Users/member/proj", None)
    sandboxes = _terminal_sandboxes(tmp_path, terminals)

    with ws(workspace_id):
        session = await sandboxes.open(conversation_id, None, "run-a", {})

    assert isinstance(session.carrier, LocalCarrier)
    assert await _stored_handle(conversation_id) == "local:local"


async def test_a_terminal_conversation_is_reachable_exactly_while_connected(
    db: None, tmp_path: Path
) -> None:
    workspace_id, conversation_id = await _conversation(handle="client:/Users/member/proj")
    terminals = Terminals()
    terminals.connect(conversation_id, "/Users/member/proj", None)
    sandboxes = _terminal_sandboxes(tmp_path, terminals)

    with ws(workspace_id):
        session = await sandboxes.existing(conversation_id)
        assert session is not None
        assert isinstance(session.carrier, TerminalCarrier)
        terminals.disconnect(conversation_id)
        assert await sandboxes.existing(conversation_id) is None


async def test_open_sandbox_commits_as_the_shared_account_another_member_connected(
    db: None, tmp_path: Path
) -> None:
    """A connection shared with the agent's audience serves a member who connected nothing, and the
    identity it exports is the shared account's — the account whose token the push authenticates
    as. Attributing those commits to the acting member instead would name someone GitHub links to
    nothing, and the sharing member's own login is what the push already discloses."""
    workspace_id, conversation_id = await _conversation()
    agent_id, member_id = await _seed_grant(workspace_id, conversation_id, shared=True)
    with ws(workspace_id), agent(agent_id):
        await GrantStore().record(
            provider="github",
            account_id="acct-gh",
            host="api.github.com",
            grantor_member_id=member_id,
            shared=True,
            account_label="alexg-ufo",
            commit=CommitIdentity(
                name="Alex Graveley",
                email="12345+alexg-ufo@users.noreply.github.com",
            ),
        )
        scoped = await _grant_cli_env(
            GrantStore(), {"github": GIT_CLI}, MemberAuthority(uuid4()), uuid4()
        )

    assert scoped == {
        "GH_TOKEN": grant_sentinel("acct-gh"),
        "GIT_AUTHOR_NAME": "Alex Graveley",
        "GIT_AUTHOR_EMAIL": "12345+alexg-ufo@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "Alex Graveley",
        "GIT_COMMITTER_EMAIL": "12345+alexg-ufo@users.noreply.github.com",
    }


async def test_open_sandbox_withdraws_the_identity_when_two_clis_claim_it(
    db: None, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """git reads one pair of ident variables however many hosts a turn clones from, so two CLIs
    declaring a git host cannot both export one. Resolving it by dict order would commit as
    whichever provider came first and say nothing; the identity is withdrawn and logged instead, so
    the turn fails visibly at the commit."""
    workspace_id, conversation_id = await _conversation()
    agent_id, member_id = await _seed_grant(workspace_id, conversation_id, shared=False)
    identity = CommitIdentity(
        name="Alex Graveley", email="12345+alexg-ufo@users.noreply.github.com"
    )
    with ws(workspace_id), agent(agent_id):
        for provider, account in (("github", "acct-gh"), ("gitlab", "acct-gl")):
            await GrantStore().record(
                provider=provider,
                account_id=account,
                host=f"api.{provider}.test",
                grantor_member_id=member_id,
                shared=False,
                account_label="alexg-ufo",
                commit=identity,
            )
        with caplog.at_level(logging.INFO):
            scoped = await _grant_cli_env(
                GrantStore(),
                {"github": GIT_CLI, "gitlab": replace(GIT_CLI, env="GITLAB_TOKEN")},
                MemberAuthority(member_id),
                uuid4(),
            )

    assert scoped == {
        "GH_TOKEN": grant_sentinel("acct-gh"),
        "GITLAB_TOKEN": grant_sentinel("acct-gl"),
    }
    assert [r.getMessage() for r in caplog.records if "git_identity_ambiguous" in r.getMessage()]
