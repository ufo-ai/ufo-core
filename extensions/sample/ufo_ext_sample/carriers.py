from collections.abc import AsyncIterator

from ufo.sdk.sandbox import DialTarget, ExecResult, SandboxHandle, SandboxSpec, ufo_fs_file_op

CARRIER_NAME = "sample_carrier"
CARRIER_CONTAINER = "sample-container"


class SampleCarrier:
    """A trivial in-process carrier the probe registers so `serve`'s backend selection has a
    manifest-contributed carrier to choose. It implements the whole Carrier protocol without a real
    container: `exec` echoes the argv it received (so a selection test can prove the carrier it got
    is this one), `write` keeps the bytes it was handed under their path (so a test reads back what
    core copied in), `read` streams those bytes back, and `create` is inert. It proves the
    `carriers` seam — that core selects an extension's carrier — never a real sandbox; the Docker
    and e2b carriers keep that proof."""

    def __init__(self) -> None:
        self.written: dict[str, bytes] = {}

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=CARRIER_CONTAINER,
            run_token=spec.run_token,
            runtime_root=f"/home/user/.ufo/runs/{spec.conversation_id.hex}",
        )

    async def attach(self, spec: SandboxSpec) -> SandboxHandle | None:
        if spec.resume_id is None:
            return None
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=spec.resume_id,
            run_token=spec.run_token,
            runtime_root=f"/home/user/.ufo/runs/{spec.conversation_id.hex}",
        )

    async def exec(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
    ) -> ExecResult:
        return ExecResult(stdout=" ".join(argv), stderr="", exit_code=0)

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        self.written[path] = content

    async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]:
        if path not in self.written:
            raise FileNotFoundError(path)
        yield self.written[path]

    async def file_op(
        self, handle: SandboxHandle, op: str, params: dict[str, object]
    ) -> dict[str, object]:
        return await ufo_fs_file_op(self, handle, op, params)

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        return DialTarget(host=f"{CARRIER_CONTAINER}:{port}", tls=False)
