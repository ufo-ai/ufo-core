"""The egress proxy: the container's only route out.

The sandbox network is internal — no gateway — so a container reaches the outside solely through
this proxy, and the proxy refuses any CONNECT whose host no ScopeRule admits. That is the wire
enforcement of default-deny: a `curl` to an ungranted host never leaves the host machine. The CA
is generated per process so the carrier can install it in the container's trust store; TLS
termination for sentinel injection (InjectionRule) rides on top of this seam.
"""

import asyncio
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from selfhost.sandbox.proxy.rules import Rule, ScopeRule
from selfhost.sandbox.session import ProxyEndpoint

PROXY_HOST = "127.0.0.1"
TUNNEL_CHUNK_BYTES = 65536
CONNECT_UPSTREAM_TIMEOUT_SECONDS = 30


async def generate_ca() -> tuple[str, str]:
    """A self-signed CA (cert, key) minted via the openssl CLI — no crypto library dependency."""
    with tempfile.TemporaryDirectory() as work:
        key_path = Path(work) / "ca.key"
        cert_path = Path(work) / "ca.crt"
        process = await asyncio.create_subprocess_exec(
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key_path),
            "-out",
            str(cert_path),
            "-days",
            "1",
            "-subj",
            "/CN=selfhost-sandbox-proxy",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"CA generation failed: {stderr.decode().strip()}")
        return cert_path.read_text(), key_path.read_text()


@dataclass
class EgressProxy:
    rules: tuple[Rule, ...]
    ca_cert: str
    ca_key: str
    _server: asyncio.Server | None = field(default=None, init=False)

    async def start(self, host: str = PROXY_HOST) -> ProxyEndpoint:
        self._server = await asyncio.start_server(self._handle, host, 0)
        port = self._server.sockets[0].getsockname()[1]
        return ProxyEndpoint(url=f"http://{host}:{port}", ca_cert=self.ca_cert)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            method, _, rest = request_line.decode(errors="replace").partition(" ")
            if method != "CONNECT":
                await _respond(writer, 405, "only CONNECT is proxied")
                return
            authority = rest.split(" ", 1)[0]
            host, _, port_text = authority.partition(":")
            while (await reader.readline()) not in (b"\r\n", b""):
                continue
            allowed = any(
                isinstance(rule, ScopeRule) and host in rule.allowed_hosts for rule in self.rules
            )
            if not allowed:
                await _respond(writer, 403, f"egress to {host} is not permitted")
                return
            await self._tunnel(reader, writer, host, int(port_text or "443"))
        finally:
            writer.close()

    async def _tunnel(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int
    ) -> None:
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=CONNECT_UPSTREAM_TIMEOUT_SECONDS
            )
        except (OSError, TimeoutError):
            await _respond(writer, 502, f"cannot reach {host}")
            return
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(
            _pipe(reader, upstream_writer),
            _pipe(upstream_reader, writer),
            return_exceptions=True,
        )
        upstream_writer.close()


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while chunk := await reader.read(TUNNEL_CHUNK_BYTES):
        writer.write(chunk)
        await writer.drain()


async def _respond(writer: asyncio.StreamWriter, status: int, message: str) -> None:
    writer.write(f"HTTP/1.1 {status} {message}\r\n\r\n".encode())
    await writer.drain()
