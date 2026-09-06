"""Attachment delivery against a real store: the sandbox curls a presigned URL into the workspace
itself, run as an off-turn probe so a signed token authorizes the egress. This drives the mechanism
`SurfaceContext.deliver_attachment` uses — `ConversationProbes.run` under workspace authority — so
no attachment's bytes cross serve. Runs on the local carrier against minio; skips without Docker.

The proxy's authorization of a probe token is proven in the egress suite; the local carrier reaches
the loopback store directly, so this pins the curl-into-workspace half end to end."""

import shlex
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

import sqlalchemy as sa

from ufo.blob import S3BlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.exec_env import ProbeEnv
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ProbeTokenCodec, ProxyEndpoint, shell_path, workspace_path
from ufo.runtime.authority import WORKSPACE_AUTHORITY
from ufo.runtime.ext.context import ConversationProbes
from ufo.runtime.workspace import ws
from ufo.schema import tables

FETCH_TTL_SECONDS = 300


def _sandboxes(root: Path) -> ConversationSandbox:
    return ConversationSandbox(
        carrier=LocalCarrier(),
        backend="local",
        off_cluster=False,
        image_ref=SANDBOX_IMAGE_REF,
        proxy=ProxyEndpoint(port=1, ca_cert="test-ca"),
        workspace_root=root,
    )


def _probes(sandboxes: ConversationSandbox) -> ConversationProbes:
    return ConversationProbes(
        sandboxes,
        ProbeTokenCodec(secret=b"attachment-fetch-test-secret"),
        ProbeEnv(grants=None, clis={}, credentials=None, slots=()).exports,
    )


async def _seed_conversation(workspace_id: UUID, agent_id: UUID, conversation_id: UUID) -> None:
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
                prompt="p",
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
                queue_key="fetch",
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


async def test_a_probe_fetches_a_presigned_attachment_into_the_workspace(
    db: None, s3_store: S3BlobStore, tmp_path: Path
) -> None:
    workspace_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4()
    with ws(workspace_id):
        await _seed_conversation(workspace_id, agent_id, conversation_id)
    blob = WorkspaceBlobStore(backend=s3_store)
    key = f"artifacts/{uuid4()}/report.pdf"
    body = b"%PDF-1.7 the stored attachment"
    sandboxes = _sandboxes(tmp_path)
    with ws(workspace_id):
        await blob.put(key, body)
        url = await blob.presigned_get(key, FETCH_TTL_SECONDS)
        target = workspace_path("web-inbox/report.pdf")
        parent = shell_path(str(PurePosixPath(target).parent))
        command = (
            f"mkdir -p {parent} && curl -sS --fail-with-body "
            f"-o {shell_path(target)} --url {shlex.quote(url)}"
        )
        result = await _probes(sandboxes).run(
            conversation_id, command, timeout_s=60, authority=WORKSPACE_AUTHORITY
        )
        assert result.exit_code == 0, result.stderr
        landed = await sandboxes.read(conversation_id, "web-inbox/report.pdf")
        assert landed is not None
        fetched = b"".join([chunk async for chunk in landed])
    assert fetched == body
