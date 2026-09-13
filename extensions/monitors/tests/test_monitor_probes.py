"""The monitor runner's tick behaviour, over the real off-turn probe capability.

Every probe here is a real command run in a real `LocalCarrier` sandbox through
`ConversationProbes` — the seam the manifest's job holds — and every fire drives the real
`Admission`; only the DBOS enqueue stands in. So what these prove is the chain the RFC claims:
quiet ticks cost a sandbox exec and post nothing, a changed probe fires exactly one arrival that
folds into a live turn or founds the next, three consecutive failures fire, an unreachable terminal
is a counted skip, and an overdue monitor probes once instead of replaying a backlog."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_monitors.manifest import NAME
from ufo_ext_monitors.monitor_runner import (
    CHANGED,
    FAILED,
    FAILURE_THRESHOLD,
    MonitorRunner,
)
from ufo_ext_monitors.monitors import Monitor, MonitorStore, due_monitor_workspaces
from ufo_ext_monitors.monitors import monitor as monitor_table

from ufo.db import workspace_tx
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.exec_env import ProbeEnv
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import SANDBOX_HANDLE_SEP, ProbeTokenCodec, ProxyEndpoint
from ufo.harness.sandbox.terminal import CLIENT_BACKEND
from ufo.harness.untrusted import UNTRUSTED_OPEN
from ufo.runtime.access.connectors import CliCredential
from ufo.runtime.access.grants import GrantStore, grant_sentinel
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.context import ConversationProbes, ExtensionContext, context_for
from ufo.runtime.surfaces.admission import Admission, AdmissionInvoker
from ufo.runtime.turns.audience import SHARED_AUDIENCE
from ufo.runtime.workspace import ws
from ufo.schema import tables

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

TOOL_NARRATION = "watching the run"
MARKER = "probe-state.txt"
PROBE = f"cat {MARKER}"


@dataclass(frozen=True)
class _NeverSecret:
    async def secret(self, workspace_id: UUID, account_id: str) -> str:
        raise AssertionError("the probe environment exports a sentinel, not the account token")


@dataclass
class StubDbos:
    """Stands in for the DBOS client at the admission seam: enqueue records the workflow id so a
    test reads back which turns were placed on the queue, never asserting DBOS itself."""

    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        self.enqueued.append(turn_id)


async def _seed() -> tuple[UUID, UUID, UUID, UUID]:
    workspace_id, member_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="who@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
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
                queue_key="session",
                audience=str(SHARED_AUDIENCE),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id, conversation_id, member_id


def _sandboxes(root: Path) -> ConversationSandbox:
    return ConversationSandbox(
        carrier=LocalCarrier(),
        backend="local",
        off_cluster=False,
        image_ref=SANDBOX_IMAGE_REF,
        proxy=ProxyEndpoint(port=1, ca_cert="test-ca"),
        workspace_root=root,
    )


def _probing_ctx(invoker: AdmissionInvoker, root: Path) -> ExtensionContext:
    """The jobs-role context the runner holds. `context_for` never builds the capability from
    `sandboxes` — the probe environment reads the deploy's declared credential slots, which
    `context_for` does not receive — so a caller passes one in, exactly as `serve` does. A bare
    `ProbeEnv` is the real environment in its degenerate configuration: no grants, no credentials,
    and never a model key, which is the authority an unattended exec gets."""
    sandboxes = _sandboxes(root)
    return context_for(
        NAME,
        frozenset(),
        invoker=invoker,
        sandboxes=sandboxes,
        probes=ConversationProbes(
            sandboxes, ProbeTokenCodec(secret=b"probe-test-secret"), ProbeEnv().exports
        ),
    )


def _invoker(workspace_id: UUID, dbos: StubDbos) -> AdmissionInvoker:
    return AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )


async def _probe_state(root: Path, conversation_id: UUID, text: str) -> None:
    """What the probe reads. The probe is `cat` of one file in the conversation's workspace, so the
    watched state is a real file the sandbox reads and a test writes."""
    workspace = root / str(conversation_id)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / MARKER).write_text(text)


async def _arm(
    ext: ExtensionContext,
    conversation_id: UUID,
    agent_id: UUID,
    member_id: UUID | None,
    *,
    baseline: str,
    command: str = PROBE,
    interval_minutes: int = 5,
    due: bool = True,
    connections: tuple[UUID, ...] = (),
    internet_access: Literal[False] | None = None,
) -> Monitor:
    now = datetime.now(UTC)
    row = await MonitorStore(ext).arm(
        conversation_id=conversation_id,
        agent_id=agent_id,
        name="ci-run",
        command=command,
        interval_minutes=interval_minutes,
        deadline_at=now + timedelta(minutes=600),
        reason="the CI run for pull request 42",
        next_steps="Read the new status and report it.",
        metadata={"pr": 42},
        created_by_member_id=member_id,
        baseline=baseline,
        next_probe_at=now - timedelta(seconds=1) if due else now + timedelta(minutes=5),
        connections=connections,
        internet_access=internet_access,
    )
    return row


async def _row(row_id: UUID) -> sa.RowMapping | None:
    async with workspace_tx() as connection:
        return (
            (await connection.execute(sa.select(monitor_table).where(monitor_table.c.id == row_id)))
            .mappings()
            .one_or_none()
        )


async def _turns(conversation_id: UUID) -> list[sa.RowMapping]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.turn)
                    .where(tables.turn.c.conversation_id == conversation_id)
                    .order_by(tables.turn.c.seq)
                )
            )
            .mappings()
            .all()
        )


async def _arrivals() -> list[str]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.inbound_message.c.body).order_by(tables.inbound_message.c.seq)
                )
            )
            .scalars()
            .all()
        )


async def _due_again(row_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(monitor_table)
            .where(monitor_table.c.id == row_id)
            .values(next_probe_at=datetime.now(UTC) - timedelta(seconds=1))
        )


@pytest.mark.parametrize("armed_by_a_member", [True, False])
async def test_a_probe_carries_the_watchs_exact_connection_capabilities(
    db: None, tmp_path: Path, armed_by_a_member: bool
) -> None:
    """The runner hands the persisted capability snapshot to both enforcement surfaces."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    await _probe_state(tmp_path, conversation_id, "queued\n")
    creator = member_id if armed_by_a_member else None
    env = ProbeEnv()
    asked: list[tuple[UUID, ...]] = []

    async def recording(
        conversation: UUID,
        probe_id: UUID,
        connections: tuple[UUID, ...],
    ) -> dict[str, str]:
        asked.append(connections)
        return await env.exports(conversation, probe_id, connections)

    sandboxes = _sandboxes(tmp_path)
    ext = context_for(
        NAME,
        frozenset(),
        invoker=_invoker(workspace_id, StubDbos()),
        sandboxes=sandboxes,
        probes=ConversationProbes(
            sandboxes, ProbeTokenCodec(secret=b"probe-test-secret"), recording
        ),
    )
    with ws(workspace_id), agent(agent_id):
        scope = tuple(sorted((uuid4(), uuid4()), key=str))
        armed = await _arm(
            ext,
            conversation_id,
            agent_id,
            creator,
            baseline="queued\n",
            connections=scope,
            internet_access=False,
        )
        await MonitorRunner(ctx=ext).run()
        row = await _row(armed.id)

    assert asked == [scope]
    assert row is not None
    assert row["probes_run"] == 1
    assert row["internet_access"] is False


async def test_a_quiet_tick_posts_nothing_and_counts_the_probe(db: None, tmp_path: Path) -> None:
    """The whole economics of a monitor: the probe ran, the output matched, and the model was never
    reached — one sandbox exec, zero rounds, zero arrivals."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    await _probe_state(tmp_path, conversation_id, "queued\n")
    dbos = StubDbos()
    ext = _probing_ctx(_invoker(workspace_id, dbos), tmp_path)
    tick_at = datetime.now(UTC)
    with ws(workspace_id), agent(agent_id):
        armed = await _arm(ext, conversation_id, agent_id, member_id, baseline="queued\n")
        await MonitorRunner(ctx=ext).run()
        row = await _row(armed.id)
        turns = await _turns(conversation_id)

    assert turns == []
    assert dbos.enqueued == []
    assert row is not None
    assert row["probes_run"] == 1
    assert row["quiet_streak"] == 1
    assert row["failure_streak"] == 0
    assert row["skipped"] == 0
    assert row["claimed_by"] is None
    assert row["last_probe_at"] is not None
    assert row["next_probe_at"].replace(tzinfo=UTC) - tick_at > timedelta(minutes=4)


async def test_a_probe_keeps_its_arming_scope_when_an_account_is_connected_later(
    db: None, tmp_path: Path
) -> None:
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    store = GrantStore()
    with ws(workspace_id), agent(agent_id):
        listed = await store.record(
            provider="hub",
            account_id="acct-listed",
            host="api.hub.test",
            grantor_member_id=member_id,
            shared=False,
        )
    expected = grant_sentinel("acct-listed")
    sandboxes = _sandboxes(tmp_path)
    ext = context_for(
        NAME,
        frozenset(),
        invoker=_invoker(workspace_id, StubDbos()),
        sandboxes=sandboxes,
        probes=ConversationProbes(
            sandboxes,
            ProbeTokenCodec(secret=b"probe-test-secret"),
            ProbeEnv(
                grants=store,
                clis={
                    "hub": CliCredential(
                        env="HUB_TOKEN", header="authorization", secret=_NeverSecret()
                    )
                },
            ).exports,
        ),
    )
    with ws(workspace_id), agent(agent_id):
        armed = await _arm(
            ext,
            conversation_id,
            agent_id,
            member_id,
            baseline=expected,
            command='printf "$HUB_TOKEN"',
            connections=(listed,),
        )
        await store.record(
            provider="hub",
            account_id="acct-later",
            host="api.hub.test",
            grantor_member_id=member_id,
            shared=False,
        )
        await MonitorRunner(ctx=ext).run()
        row = await _row(armed.id)
        turns = await _turns(conversation_id)

    assert row is not None
    assert row["probes_run"] == 1
    assert row["quiet_streak"] == 1
    assert turns == []


async def test_changed_output_founds_one_turn_and_retires_the_monitor(
    db: None, tmp_path: Path
) -> None:
    """The fire an idle conversation gets: one turn whose body names the cause, restates the arming
    reason, next steps, and metadata, and holds the new output walled as data. The row is gone, so
    the watch cannot fire twice."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    await _probe_state(tmp_path, conversation_id, "passed\n")
    dbos = StubDbos()
    ext = _probing_ctx(_invoker(workspace_id, dbos), tmp_path)
    with ws(workspace_id), agent(agent_id):
        armed = await _arm(ext, conversation_id, agent_id, member_id, baseline="queued\n")
        await MonitorRunner(ctx=ext).run()
        turns = await _turns(conversation_id)
        row = await _row(armed.id)

    assert row is None
    [turn] = turns
    assert dbos.enqueued == [str(turn["id"])]
    assert turn["admission_source"] == "internal"
    body = turn["inbound"]
    assert f'<monitor_fired name="ci-run" cause="{CHANGED}">' in body
    assert "reason: the CI run for pull request 42" in body
    assert "next_steps: Read the new status and report it." in body
    assert 'metadata: {"pr": 42}' in body
    assert "probes_run: 1  quiet_ticks: 0  skipped: 0" in body
    assert UNTRUSTED_OPEN.format(source="monitor:ci-run") in body
    assert "passed\n" in body


async def test_a_fire_founds_its_own_workspace_turn(db: None, tmp_path: Path) -> None:
    """An automatic monitor fire does not fold into unrelated live workspace work."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    await _probe_state(tmp_path, conversation_id, "passed\n")
    running = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=running,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="running",
                inbound="earlier work",
                admission_source="internal",
                terminal=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    dbos = StubDbos()
    ext = _probing_ctx(_invoker(workspace_id, dbos), tmp_path)
    with ws(workspace_id), agent(agent_id):
        armed = await _arm(ext, conversation_id, agent_id, member_id, baseline="queued\n")
        await MonitorRunner(ctx=ext).run()
        turns = await _turns(conversation_id)
        arrivals = await _arrivals()
        row = await _row(armed.id)

    assert row is None
    assert len(turns) == 2
    assert turns[0]["id"] == running
    assert f'cause="{CHANGED}"' in turns[1]["inbound"]
    assert "passed\n" in turns[1]["inbound"]
    assert arrivals == []


async def test_three_consecutive_failures_fire_and_a_success_resets_the_streak(
    db: None, tmp_path: Path
) -> None:
    """A probe that keeps failing is reported rather than idled on — but only once it has failed
    three times running, and a success in between starts the count over."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    dbos = StubDbos()
    ext = _probing_ctx(_invoker(workspace_id, dbos), tmp_path)
    runner = MonitorRunner(ctx=ext)
    with ws(workspace_id), agent(agent_id):
        armed = await _arm(ext, conversation_id, agent_id, member_id, baseline="queued\n")
        for expected in range(1, FAILURE_THRESHOLD):
            await runner.run()
            row = await _row(armed.id)
            assert row is not None
            assert row["failure_streak"] == expected
            assert await _turns(conversation_id) == []
            await _due_again(armed.id)
        await _probe_state(tmp_path, conversation_id, "queued\n")
        await runner.run()
        recovered = await _row(armed.id)
        assert recovered is not None
        assert recovered["failure_streak"] == 0
        assert recovered["quiet_streak"] == 1
        (tmp_path / str(conversation_id) / MARKER).unlink()
        for _ in range(FAILURE_THRESHOLD):
            await _due_again(armed.id)
            await runner.run()
        turns = await _turns(conversation_id)
        gone = await _row(armed.id)

    assert gone is None
    [turn] = turns
    body = turn["inbound"]
    assert f'<monitor_fired name="ci-run" cause="{FAILED}">' in body
    assert "exit code: 1" in body
    assert f"probes_run: {FAILURE_THRESHOLD + FAILURE_THRESHOLD}" in body
    assert "quiet_ticks: 0" in body


async def test_over_cap_output_spills_to_the_run_and_the_body_names_the_path(
    db: None, tmp_path: Path
) -> None:
    """Signal pushed, payload pulled: the fire carries the bounded output and names its runtime
    path, so the agent reads the rest only if it cares."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    dbos = StubDbos()
    ext = _probing_ctx(_invoker(workspace_id, dbos), tmp_path)
    with ws(workspace_id), agent(agent_id):
        await _arm(
            ext,
            conversation_id,
            agent_id,
            member_id,
            baseline="queued\n",
            command="python3 -c \"print('x' * 20000)\"",
        )
        await MonitorRunner(ctx=ext).run()
        [turn] = await _turns(conversation_id)

    body = turn["inbound"]
    named = next(line for line in body.splitlines() if line.startswith("full_output: "))
    spilled = named.removeprefix("full_output: ")
    assert spilled.startswith(f"$UFO_HOME/runs/{conversation_id.hex}/monitors/ci-run-")
    assert not any((tmp_path / str(conversation_id)).iterdir())
    assert "bytes omitted" in body
    assert len(body) < 20000


async def test_an_unreachable_terminal_is_a_counted_skip(db: None, tmp_path: Path) -> None:
    """A client-carrier conversation whose member has no terminal connected cannot be probed. That
    is neither a failure nor a change: the tick is counted so the eventual fire says how many probes
    never ran."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.conversation)
            .where(tables.conversation.c.id == conversation_id)
            .values(sandbox_handle=f"{CLIENT_BACKEND}{SANDBOX_HANDLE_SEP}{tmp_path / 'terminal'}")
        )
    dbos = StubDbos()
    ext = _probing_ctx(_invoker(workspace_id, dbos), tmp_path)
    with ws(workspace_id), agent(agent_id):
        armed = await _arm(ext, conversation_id, agent_id, member_id, baseline="queued\n")
        await MonitorRunner(ctx=ext).run()
        row = await _row(armed.id)
        turns = await _turns(conversation_id)

    assert turns == []
    assert row is not None
    assert row["skipped"] == 1
    assert row["probes_run"] == 0
    assert row["failure_streak"] == 0
    assert row["claimed_by"] is None


async def test_an_overdue_monitor_probes_once_and_never_replays_a_backlog(
    db: None, tmp_path: Path
) -> None:
    """A row whose next probe passed hours ago — a deploy roll, a stalled runner — probes once and
    is rescheduled one interval from now, so a restart cannot burst-fire."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    await _probe_state(tmp_path, conversation_id, "queued\n")
    dbos = StubDbos()
    ext = _probing_ctx(_invoker(workspace_id, dbos), tmp_path)
    tick_at = datetime.now(UTC)
    with ws(workspace_id), agent(agent_id):
        armed = await _arm(ext, conversation_id, agent_id, member_id, baseline="queued\n")
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(monitor_table)
                .where(monitor_table.c.id == armed.id)
                .values(next_probe_at=tick_at - timedelta(hours=4))
            )
        await MonitorRunner(ctx=ext).run()
        first = await _row(armed.id)
        await MonitorRunner(ctx=ext).run()
        second = await _row(armed.id)

    assert first is not None and second is not None
    assert first["probes_run"] == 1
    assert second["probes_run"] == 1
    assert first["next_probe_at"].replace(tzinfo=UTC) - tick_at > timedelta(minutes=4)


async def test_the_runner_fails_loud_without_the_probe_capability(db: None, tmp_path: Path) -> None:
    """A runner with no probe capability is a runner that would silently never watch anything."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    dbos = StubDbos()
    unprobing = context_for(NAME, frozenset(), invoker=_invoker(workspace_id, dbos))
    with ws(workspace_id), agent(agent_id):
        await _arm(unprobing, conversation_id, agent_id, member_id, baseline="queued\n")
        with pytest.raises(RuntimeError, match="monitor ticks failed: ci-run"):
            await MonitorRunner(ctx=unprobing).run()


async def test_only_due_workspaces_reach_the_runner(db: None, tmp_path: Path) -> None:
    """The candidate seam: a workspace whose only monitor is not yet due never opens a tick, so a
    fleet of armed-but-quiet monitors costs the dispatcher nothing."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    ext = context_for(NAME, frozenset())
    with ws(workspace_id), agent(agent_id):
        armed = await _arm(
            ext, conversation_id, agent_id, member_id, baseline="queued\n", due=False
        )
    assert workspace_id not in await due_monitor_workspaces()()
    await _due_again(armed.id)
    assert workspace_id in await due_monitor_workspaces()()
