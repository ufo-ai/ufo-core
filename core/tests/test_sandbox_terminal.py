"""The terminal rendezvous and its carrier: one slot per conversation, surviving the reconnects the
client's 85s-held streams make routine, never answering a turn with silence — and the carrier whose
every op is asked over it, the test playing the connected terminal."""

import asyncio
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from ufo.sandbox import terminal
from ufo.sandbox.session import (
    EGRESS_CA_CERT_ENV,
    WORKSPACE_DIR,
    ProxyEndpoint,
    SandboxSpec,
    SandboxUnreachable,
)
from ufo.sandbox.terminal import (
    TerminalCarrier,
    TerminalGone,
    TerminalOp,
    Terminals,
)


def _spec(conversation_id: UUID, cwd: str, public_url: str | None = None) -> SandboxSpec:
    return SandboxSpec(
        conversation_id=conversation_id,
        image_ref="unused",
        workspace_host_path=cwd,
        proxy=ProxyEndpoint(port=8080, ca_cert="ca-pem", public_url=public_url),
        run_token="run-token",
        env={"UFO_CONVERSATION_ID": str(conversation_id)},
    )


async def _answer(terminals: Terminals, conversation_id: UUID, reply: bytes) -> TerminalOp:
    op = await terminals.next_op(conversation_id)
    assert terminals.resolve(conversation_id, op.op_id, reply)
    return op


async def _refuse(terminals: Terminals, conversation_id: UUID, failed: str) -> TerminalOp:
    op = await terminals.next_op(conversation_id)
    assert terminals.resolve(conversation_id, op.op_id, b"", failed=failed)
    return op


def _trailer_params(op: TerminalOp) -> dict:
    prefix, _, invocation = op.payload.rpartition("return main(")
    assert prefix and invocation.endswith("); }")
    return json.loads(invocation[: -len("); }")])


async def test_send_answers_with_the_resolved_reply() -> None:
    terminals = Terminals()
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/Users/member/proj", uuid4())

    async def answer() -> None:
        op = await terminals.next_op(conversation_id)
        assert op.kind == "exec" and op.timeout_s == 5
        assert terminals.resolve(conversation_id, op.op_id, b'{"exit_code":0}')

    answering = asyncio.ensure_future(answer())
    reply = await terminals.send(conversation_id, "exec", 5, payload="main({});")
    await answering
    assert reply == b'{"exit_code":0}'


async def test_send_without_a_connected_terminal_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(terminal, "ARRIVAL_GRACE_SECONDS", 0.05)
    with pytest.raises(TerminalGone):
        await Terminals().send(uuid4(), "exec", 1)


async def test_send_waits_out_the_gap_between_held_streams() -> None:
    """A turn's op landing while the client is between polls is the normal case: the send waits for
    the reconnect instead of failing a turn whose member is right here."""
    terminals = Terminals()
    conversation_id = uuid4()
    sending = asyncio.ensure_future(terminals.send(conversation_id, "exec", 5))
    await asyncio.sleep(0.02)
    terminals.connect(conversation_id, "/p", None)
    op = await terminals.next_op(conversation_id)
    assert terminals.resolve(conversation_id, op.op_id, b"{}")
    assert await sending == b"{}"


async def test_a_second_sender_serializes_behind_the_first() -> None:
    """A terminal runs one op at a time — a background subagent's op, or an off-turn attachment
    write, queues behind the in-flight op and runs when it frees, rather than failing."""
    terminals = Terminals()
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    first = asyncio.ensure_future(terminals.send(conversation_id, "exec", 5, arg="one"))
    await asyncio.sleep(0)
    second = asyncio.ensure_future(terminals.send(conversation_id, "exec", 5, arg="two"))
    await asyncio.sleep(0)

    op_one = await terminals.next_op(conversation_id)
    assert op_one.arg == "one"
    assert terminals.in_flight(conversation_id).arg == "one"
    assert terminals.resolve(conversation_id, op_one.op_id, b"1")
    assert await first == b"1"

    op_two = await terminals.next_op(conversation_id)
    assert op_two.arg == "two"
    assert terminals.resolve(conversation_id, op_two.op_id, b"2")
    assert await second == b"2"


async def test_timeout_raises_gone_and_clears_the_op(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(terminal, "OP_DEADLINE_SLACK_SECONDS", 0.0)
    terminals = Terminals()
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    with pytest.raises(TerminalGone):
        await terminals.send(conversation_id, "write", 0, body=b"payload")
    assert terminals.in_flight(conversation_id) is None
    assert not terminals.resolve(conversation_id, "0" * 32, b"{}")


async def test_a_stale_reply_is_dropped_without_disturbing_the_waiter() -> None:
    terminals = Terminals()
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    sending = asyncio.ensure_future(terminals.send(conversation_id, "exec", 5))
    await asyncio.sleep(0)
    assert not terminals.resolve(conversation_id, "not-the-op", b"wrong")
    op = await terminals.next_op(conversation_id)
    assert terminals.resolve(conversation_id, op.op_id, b"right")
    assert await sending == b"right"


async def test_the_slot_survives_a_reconnect_mid_op() -> None:
    terminals = Terminals()
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    sending = asyncio.ensure_future(terminals.send(conversation_id, "exec", 5))
    await asyncio.sleep(0)
    terminals.connect(conversation_id, "/p", None)
    terminals.disconnect(conversation_id)
    op = await terminals.next_op(conversation_id)
    assert terminals.resolve(conversation_id, op.op_id, b"{}")
    assert await sending == b"{}"


async def test_staged_bytes_serve_only_the_op_that_carries_them() -> None:
    terminals = Terminals()
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    sending = asyncio.ensure_future(
        terminals.send(conversation_id, "write", 5, arg="/p/a", body=b"content")
    )
    await asyncio.sleep(0)
    op = await terminals.next_op(conversation_id)
    assert terminals.staged(conversation_id, op.op_id) == b"content"
    assert terminals.staged(conversation_id, "another-op") is None
    terminals.resolve(conversation_id, op.op_id, b"{}")
    await sending
    assert terminals.staged(conversation_id, op.op_id) is None


async def test_the_last_disconnect_removes_an_idle_slot() -> None:
    terminals = Terminals()
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    terminals.connect(conversation_id, "/p", None)
    terminals.disconnect(conversation_id)
    assert terminals.workspace(conversation_id) is not None
    terminals.disconnect(conversation_id)
    assert terminals.workspace(conversation_id) is None


async def test_an_idle_reconnect_from_elsewhere_rebinds_the_slot() -> None:
    terminals = Terminals()
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/first", None)
    terminals.disconnect(conversation_id)
    terminals.connect(conversation_id, "/second", None)
    workspace = terminals.workspace(conversation_id)
    assert workspace is not None and workspace.cwd == "/second"


async def test_create_refuses_without_a_binding_and_names_a_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(terminal, "ARRIVAL_GRACE_SECONDS", 0.05)
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    with pytest.raises(TerminalGone):
        await carrier.create(_spec(conversation_id, "/Users/member/proj"))
    terminals.connect(conversation_id, "/Users/member/elsewhere", None)
    with pytest.raises(TerminalGone) as refusal:
        await carrier.create(_spec(conversation_id, "/Users/member/proj"))
    assert "/Users/member/proj" in str(refusal.value)
    assert "/Users/member/elsewhere" in str(refusal.value)


async def test_create_binds_the_directory_and_the_metered_proxy() -> None:
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/Users/member/proj", None)
    handle = await carrier.create(
        _spec(conversation_id, "/Users/member/proj", public_url="https://proxy.example.com")
    )
    assert handle.container_id == "/Users/member/proj"
    assert handle.egress_env["HTTPS_PROXY"] == "https://run-token:@proxy.example.com"
    assert handle.egress_env["UFO_EGRESS_CA_CERT"] == "ca-pem"
    assert handle.egress_env["UFO_CONVERSATION_ID"] == str(conversation_id)


async def test_create_without_a_public_proxy_fails_closed_on_loopback() -> None:
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    handle = await carrier.create(_spec(conversation_id, "/p"))
    assert handle.egress_env["HTTP_PROXY"] == "http://run-token:@127.0.0.1:8080"


async def test_attach_answers_only_the_bound_directory() -> None:
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    spec = _spec(conversation_id, "/p")
    assert await carrier.attach(_resuming(spec, "/p")) is None
    terminals.connect(conversation_id, "/p", None)
    assert await carrier.attach(_resuming(spec, "/elsewhere")) is None
    attached = await carrier.attach(_resuming(spec, "/p"))
    assert attached is not None and attached.workspace_host_path == "/p"


def _resuming(spec: SandboxSpec, resume_id: str) -> SandboxSpec:
    return SandboxSpec(
        conversation_id=spec.conversation_id,
        image_ref=spec.image_ref,
        workspace_host_path=spec.workspace_host_path,
        proxy=spec.proxy,
        run_token=spec.run_token,
        resume_id=resume_id,
    )


async def test_exec_ships_the_program_with_rewritten_argv_and_decodes_the_reply() -> None:
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/Users/member/proj", None)
    handle = await carrier.create(_spec(conversation_id, "/Users/member/proj"))
    reply = json.dumps(
        {"exit_code": 0, "stdout_hex": b"out\n".hex(), "stderr_hex": b"".hex()}
    ).encode()
    running = asyncio.ensure_future(carrier.exec(handle, ("cat", "/workspace/a.txt"), timeout_s=30))
    await asyncio.sleep(0)
    op = await _answer(terminals, conversation_id, reply)
    result = await running
    assert op.kind == "exec" and op.timeout_s == 30
    params = _trailer_params(op)
    assert params["argv"] == ["cat", "/Users/member/proj/a.txt"]
    assert params["env"]["HTTP_PROXY"].startswith("http://run-token:@")
    assert "function main" in op.payload
    assert result.exit_code == 0 and result.stdout == "out\n"


async def test_write_stages_the_bytes_and_maps_a_refusal() -> None:
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    handle = await carrier.create(_spec(conversation_id, "/p"))
    writing = asyncio.ensure_future(carrier.write(handle, "/workspace/new.txt", b"content"))
    await asyncio.sleep(0)
    op = await terminals.next_op(conversation_id)
    assert terminals.staged(conversation_id, op.op_id) == b"content"
    assert op.kind == "write" and op.arg == "/p/new.txt" and op.payload == ""
    terminals.resolve(conversation_id, op.op_id, b"{}")
    await writing

    failing = asyncio.ensure_future(carrier.write(handle, "/workspace/no.txt", b"x"))
    await asyncio.sleep(0)
    await _refuse(terminals, conversation_id, "EACCES: cannot rename")
    with pytest.raises(OSError, match="EACCES"):
        await failing


async def test_a_workspace_named_subdirectory_maps_under_the_root_once() -> None:
    """A member's own subdirectory named `workspace` yields the logical path
    `/workspace/workspace/notes.md`; the carrier must strip the one leading `/workspace` and land it
    under the root once, never rewrite every occurrence into a bogus nested tree."""
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/Users/me/proj", None)
    handle = await carrier.create(_spec(conversation_id, "/Users/me/proj"))
    writing = asyncio.ensure_future(carrier.write(handle, "/workspace/workspace/notes.md", b"x"))
    await asyncio.sleep(0)
    op = await terminals.next_op(conversation_id)
    assert op.arg == "/Users/me/proj/workspace/notes.md"
    terminals.resolve(conversation_id, op.op_id, b"{}")
    await writing


async def test_read_streams_the_reply_and_maps_a_missing_file() -> None:
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    handle = await carrier.create(_spec(conversation_id, "/p"))

    async def read_all() -> bytes:
        chunks = b""
        async for chunk in carrier.read(handle, "/workspace/a.bin"):
            chunks += chunk
        return chunks

    reading = asyncio.ensure_future(read_all())
    await asyncio.sleep(0)
    op = await _answer(terminals, conversation_id, b"\x00binary\xff")
    assert op.kind == "read" and op.arg == "/p/a.bin"
    assert await reading == b"\x00binary\xff"

    missing = asyncio.ensure_future(read_all())
    await asyncio.sleep(0)
    await _refuse(terminals, conversation_id, "ENOENT: /p/a.bin")
    with pytest.raises(FileNotFoundError):
        await missing


async def test_file_op_ships_the_program_and_maps_the_handled_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(terminal, "CLIENT_PROGRAM_DIR", tmp_path)
    (tmp_path / "prelude.js").write_text("function __start__(argv) {}")
    (tmp_path / "exec.js").write_text("function main(params) { print('{}'); }")
    (tmp_path / "grep.js").write_text("function main(params) { print('{}'); }")
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    handle = await carrier.create(_spec(conversation_id, "/p"))

    with pytest.raises(RuntimeError, match="no client program"):
        await carrier.file_op(handle, "glob", {"pattern": "**/*"})

    running = asyncio.ensure_future(
        carrier.file_op(
            handle, "grep", {"pattern": "x", "path": "/workspace", "workspace": "/workspace"}
        )
    )
    await asyncio.sleep(0)
    exec_ok = json.dumps({"exit_code": 0, "stdout_hex": "", "stderr_hex": ""}).encode()
    listing = await _answer(terminals, conversation_id, exec_ok)
    assert listing.kind == "exec"
    enum_command = _trailer_params(listing)["argv"][2]
    assert enum_command.startswith("UFO_WALK_ROOT=/p\n")
    assert "grep-enum" in enum_command
    op = await _answer(terminals, conversation_id, b'{"matches": []}')
    assert op.kind == "fileop"
    assert _trailer_params(op) == {
        "pattern": "x",
        "path": "/p",
        "workspace": "/p",
        "enum": "grep-enum",
    }
    assert "function main(params)" in op.payload
    assert op.payload.startswith("function __start__")
    assert await running == {"matches": []}

    refused = asyncio.ensure_future(
        carrier.file_op(handle, "grep", {"pattern": "x", "workspace": WORKSPACE_DIR})
    )
    await asyncio.sleep(0)
    await _answer(terminals, conversation_id, exec_ok)
    await _answer(terminals, conversation_id, b'{"error": "invalid output_mode: y"}')
    with pytest.raises(ValueError, match="invalid output_mode"):
        await refused


async def test_a_walk_enumerates_a_bound_directory_that_contains_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A member launched from a directory whose path contains `/workspace` (`~/workspace/api`) must
    still walk: the enumeration's already-concrete root must not go through the `/workspace`→root
    rewrite a second time, which would mangle it into a path `find` cannot reach."""
    monkeypatch.setattr(terminal, "CLIENT_PROGRAM_DIR", tmp_path)
    (tmp_path / "prelude.js").write_text("function __start__(argv) {}")
    (tmp_path / "exec.js").write_text("function main(params) { print('{}'); }")
    (tmp_path / "grep.js").write_text("function main(params) { print('{}'); }")
    root = "/Users/alex/workspace/api"
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, root, None)
    handle = await carrier.create(_spec(conversation_id, root))

    running = asyncio.ensure_future(
        carrier.file_op(
            handle, "grep", {"pattern": "x", "path": WORKSPACE_DIR, "workspace": WORKSPACE_DIR}
        )
    )
    await asyncio.sleep(0)
    exec_ok = json.dumps({"exit_code": 0, "stdout_hex": "", "stderr_hex": ""}).encode()
    listing = await _answer(terminals, conversation_id, exec_ok)
    enum_command = _trailer_params(listing)["argv"][2]
    assert f"UFO_WALK_ROOT={root}\n" in enum_command
    assert "/Users/alex/Users/alex" not in enum_command
    op = await _answer(terminals, conversation_id, b'{"matches": []}')
    assert _trailer_params(op)["path"] == root
    assert await running == {"matches": []}


async def test_dial_is_unreachable() -> None:
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    handle = await carrier.create(_spec(conversation_id, "/p"))
    with pytest.raises(SandboxUnreachable):
        await carrier.dial(handle, 9222)


def test_a_sender_on_another_loop_is_woken_from_this_one() -> None:
    """The shape production actually runs: the turn's send lives on the DBOS workflow loop in its
    own thread, the surface resolves from serve's — the wakeup must hop loops, exactly as the hub's
    delivery does."""
    import threading as _threading

    terminals = Terminals()
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    outcome: list[bytes] = []

    def worker() -> None:
        outcome.append(
            asyncio.run(terminals.send(conversation_id, "exec", 10, payload="main({});"))
        )

    thread = _threading.Thread(target=worker)
    thread.start()

    async def answer() -> None:
        async with asyncio.timeout(10):
            while (op := terminals.in_flight(conversation_id)) is None:  # noqa: ASYNC110
                await asyncio.sleep(0.01)
            assert terminals.resolve(conversation_id, op.op_id, b"cross-loop")

    asyncio.run(answer())
    thread.join(timeout=10)
    assert not thread.is_alive() and outcome == [b"cross-loop"]


def test_exec_js_spells_the_python_constants() -> None:
    """`exec.js` reads the CA off the env name `session.py` declares and exports the four bundle
    variables `local.py` also spells — cross-language duplication a rename would silently break,
    so the shipped source is pinned to the constants here."""
    source = (terminal.CLIENT_PROGRAM_DIR / "exec.js").read_text()
    assert EGRESS_CA_CERT_ENV in source
    for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS"):
        assert name in source
