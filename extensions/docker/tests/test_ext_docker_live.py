import asyncio
import json
import ssl
import time
from pathlib import Path
from uuid import uuid4

import pytest
from ufo_ext_docker import IDLE_RECLAIM_SECONDS, WARM_NETWORK_LIMIT, DockerCarrier, _docker

from ufo.sdk.sandbox import ProxyEndpoint, SandboxHandle, SandboxSpec

pytestmark = pytest.mark.docker

CONVERSATION_COUNT = 36
COMMAND_TIMEOUT_SECONDS = 30
ACTIVE_COMMAND_TIMEOUT_SECONDS = 600


@pytest.fixture
def capacity_specs(tmp_path: Path, sandbox_image: str) -> tuple[SandboxSpec, ...]:
    cert = ssl.DER_cert_to_PEM_cert(ssl.create_default_context().get_ca_certs(binary_form=True)[0])
    specs = []
    for _ in range(CONVERSATION_COUNT):
        conversation = uuid4()
        workspace = tmp_path / str(conversation)
        workspace.mkdir()
        specs.append(
            SandboxSpec(
                conversation_id=conversation,
                image_ref=sandbox_image,
                workspace_host_path=str(workspace),
                proxy=ProxyEndpoint(port=8080, ca_cert=cert),
                run_token="capacity-test",
            )
        )
    return tuple(specs)


async def _checked_docker(*argv: str) -> str:
    code, stdout, stderr = await _docker(*argv)
    assert code == 0, stderr.decode()
    return stdout.decode().strip()


async def test_network_capacity_preserves_workspace_and_active_exec(
    capacity_specs: tuple[SandboxSpec, ...],
) -> None:
    carrier = DockerCarrier(network=f"ufo-capacity-{uuid4().hex}")
    peer = DockerCarrier(network=carrier.network)
    active: SandboxHandle | None = None
    blocked: asyncio.Task | None = None
    started = time.monotonic()
    try:
        foreign = await peer.create(capacity_specs[0])
        active = await carrier.create(capacity_specs[1])
        quiet = await carrier.create(capacity_specs[2])
        await carrier.write(quiet, "/workspace/saved", b"workspace-survives")
        blocked = asyncio.create_task(
            carrier.exec(
                active,
                ("sh", "-c", "mkfifo gate; touch ready; cat gate; printf protected"),
                ACTIVE_COMMAND_TIMEOUT_SECONDS,
            )
        )
        async with asyncio.timeout(COMMAND_TIMEOUT_SECONDS):
            while True:
                code, _, _ = await _docker(
                    "exec", active.container_id, "test", "-f", "/workspace/ready"
                )
                if code == 0:
                    break
        for spec in capacity_specs[3:]:
            handle = await carrier.create(spec)
            result = await carrier.exec(handle, ("printf", "created"), COMMAND_TIMEOUT_SECONDS)
            assert result.exit_code == 0
            assert result.stdout == "created"
            networks = await _checked_docker(
                "network", "ls", "--filter", f"name=^{carrier.network}-", "--format", "{{.Name}}"
            )
            assert len(networks.splitlines()) <= WARM_NETWORK_LIMIT + 1
            attached = json.loads(await _checked_docker("inspect", handle.container_id))[0]
            assert tuple(attached["NetworkSettings"]["Networks"]) == (
                f"{carrier.network}-{spec.conversation_id.hex}",
            )
        assert time.monotonic() - started < IDLE_RECLAIM_SECONDS
        assert not blocked.done()
        assert (
            await _checked_docker("inspect", "-f", "{{.State.Running}}", active.container_id)
            == "true"
        )
        assert (
            await _checked_docker("inspect", "-f", "{{.State.Running}}", quiet.container_id)
            == "false"
        )
        assert (
            await _checked_docker("inspect", "-f", "{{.State.Running}}", foreign.container_id)
            == "true"
        )
        foreign_result = await peer.exec(foreign, ("printf", "foreign"), COMMAND_TIMEOUT_SECONDS)
        assert foreign_result.exit_code == 0
        assert foreign_result.stdout == "foreign"
        recovered = await carrier.exec(quiet, ("cat", "saved"), COMMAND_TIMEOUT_SECONDS)
        assert recovered.exit_code == 0
        assert recovered.stdout == "workspace-survives"
        released = await carrier.exec(
            active, ("sh", "-c", "printf release > gate"), COMMAND_TIMEOUT_SECONDS
        )
        assert released.exit_code == 0
        completed = await blocked
        assert completed.exit_code == 0
        assert completed.stdout == "releaseprotected"
        resumed = await asyncio.gather(*(carrier.create(spec) for spec in capacity_specs[2:6]))
        results = await asyncio.gather(
            *(
                carrier.exec(handle, ("printf", "resumed"), COMMAND_TIMEOUT_SECONDS)
                for handle in resumed
            )
        )
        assert all(result.exit_code == 0 and result.stdout == "resumed" for result in results)
        networks = await _checked_docker(
            "network", "ls", "--filter", f"name=^{carrier.network}-", "--format", "{{.Name}}"
        )
        assert len(networks.splitlines()) <= WARM_NETWORK_LIMIT + 1
    finally:
        for spec in capacity_specs:
            await _docker("rm", "-f", f"ufo-sbx-{spec.conversation_id}")
            await _docker("network", "rm", f"{carrier.network}-{spec.conversation_id.hex}")
        if blocked is not None:
            await blocked
