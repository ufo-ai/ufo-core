"""The terminal rendezvous and its carrier: one slot per conversation, surviving the reconnects the
client's 85s-held streams make routine, never answering a turn with silence — and the carrier whose
every op is asked over it, the test playing the connected terminal."""

import asyncio
import base64
import io
import json
import os
import subprocess
import threading
import zipfile
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest

from ufo.harness.sandbox import terminal
from ufo.harness.sandbox.session import (
    WORKSPACE_DIR,
    ProxyEndpoint,
    SandboxSession,
    SandboxSpec,
    SandboxUnreachable,
)
from ufo.harness.sandbox.terminal import (
    EXEC_TIMEOUT_CODE,
    TerminalAbsent,
    TerminalCarrier,
    TerminalGone,
    TerminalOp,
    Terminals,
    TerminalTransport,
)
from ufo.runtime.media.document_renderer import DOCUMENT_INPUT_MAX_BYTES, DocumentRenderer


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


def _op_params(op: TerminalOp) -> dict:
    """The params the op carries for the client's native implementation to read."""
    return json.loads(op.params)


async def test_send_answers_with_the_resolved_reply() -> None:
    terminals = Terminals()
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/Users/member/proj", uuid4())

    async def answer() -> None:
        op = await terminals.next_op(conversation_id)
        assert op.kind == "exec" and op.timeout_s == 5
        assert terminals.resolve(conversation_id, op.op_id, b'{"exit_code":0}')

    answering = asyncio.ensure_future(answer())
    reply = await terminals.send(conversation_id, "exec", 5)
    await answering
    assert reply == b'{"exit_code":0}'


async def test_send_without_a_connected_terminal_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(terminal, "ARRIVAL_GRACE_SECONDS", 0.05)
    with pytest.raises(TerminalAbsent):
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
    assert await terminals.staged(conversation_id, op.op_id) == b"content"
    assert await terminals.staged(conversation_id, "another-op") is None
    terminals.resolve(conversation_id, op.op_id, b"{}")
    await sending
    assert await terminals.staged(conversation_id, op.op_id) is None


async def test_the_member_gate_rejects_a_stranger_and_admits_the_binding_member() -> None:
    """The in-process transport, reached through the `TerminalTransport` Protocol: an op's staged
    bytes and its reply answer only the member the binding named, and `None` leaves the gate open
    for the direct-drive path."""
    terminals: TerminalTransport = Terminals()
    conversation_id = uuid4()
    member = uuid4()
    terminals.connect(conversation_id, "/p", member)
    sending = asyncio.ensure_future(
        terminals.send(conversation_id, "write", 5, arg="/p/a", body=b"content")
    )
    await asyncio.sleep(0)
    op = await terminals.next_op(conversation_id)
    assert await terminals.staged(conversation_id, op.op_id, uuid4()) is None
    assert await terminals.staged(conversation_id, op.op_id, member) == b"content"
    assert await terminals.staged(conversation_id, op.op_id) == b"content"
    assert not terminals.resolve(conversation_id, op.op_id, b"{}", member_id=uuid4())
    assert terminals.resolve(conversation_id, op.op_id, b"{}", member_id=member)
    assert await sending == b"{}"


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
    with pytest.raises(TerminalAbsent):
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
    assert handle.runtime_root == f"$UFO_HOME/runs/{conversation_id.hex}"
    assert handle.egress_env["HTTPS_PROXY"] == "https://run-token:ufo@proxy.example.com"
    assert handle.egress_env["UFO_EGRESS_CA_CERT"] == "ca-pem"
    assert handle.egress_env["UFO_CONVERSATION_ID"] == str(conversation_id)


async def test_create_without_a_public_proxy_fails_closed_on_loopback() -> None:
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    handle = await carrier.create(_spec(conversation_id, "/p"))
    assert handle.egress_env["HTTP_PROXY"] == "http://run-token:ufo@127.0.0.1:8080"


def _rendered_document(kind: str, page: int, text: str) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "kind": kind,
                    "total_pages": page,
                    "requested_range": {"start_page": page, "limit": 1},
                    "pages": [
                        {
                            "number": page,
                            "file": "page-01.png",
                            "width": 800,
                            "height": 600,
                            "text": text,
                        }
                    ],
                }
            ),
        )
        archive.writestr("page-01.png", b"\x89PNG\r\n\x1a\nrendered")
    return target.getvalue()


async def test_a_document_read_relays_bounded_bytes_to_preview() -> None:
    rendered = _rendered_document("xlsx", 2, "sheet page two")

    async def preview(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=rendered)

    terminals = Terminals()
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    carrier = TerminalCarrier(
        terminals=terminals,
        document_renderer=DocumentRenderer(
            service_url="https://preview.test",
            token="preview-real",
            transport=httpx.MockTransport(preview),
        ),
    )
    handle = await carrier.create(_spec(conversation_id, "/p"))
    running = asyncio.create_task(
        carrier.file_op(handle, "read", {"path": "/workspace/model.xlsx", "offset": 2, "limit": 1})
    )
    op = await _answer(terminals, conversation_id, b"xlsx bytes")
    assert op.kind == "read"
    assert op.arg == "/p/model.xlsx"
    assert _op_params(op) == {
        "max_bytes": DOCUMENT_INPUT_MAX_BYTES,
        "workspace": "/p",
    }
    result = await running
    assert result["type"] == "xlsx"
    assert result["text"] == "sheet page two"
    assert result["start_page"] == 2
    assert result["pages_returned"] == 1


async def test_a_skill_document_read_keeps_ufo_home_as_its_containment_root() -> None:
    async def preview(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_rendered_document("pdf", 1, "showcase"),
        )

    terminals = Terminals()
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    carrier = TerminalCarrier(
        terminals=terminals,
        document_renderer=DocumentRenderer(
            service_url="https://preview.test",
            token="preview-real",
            transport=httpx.MockTransport(preview),
        ),
    )
    handle = await carrier.create(_spec(conversation_id, "/p"))
    running = asyncio.create_task(
        carrier.file_op(
            handle,
            "read",
            {
                "path": "$UFO_HOME/skills/theme-factory/theme-showcase.pdf",
                "workspace": "$UFO_HOME/skills",
                "limit": 1,
            },
        )
    )

    op = await _answer(terminals, conversation_id, b"pdf bytes")

    assert op.kind == "read"
    assert op.arg == "$UFO_HOME/skills/theme-factory/theme-showcase.pdf"
    assert _op_params(op) == {
        "max_bytes": DOCUMENT_INPUT_MAX_BYTES,
        "workspace": "$UFO_HOME/skills",
    }
    result = await running
    assert result["type"] == "pdf"


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


async def test_exec_names_its_program_with_rewritten_argv_and_decodes_the_reply() -> None:
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/Users/member/proj", None)
    handle = await carrier.create(_spec(conversation_id, "/Users/member/proj"))
    reply = json.dumps(
        {"exit_code": 0, "stdout_b64": base64.b64encode(b"out\n").decode(), "stderr_b64": ""}
    ).encode()
    running = asyncio.ensure_future(carrier.exec(handle, ("cat", "/workspace/a.txt"), timeout_s=30))
    await asyncio.sleep(0)
    op = await _answer(terminals, conversation_id, reply)
    result = await running
    assert op.kind == "exec" and op.timeout_s == 30 and op.name == "exec"
    params = _op_params(op)
    assert params["argv"] == ["cat", "/Users/member/proj/a.txt"]
    assert params["env"]["HTTP_PROXY"].startswith("http://run-token:ufo@")
    assert result.exit_code == 0 and result.stdout == "out\n"


async def test_skills_use_the_running_clients_native_operation() -> None:
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/Users/member/proj", None)
    handle = await carrier.create(_spec(conversation_id, "/Users/member/proj"))
    session = SandboxSession(carrier=carrier, handle=handle)
    requested = {"system": {"sandbox": "sha256:aaa"}, "user": {}}

    running = asyncio.create_task(session.load_skills(requested))
    op = await _answer(
        terminals,
        conversation_id,
        b'{"roots":{"sandbox":"/Users/member/.ufo/skills/sandbox"}}',
    )

    assert op.kind == "skills"
    assert op.name == "" and op.arg == ""
    assert _op_params(op) == requested
    assert await running == {"sandbox": "/Users/member/.ufo/skills/sandbox"}


async def test_exec_keeps_a_presigned_url_whole_beside_the_path_it_uploads() -> None:
    """A member's machine is where the upload leg of hosted publishing runs: `curl -T` over a
    workspace path and a presigned URL, each its own argv element. Every blob key begins
    `workspaces/`, so the logical root's name rides inside a URL a signature covers — the path is
    named under the bound directory and the URL crosses byte for byte, or the store gets a PUT for
    a key no signature covers and answers 403."""
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/Users/member/proj", None)
    handle = await carrier.create(_spec(conversation_id, "/Users/member/proj"))
    url = "https://bucket.s3.amazonaws.com/workspaces/f795c197/sites/i.html?X-Amz-Signature=a"
    script = 'curl -sS --fail-with-body -T "$1" --url "$2"'
    reply = json.dumps({"exit_code": 0, "stdout_b64": "", "stderr_b64": ""}).encode()

    running = asyncio.ensure_future(
        carrier.exec(
            handle,
            ("sh", "-c", script, "sh", f"{WORKSPACE_DIR}/index.html", url),
            timeout_s=30,
        )
    )
    await asyncio.sleep(0)
    op = await _answer(terminals, conversation_id, reply)
    await running

    assert _op_params(op)["argv"] == [
        "sh",
        "-c",
        script,
        "sh",
        "/Users/member/proj/index.html",
        url,
    ]


async def test_exec_decodes_a_base64_reply() -> None:
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    handle = await carrier.create(_spec(conversation_id, "/p"))
    reply = json.dumps(
        {
            "exit_code": 3,
            "stdout_b64": base64.b64encode(b"out\n").decode(),
            "stderr_b64": base64.b64encode(b"err\n").decode(),
        }
    ).encode()
    running = asyncio.ensure_future(carrier.exec(handle, ("true",), timeout_s=30))
    await asyncio.sleep(0)
    await _answer(terminals, conversation_id, reply)
    result = await running
    assert result.exit_code == 3 and result.stdout == "out\n" and result.stderr == "err\n"


async def test_exec_reports_the_clients_expired_deadline_as_a_timeout() -> None:
    """The seconds the op allowed are what the `bash` tool needs to hand back the handles of a
    command still running on the member's machine; without them a timed-out call reports a bare
    exit code and the model never learns the work continues."""
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    handle = await carrier.create(_spec(conversation_id, "/p"))
    reply = json.dumps(
        {
            "exit_code": 137,
            "timed_out": True,
            "stdout_b64": base64.b64encode(b"partial\n").decode(),
            "stderr_b64": "",
        }
    ).encode()
    running = asyncio.ensure_future(carrier.exec(handle, ("sleep", "999"), timeout_s=30))
    await asyncio.sleep(0)
    await _answer(terminals, conversation_id, reply)
    result = await running
    assert result.timed_out_after_s == 30
    assert result.exit_code == EXEC_TIMEOUT_CODE
    assert result.stdout == "partial\n"


async def test_exec_reports_a_command_the_member_killed_as_its_own_exit() -> None:
    """A group signal the client did not send answers the same code, so only the reply's own field
    says whose deadline ended the command — and a client the deploy has not yet updated sends none.
    """
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    handle = await carrier.create(_spec(conversation_id, "/p"))
    for reply in (
        json.dumps(
            {"exit_code": 137, "timed_out": False, "stdout_b64": "", "stderr_b64": ""}
        ).encode(),
        json.dumps({"exit_code": 137, "stdout_b64": "", "stderr_b64": ""}).encode(),
    ):
        running = asyncio.ensure_future(carrier.exec(handle, ("sleep", "999"), timeout_s=30))
        await asyncio.sleep(0)
        await _answer(terminals, conversation_id, reply)
        result = await running
        assert result.timed_out_after_s is None
        assert result.exit_code == 137


async def test_exec_refuses_a_malformed_base64_reply() -> None:
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    handle = await carrier.create(_spec(conversation_id, "/p"))
    reply = json.dumps({"exit_code": 0, "stdout_b64": "not base64!", "stderr_b64": ""}).encode()
    running = asyncio.ensure_future(carrier.exec(handle, ("true",), timeout_s=30))
    await asyncio.sleep(0)
    await _answer(terminals, conversation_id, reply)
    with pytest.raises(ValueError):
        await running


async def test_exec_refuses_a_reply_missing_its_streams() -> None:
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    handle = await carrier.create(_spec(conversation_id, "/p"))
    reply = json.dumps({"exit_code": 0, "stdout_hex": "6869", "stderr_hex": ""}).encode()
    running = asyncio.ensure_future(carrier.exec(handle, ("true",), timeout_s=30))
    await asyncio.sleep(0)
    await _answer(terminals, conversation_id, reply)
    with pytest.raises(RuntimeError, match="carries no stdout_b64"):
        await running


async def test_write_stages_the_bytes_and_maps_a_refusal() -> None:
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    handle = await carrier.create(_spec(conversation_id, "/p"))
    writing = asyncio.ensure_future(carrier.write(handle, "/workspace/new.txt", b"content"))
    await asyncio.sleep(0)
    op = await terminals.next_op(conversation_id)
    assert await terminals.staged(conversation_id, op.op_id) == b"content"
    assert op.kind == "write" and op.arg == "/p/new.txt" and op.name == "" and op.params == ""
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


async def test_file_op_names_its_program_and_maps_the_handled_error() -> None:
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    handle = await carrier.create(_spec(conversation_id, "/p"))

    running = asyncio.ensure_future(
        carrier.file_op(
            handle, "grep", {"pattern": "x", "path": "/workspace", "workspace": "/workspace"}
        )
    )
    await asyncio.sleep(0)
    exec_ok = json.dumps({"exit_code": 0, "stdout_b64": "", "stderr_b64": ""}).encode()
    listing = await _answer(terminals, conversation_id, exec_ok)
    assert listing.kind == "exec"
    enum_command = _op_params(listing)["argv"][2]
    assert enum_command.startswith("UFO_WALK_ROOT=/p\n")
    assert "grep-enum" in enum_command
    op = await _answer(terminals, conversation_id, b'{"matches": []}')
    assert op.kind == "fileop" and op.name == "grep"
    assert _op_params(op) == {
        "pattern": "x",
        "path": "/p",
        "workspace": "/p",
        "enum": "grep-enum",
    }
    assert await running == {"matches": []}

    refused = asyncio.ensure_future(
        carrier.file_op(handle, "grep", {"pattern": "x", "workspace": WORKSPACE_DIR})
    )
    await asyncio.sleep(0)
    await _answer(terminals, conversation_id, exec_ok)
    await _answer(terminals, conversation_id, b'{"error": "invalid output_mode: y"}')
    with pytest.raises(ValueError, match="invalid output_mode"):
        await refused


async def test_a_walk_enumerates_a_bound_directory_that_contains_workspace() -> None:
    """A member launched from a directory whose path contains `/workspace` (`~/workspace/api`) must
    still walk: the enumeration's already-concrete root must not go through the `/workspace`→root
    rewrite a second time, which would mangle it into a path `find` cannot reach."""
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
    exec_ok = json.dumps({"exit_code": 0, "stdout_b64": "", "stderr_b64": ""}).encode()
    listing = await _answer(terminals, conversation_id, exec_ok)
    enum_command = _op_params(listing)["argv"][2]
    assert f"UFO_WALK_ROOT={root}\n" in enum_command
    assert "/Users/alex/Users/alex" not in enum_command
    op = await _answer(terminals, conversation_id, b'{"matches": []}')
    assert _op_params(op)["path"] == root
    assert await running == {"matches": []}


def _enumerate(op: str, root: Path, workdir: Path) -> tuple[list[str], list[str]]:
    """The walk's own shell program run over a real tree, split into the sections the client reads:
    the NUL-separated paths, then the measurement line each path is paired with by position."""
    workdir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["/bin/sh", "-c", terminal.WALK_ENUMERATION[op]],
        env={"UFO_WALK_ROOT": str(root), "UFO_OP_WORKDIR": str(workdir), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    items = (workdir / f"{op}-enum").read_bytes().split(b"\0")
    cut = items.index(b"")
    return (
        [item.decode() for item in items[:cut]],
        [line.decode() for line in b"\0".join(items[cut + 1 :]).splitlines() if line],
    )


def _ufo_fs_glob(root: Path, client: Path) -> list[str]:
    """The paths the in-sandbox walk itself returns for the whole tree, from the real `ufo fs glob`
    run the way a carrier runs it: one JSON argv, one JSON object on stdout. No `enum` param, so the
    verb builds the listing itself — which is the half this compares against."""
    completed = subprocess.run(
        [str(client), "fs", "glob", json.dumps({"pattern": "**/*", "workspace": str(root)})],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return [file["path"] for file in json.loads(completed.stdout)["files"]]


@pytest.mark.integration
def test_the_glob_walk_lists_every_file_ufo_fs_would_have_walked(
    tmp_path: Path, sandbox_client: Path
) -> None:
    """Parity with the container, which is what lets the client read the listing instead of the
    tree: `ufo fs glob` is `Path.glob`'s walk, which enters `node_modules` and `.git` — so pruning
    the names the `grep` walk prunes would hide files a glob run in the container returns."""
    root = tmp_path / "workspace"
    names = ("src/app.py", "node_modules/pkg/index.js", ".git/config", ".venv/lib/x.py")
    for index, name in enumerate(names):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(name)
        os.utime(root / name, (0, 1_700_000_000.5 + index))

    paths, measurements = _enumerate("glob", root, tmp_path / "workdir")

    assert sorted(paths) == sorted(_ufo_fs_glob(root, sandbox_client))
    assert [(Path(path).stat().st_size, Path(path).stat().st_mtime) for path in paths] == [
        (int(line.split(" ")[0]), float(line.split(" ")[1])) for line in measurements
    ]


GROWN_FILES = 2000


def test_the_glob_walk_measures_the_paths_it_listed(tmp_path: Path) -> None:
    """One walk, not two. The client pairs the sections by position, so a listing and a measurement
    taken by separate walks hand every file after a newly created one another file's size."""
    root = tmp_path / "tree"
    churn = root / "churn"
    churn.mkdir(parents=True)
    for index in range(200):
        (root / f"f{index}.txt").write_text("x" * index)
    staging = tmp_path / "staging"
    staging.mkdir()
    stop = threading.Event()

    def grow() -> None:
        index = 0
        while not stop.is_set() and index < GROWN_FILES:
            staged = staging / f"n{index}"
            staged.write_text("y" * (index % 13))
            staged.rename(churn / f"n{index}")
            index += 1

    growing = threading.Thread(target=grow)
    growing.start()
    try:
        paths, measurements = _enumerate("glob", root, tmp_path / "workdir")
    finally:
        stop.set()
        growing.join()

    assert len(paths) == len(measurements)
    assert [Path(path).stat().st_size for path in paths] == [
        int(line.split(" ")[0]) for line in measurements
    ]


def _repository(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    (repo / "mod.py").write_text("x = 1\n")
    for args in (
        ("init", "-q", "."),
        ("add", "-A"),
        ("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"),
    ):
        completed = subprocess.run(
            ["/usr/bin/git", "-C", str(repo), *args], capture_output=True, text=True, check=False
        )
        assert completed.returncode == 0, completed.stderr
    return repo


def _changes_enum(root: Path, workdir: Path, targets: list[str]) -> bytes:
    workdir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["/bin/sh", "-c", terminal.CHANGES_ENUMERATION, "sh", *targets],
        env={"UFO_WALK_ROOT": str(root), "UFO_OP_WORKDIR": str(workdir), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return (workdir / "changes-enum").read_bytes()


def test_the_changes_scan_diffs_only_repositories_that_report_changes(tmp_path: Path) -> None:
    """A clean checkout answers its marker with empty sections off one status probe — no diff run —
    and a changed one still carries its porcelain entries and its patch."""
    root = tmp_path / "workspace"
    clean = _repository(root, "clean")
    dirty = _repository(root, "dirty")
    (dirty / "mod.py").write_text("x = 2\n")

    enum = _changes_enum(root, tmp_path / "workdir", ["clean", "dirty"])

    assert b"R\x00" + str(clean).encode() + b"\x00D\x00\x00" in enum
    assert b"R\x00" + str(dirty).encode() + b"\x00 M mod.py\x00" in enum
    assert b"-x = 1\n+x = 2" in enum


def test_a_target_resolves_to_the_outermost_checkout_once(tmp_path: Path) -> None:
    """The `changes` op's `_repositories` rule, ascending instead of walking: a target inside a
    vendored clone answers as the checkout above it, targets sharing one checkout enumerate it
    once, and a target whose directory is gone still resolves through the ancestors that remain."""
    root = _repository(tmp_path, "workspace")
    _repository(root, "vendored")

    enum = _changes_enum(root, tmp_path / "workdir", ["vendored", ".", "gone/away"])

    assert enum.count(b"R\x00") == 1
    assert enum.startswith(b"R\x00" + str(root).encode() + b"\x00")


def test_a_target_outside_any_checkout_enumerates_nothing(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    (root / "notes").mkdir(parents=True)

    enum = _changes_enum(root, tmp_path / "workdir", ["notes"])

    assert enum == b""


async def test_changes_without_targets_sends_no_op() -> None:
    """The empty scan is answered by the carrier itself: nothing travels, so a turn that wrote
    nothing costs the member's machine nothing."""
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    handle = await carrier.create(_spec(conversation_id, "/p"))

    result = await carrier.file_op(handle, "changes", {"workspace": WORKSPACE_DIR, "paths": []})

    assert result == {"changes": [], "truncated": False}
    assert terminals.in_flight(conversation_id) is None

    with pytest.raises(ValueError, match="target"):
        await carrier.file_op(handle, "changes", {"workspace": WORKSPACE_DIR})


async def test_changes_targets_ride_the_enumeration_argv() -> None:
    terminals = Terminals()
    carrier = TerminalCarrier(terminals=terminals)
    conversation_id = uuid4()
    terminals.connect(conversation_id, "/p", None)
    handle = await carrier.create(_spec(conversation_id, "/p"))

    running = asyncio.ensure_future(
        carrier.file_op(handle, "changes", {"workspace": WORKSPACE_DIR, "paths": ["src", "."]})
    )
    await asyncio.sleep(0)
    exec_ok = json.dumps({"exit_code": 0, "stdout_b64": "", "stderr_b64": ""}).encode()
    listing = await _answer(terminals, conversation_id, exec_ok)
    argv = _op_params(listing)["argv"]
    assert argv[2].startswith("UFO_WALK_ROOT=/p\n")
    assert "changes-enum" in argv[2]
    assert argv[3:] == ["sh", "src", "."]
    op = await _answer(terminals, conversation_id, b'{"changes": [], "truncated": false}')
    assert op.kind == "fileop" and op.name == "changes"
    assert _op_params(op) == {"workspace": "/p", "enum": "changes-enum"}
    assert await running == {"changes": [], "truncated": False}


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
        outcome.append(asyncio.run(terminals.send(conversation_id, "exec", 10)))

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
