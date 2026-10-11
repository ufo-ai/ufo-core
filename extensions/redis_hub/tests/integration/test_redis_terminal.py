"""RedisTerminals end to end against a REAL Redis and a real FilesystemBlobStore — the two-pod fleet
in a test. Two transport instances share one Redis and one blob root: instance `turn` plays the pod
running the turn's workflow, instance `conn` the pod holding the member's connection, and either can
take the reply POST. Every RFC 0027 hop is driven across the two: an op sent from `turn` reaches
`conn`, a reply posted resolves the sender, a copy-in body staged on `turn` is served from `conn`, a
large copy-out reply rides the blob, a reconnect to the other instance mid-op re-delivers exactly
once, and a never-answered op raises `TerminalGone` at its deadline rather than wedging the turn.

A container is launched with `docker run -d --rm -p <port>:6379 redis:7-alpine` exactly as
`tests/integration/test_redis_hub.py` does; `docker`-gated: missing infrastructure fails the
required integration gate and skips an optional local run."""

import asyncio
import shutil
import socket
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import suppress
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import ufo_ext_redis_hub.stream_terminal as stream_terminal
from redis.asyncio import Redis
from redis.exceptions import RedisError
from ufo_ext_redis_hub.stream_terminal import REPLY_INLINE_MAX_BYTES, RedisTerminals
from ufo_testsupport.plugin import integration_dependency_available

from ufo.blob import BlobEntry, FilesystemBlobStore
from ufo.harness.sandbox.session import SandboxSpec
from ufo.harness.sandbox.terminal import (
    TerminalAbsent,
    TerminalCarrier,
    TerminalGone,
    TerminalOp,
    TerminalOpFailed,
)

pytestmark = pytest.mark.docker

REDIS_IMAGE = "redis:7-alpine"
READY_TIMEOUT_S = 10.0
READY_POLL_S = 0.1


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _ping(url: str) -> None:
    client = Redis.from_url(url)
    try:
        await client.ping()
    finally:
        await client.aclose()


@pytest.fixture
def redis_url() -> Iterator[str]:
    if not integration_dependency_available(
        shutil.which("docker") is not None, "Docker executable is not available"
    ):
        pytest.skip("Docker executable is not available")
    port = _free_port()
    started = subprocess.run(
        ["docker", "run", "-d", "--rm", "-p", f"{port}:6379", REDIS_IMAGE],
        capture_output=True,
        text=True,
        check=False,
    )
    reason = f"Docker cannot run {REDIS_IMAGE}: {started.stderr.strip()}"
    if not integration_dependency_available(started.returncode == 0, reason):
        pytest.skip(reason)
    container = started.stdout.strip()
    url = f"redis://127.0.0.1:{port}/0"
    try:
        deadline = time.monotonic() + READY_TIMEOUT_S
        while True:
            try:
                asyncio.run(_ping(url))
                break
            except RedisError:
                if time.monotonic() >= deadline:
                    reason = f"Redis container ({container}) did not become ready in time"
                    integration_dependency_available(False, reason)
                    pytest.skip(reason)
                time.sleep(READY_POLL_S)
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)


@pytest.fixture
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tighten the round-trip margins so timeout paths resolve in a test's time, not a turn's."""
    monkeypatch.setattr(stream_terminal, "ARRIVAL_GRACE_SECONDS", 3.0)
    monkeypatch.setattr(stream_terminal, "ARRIVAL_POLL_SECONDS", 0.05)
    monkeypatch.setattr(stream_terminal, "OP_DEADLINE_SLACK_SECONDS", 0.3)
    monkeypatch.setattr(stream_terminal, "OP_BLOCK_MS", 200)
    monkeypatch.setattr(stream_terminal, "REPLY_BLOCK_MS", 200)


async def _shutdown(instance: RedisTerminals) -> None:
    for hold in list(instance._holds.values()):
        if hold.task is not None:
            hold.task.cancel()
    for task in list(instance._tasks):
        task.cancel()
    await asyncio.sleep(0)
    for client in instance._clients.values():
        with suppress(RedisError):
            await client.aclose()


@pytest.fixture
async def pods(
    redis_url: str, tmp_path: Path, fast: None
) -> AsyncIterator[tuple[RedisTerminals, RedisTerminals]]:
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    turn = RedisTerminals(url=redis_url, blob=blob)
    conn = RedisTerminals(url=redis_url, blob=blob)
    try:
        yield turn, conn
    finally:
        await _shutdown(turn)
        await _shutdown(conn)


async def test_an_op_from_the_turn_pod_reaches_the_connection_pod_and_a_reply_resolves(
    pods: tuple[RedisTerminals, RedisTerminals],
) -> None:
    turn, conn = pods
    conversation_id, member = uuid4(), uuid4()
    conn.connect(conversation_id, "/Users/member/proj", member)

    sending = asyncio.ensure_future(turn.send(conversation_id, "exec", 5, call_id="c1"))
    op = await conn.next_op(conversation_id)
    assert op.kind == "exec" and op.timeout_s == 5
    assert op.call_id == "c1", "the call the op serves crosses the pods with it"
    assert turn.resolve(conversation_id, op.op_id, b'{"exit_code":0}', member_id=member)
    assert await sending == b'{"exit_code":0}'

    conn.disconnect(conversation_id)


async def test_a_copy_in_body_staged_on_the_turn_pod_is_served_from_the_connection_pod(
    pods: tuple[RedisTerminals, RedisTerminals],
) -> None:
    turn, conn = pods
    conversation_id, member = uuid4(), uuid4()
    conn.connect(conversation_id, "/p", member)

    sending = asyncio.ensure_future(
        turn.send(conversation_id, "write", 5, arg="/p/new.txt", body=b"payload")
    )
    op = await conn.next_op(conversation_id)
    assert op.kind == "write" and op.arg == "/p/new.txt"
    assert await conn.staged(conversation_id, op.op_id, member) == b"payload"
    assert await turn.staged(conversation_id, op.op_id, member) == b"payload"
    assert await conn.staged(conversation_id, op.op_id, uuid4()) is None

    assert turn.resolve(conversation_id, op.op_id, b"{}", member_id=member)
    assert await sending == b"{}"
    assert await conn.staged(conversation_id, op.op_id, member) is None

    conn.disconnect(conversation_id)


async def test_a_large_copy_out_reply_rides_the_blob(
    pods: tuple[RedisTerminals, RedisTerminals],
) -> None:
    turn, conn = pods
    conversation_id, member = uuid4(), uuid4()
    conn.connect(conversation_id, "/p", member)
    big = b"\x00\xff" * REPLY_INLINE_MAX_BYTES

    sending = asyncio.ensure_future(turn.send(conversation_id, "read", 5, arg="/p/a.bin"))
    op = await conn.next_op(conversation_id)
    assert conn.resolve(conversation_id, op.op_id, big, member_id=member)
    assert await sending == big

    conn.disconnect(conversation_id)


async def test_a_failed_op_surfaces_as_terminal_op_failed(
    pods: tuple[RedisTerminals, RedisTerminals],
) -> None:
    turn, conn = pods
    conversation_id, member = uuid4(), uuid4()
    conn.connect(conversation_id, "/p", member)

    sending = asyncio.ensure_future(turn.send(conversation_id, "read", 5, arg="/p/missing"))
    op = await conn.next_op(conversation_id)
    assert conn.resolve(
        conversation_id, op.op_id, b"", failed="ENOENT: /p/missing", member_id=member
    )
    with pytest.raises(TerminalOpFailed, match="ENOENT"):
        await sending

    conn.disconnect(conversation_id)


async def test_a_reconnect_to_the_other_pod_mid_op_never_re_delivers(
    pods: tuple[RedisTerminals, RedisTerminals],
) -> None:
    turn, conn = pods
    conversation_id, member = uuid4(), uuid4()
    conn.connect(conversation_id, "/p", member)

    sending = asyncio.ensure_future(turn.send(conversation_id, "exec", 5))
    op = await conn.next_op(conversation_id)

    other = await _next_op_or_timeout(turn, conversation_id, 0.4)
    assert other is None, "a reconnecting pod re-rendered an op the client already received"

    assert conn.resolve(conversation_id, op.op_id, b"{}", member_id=member)
    assert await sending == b"{}"

    conn.disconnect(conversation_id)


async def test_two_pods_racing_next_op_deliver_one_op_exactly_once(
    pods: tuple[RedisTerminals, RedisTerminals],
) -> None:
    turn, conn = pods
    conversation_id, member = uuid4(), uuid4()
    conn.connect(conversation_id, "/p", member)

    sending = asyncio.ensure_future(turn.send(conversation_id, "exec", 5))
    racing = {
        asyncio.ensure_future(turn.next_op(conversation_id)),
        asyncio.ensure_future(conn.next_op(conversation_id)),
    }
    done, pending = await asyncio.wait(racing, timeout=2, return_when=asyncio.FIRST_COMPLETED)
    assert len(done) == 1, "both pods rendered the same op"
    op = done.pop().result()
    for task in pending:
        task.cancel()

    assert conn.resolve(conversation_id, op.op_id, b"{}", member_id=member)
    assert await sending == b"{}"

    conn.disconnect(conversation_id)


async def test_a_never_answered_op_raises_gone_at_the_deadline(
    pods: tuple[RedisTerminals, RedisTerminals],
) -> None:
    """A client's wait always ends: the sender's reply XREAD blocks until the reply appears or the
    op's own deadline elapses, then raises `TerminalGone` and clears state — no wedged turn."""
    turn, conn = pods
    conversation_id, member = uuid4(), uuid4()
    conn.connect(conversation_id, "/p", member)

    with pytest.raises(TerminalGone, match="did not answer"):
        await turn.send(conversation_id, "exec", 1)

    conn.disconnect(conversation_id)


async def test_a_queued_op_gets_its_full_reply_budget_after_the_lock(
    pods: tuple[RedisTerminals, RedisTerminals], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stream_terminal, "HARD_MARGIN_SECONDS", 0.3)
    turn, conn = pods
    conversation_id, member = uuid4(), uuid4()
    conn.connect(conversation_id, "/p", member)

    first = asyncio.ensure_future(turn.send(conversation_id, "exec", 2))
    op_a = await conn.next_op(conversation_id)
    second = asyncio.ensure_future(turn.send(conversation_id, "exec", 2))
    await asyncio.sleep(1.5)
    assert conn.resolve(conversation_id, op_a.op_id, b"a", member_id=member)
    assert await first == b"a"

    op_b = await conn.next_op(conversation_id)
    await asyncio.sleep(1.5)
    assert conn.resolve(conversation_id, op_b.op_id, b"b", member_id=member)
    assert await second == b"b"

    conn.disconnect(conversation_id)


async def test_a_stranger_cannot_resolve_another_members_op(
    pods: tuple[RedisTerminals, RedisTerminals],
) -> None:
    turn, conn = pods
    conversation_id, member = uuid4(), uuid4()
    conn.connect(conversation_id, "/p", member)

    sending = asyncio.ensure_future(turn.send(conversation_id, "exec", 1))
    op = await conn.next_op(conversation_id)
    conn.resolve(conversation_id, op.op_id, b"{}", member_id=uuid4())
    with pytest.raises(TerminalGone):
        await sending

    conn.disconnect(conversation_id)


async def test_a_send_with_no_terminal_connected_raises_gone(
    pods: tuple[RedisTerminals, RedisTerminals],
) -> None:
    turn, _conn = pods
    with pytest.raises(TerminalAbsent, match="no terminal is connected"):
        await turn.send(uuid4(), "exec", 1)


async def test_the_turn_pods_carrier_lands_a_write_on_the_connection_pods_machine(
    pods: tuple[RedisTerminals, RedisTerminals], tmp_path: Path
) -> None:
    turn, conn = pods
    conversation_id, member = uuid4(), uuid4()
    project = tmp_path / "project"
    project.mkdir()
    conn.connect(conversation_id, str(project), member)

    carrier = TerminalCarrier(terminals=turn)
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=conversation_id,
            image_ref="unused",
            workspace_host_path=str(project),
        )
    )

    async def terminal_client() -> None:
        op = await conn.next_op(conversation_id)
        body = await conn.staged(conversation_id, op.op_id, member)
        assert body is not None
        await asyncio.to_thread(Path(op.arg).write_bytes, body)
        conn.resolve(conversation_id, op.op_id, b"{}", member_id=member)

    client = asyncio.ensure_future(terminal_client())
    await carrier.write(handle, "/workspace/PROOF.txt", b"hello")
    await client

    assert (project / "PROOF.txt").read_bytes() == b"hello"
    conn.disconnect(conversation_id)


async def test_a_claimed_op_is_never_re_handed_out_within_its_window(
    pods: tuple[RedisTerminals, RedisTerminals],
) -> None:
    turn, conn = pods
    conversation_id, member = uuid4(), uuid4()
    conn.connect(conversation_id, "/p", member)

    sending = asyncio.ensure_future(turn.send(conversation_id, "exec", 5))
    first = await conn.next_op(conversation_id)
    assert await _next_op_or_timeout(conn, conversation_id, 0.4) is None
    assert await _next_op_or_timeout(turn, conversation_id, 0.4) is None

    assert conn.resolve(conversation_id, first.op_id, b"{}", member_id=member)
    assert await sending == b"{}"
    conn.disconnect(conversation_id)


async def test_a_received_op_is_never_re_delivered_by_the_reply_reconnect(
    pods: tuple[RedisTerminals, RedisTerminals],
) -> None:
    turn, conn = pods
    conversation_id, member = uuid4(), uuid4()
    conn.connect(conversation_id, "/p", member)

    sending = asyncio.ensure_future(turn.send(conversation_id, "exec", 5))
    op = await conn.next_op(conversation_id)
    assert await _next_op_or_timeout(conn, conversation_id, 0.4, exclude=op.op_id) is None

    assert conn.resolve(conversation_id, op.op_id, b"{}", member_id=member)
    assert await sending == b"{}"
    conn.disconnect(conversation_id)


async def test_an_entry_past_its_window_from_a_dead_sender_is_reaped_never_rendered(
    pods: tuple[RedisTerminals, RedisTerminals],
) -> None:
    _turn, conn = pods
    conversation_id, member = uuid4(), uuid4()
    conn.connect(conversation_id, "/p", member)
    client = conn._client()
    await client.xadd(
        conn._op_stream(conversation_id),
        {
            "op_id": uuid4().hex,
            "kind": "exec",
            "timeout_s": "5",
            "name": "",
            "arg": "",
            "params": "x",
        },
        id="100-0",
    )

    assert await _next_op_or_timeout(conn, conversation_id, 0.6) is None
    assert await client.xlen(conn._op_stream(conversation_id)) == 0
    conn.disconnect(conversation_id)


async def test_the_gate_fails_closed_for_a_memberless_binding_and_off_conversation(
    pods: tuple[RedisTerminals, RedisTerminals],
) -> None:
    turn, conn = pods
    conversation_id = uuid4()
    conn.connect(conversation_id, "/p", None)

    sending = asyncio.ensure_future(turn.send(conversation_id, "write", 5, arg="/p/a", body=b"x"))
    op = await conn.next_op(conversation_id)
    assert await conn.staged(conversation_id, op.op_id, uuid4()) is None
    assert await conn.staged(conversation_id, op.op_id, None) == b"x"
    assert await conn.staged(uuid4(), op.op_id, None) is None

    assert conn.resolve(conversation_id, op.op_id, b"{}", member_id=None)
    assert await sending == b"{}"
    conn.disconnect(conversation_id)


async def test_an_inflight_op_keeps_the_terminal_reachable_when_the_bind_expires(
    pods: tuple[RedisTerminals, RedisTerminals],
) -> None:
    """No pod holds a connection while the client executes, so nothing refreshes the liveness key
    and it expires for any op longer than its TTL."""
    turn, conn = pods
    conversation_id, member = uuid4(), uuid4()
    conn.connect(conversation_id, "/p", member)

    sending = asyncio.ensure_future(turn.send(conversation_id, "exec", 5))
    op = await conn.next_op(conversation_id)
    await conn._client().delete(conn._bind_key(conversation_id))

    reached = await turn.arrived(conversation_id, 0.0)
    assert reached is not None and reached.cwd == "/p"

    assert conn.resolve(conversation_id, op.op_id, b"{}", member_id=member)
    assert await sending == b"{}"
    conn.disconnect(conversation_id)


async def test_the_binding_survives_a_disconnect(
    pods: tuple[RedisTerminals, RedisTerminals],
) -> None:
    turn, conn = pods
    conversation_id, member = uuid4(), uuid4()
    conn.connect(conversation_id, "/p", member)
    assert await turn.arrived(conversation_id, 1.0) is not None

    conn.disconnect(conversation_id)
    assert await turn.arrived(conversation_id, 0.0) is not None


async def test_attach_finds_a_binding_a_peer_pod_holds(
    pods: tuple[RedisTerminals, RedisTerminals],
) -> None:
    turn, conn = pods
    conversation_id, member = uuid4(), uuid4()
    conn.connect(conversation_id, "/proj", member)
    assert await turn.arrived(conversation_id, 1.0) is not None

    carrier = TerminalCarrier(terminals=turn)
    handle = await carrier.attach(
        SandboxSpec(
            conversation_id=conversation_id,
            image_ref="unused",
            workspace_host_path="/proj",
            resume_id="/proj",
        )
    )
    assert handle is not None
    assert handle.workspace_host_path == "/proj"
    assert handle.runtime_root == f"$UFO_HOME/runs/{conversation_id.hex}"
    conn.disconnect(conversation_id)


async def test_a_wedged_blob_store_does_not_push_send_past_a_bound(
    redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stream_terminal, "BLOB_OP_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(stream_terminal, "ARRIVAL_GRACE_SECONDS", 3.0)
    monkeypatch.setattr(stream_terminal, "ARRIVAL_POLL_SECONDS", 0.05)
    terminals = RedisTerminals(url=redis_url, blob=_HangingBlob())
    conversation_id, member = uuid4(), uuid4()
    terminals.connect(conversation_id, "/p", member)
    try:
        started = time.monotonic()
        with pytest.raises(TerminalGone):
            await terminals.send(conversation_id, "write", 5, arg="/p/a", body=b"payload")
        assert time.monotonic() - started < 2.0
    finally:
        await _shutdown(terminals)


async def test_a_redis_fault_becomes_terminal_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stream_terminal, "SOCKET_CONNECT_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(stream_terminal, "SOCKET_TIMEOUT_SECONDS", 0.3)
    dead = RedisTerminals(url="redis://127.0.0.1:1/0", blob=FilesystemBlobStore(root=tmp_path))
    try:
        with pytest.raises(TerminalGone):
            await dead.next_op(uuid4())
    finally:
        await _shutdown(dead)


async def test_the_client_sets_an_explicit_socket_timeout(
    pods: tuple[RedisTerminals, RedisTerminals],
) -> None:
    turn, _conn = pods
    kwargs = turn._client().connection_pool.connection_kwargs
    assert kwargs.get("socket_timeout") == stream_terminal.SOCKET_TIMEOUT_SECONDS
    assert kwargs["socket_timeout"] is not None


class _HangingBlob:
    """A blob store whose reads and writes never return — the wedge a bounded send must survive."""

    async def put(self, key: str, data: bytes) -> None:
        await asyncio.sleep(3600)

    async def get(self, key: str) -> bytes:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    async def exists(self, key: str) -> bool:
        return False

    async def delete(self, key: str) -> None:
        await asyncio.sleep(3600)

    def get_stream(self, key: str) -> AsyncIterator[bytes]:
        raise NotImplementedError

    async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None:
        raise NotImplementedError

    async def list(self, prefix: str) -> tuple[BlobEntry, ...]:
        return ()


async def _next_op_or_timeout(
    instance: RedisTerminals,
    conversation_id: UUID,
    after_s: float,
    exclude: str | None = None,
) -> TerminalOp | None:
    task = asyncio.ensure_future(instance.next_op(conversation_id, exclude))
    try:
        return await asyncio.wait_for(asyncio.shield(task), after_s)
    except TimeoutError:
        task.cancel()
        return None
