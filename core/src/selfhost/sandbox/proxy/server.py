"""The egress proxy: the container's only route out, and the seam where the real key hits the wire.

The sandbox reaches the network solely through this proxy (its HTTP(S)_PROXY). Default-deny is a
CONNECT the proxy refuses: a host no ScopeRule admits gets a 403 and never leaves the machine. An
admitted host carrying an InjectionRule is MITM'd — the proxy terminates TLS with a leaf minted from
the per-process CA (in the container's trust store), swaps the sentinel Authorization value the
sandbox sees for the real credential, and re-originates upstream over its own verified TLS, so the
raw key is never inside the sandbox. An admitted host with no InjectionRule is tunnelled opaquely.
Each forwarded request is metered under its MeterRule (the durable ledger write is U7)."""

import asyncio
import ssl
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from selfhost.o11y import emit_metric
from selfhost.sandbox.proxy.rules import InjectionRule, MeterRule, Rule, ScopeRule
from selfhost.sandbox.session import ProxyEndpoint

PROXY_BIND_HOST = "0.0.0.0"
RELAY_CHUNK_BYTES = 65536
MAX_HEADER_BYTES = 65536
CONNECT_UPSTREAM_TIMEOUT_SECONDS = 30
DEFAULT_HTTPS_PORT = 443
CERT_VALID_DAYS = "1"


async def generate_ca() -> tuple[str, str]:
    """A self-signed CA (cert, key) minted via the openssl CLI — no crypto library dependency."""
    with tempfile.TemporaryDirectory() as work:
        key_path = Path(work) / "ca.key"
        cert_path = Path(work) / "ca.crt"
        await _openssl(
            "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key_path), "-out", str(cert_path),
            "-days", CERT_VALID_DAYS, "-subj", "/CN=selfhost-sandbox-proxy",
        )
        return cert_path.read_text(), key_path.read_text()


async def _openssl(*argv: str) -> None:
    process = await asyncio.create_subprocess_exec(
        "openssl", *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"openssl {argv[0]} failed: {stderr.decode().strip()}")


@dataclass
class EgressProxy:
    rules: tuple[Rule, ...]
    ca_cert: str
    ca_key: str
    _server: asyncio.Server | None = field(default=None, init=False)
    _workdir: tempfile.TemporaryDirectory | None = field(default=None, init=False)
    _contexts: dict[str, ssl.SSLContext] = field(default_factory=dict, init=False)
    _mint_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    async def start(self, bind_host: str = PROXY_BIND_HOST) -> ProxyEndpoint:
        self._workdir = tempfile.TemporaryDirectory()
        root = Path(self._workdir.name)
        (root / "ca.crt").write_text(self.ca_cert)
        (root / "ca.key").write_text(self.ca_key)
        await _openssl("genrsa", "-out", str(root / "leaf.key"), "2048")
        self._server = await asyncio.start_server(self._handle, bind_host, 0)
        port = self._server.sockets[0].getsockname()[1]
        return ProxyEndpoint(port=port, ca_cert=self.ca_cert)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._workdir is not None:
            self._workdir.cleanup()
            self._workdir = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            method, _, rest = request_line.decode(errors="replace").partition(" ")
            if method != "CONNECT":
                await _respond(writer, 405, "only CONNECT is proxied")
                return
            host, _, port_text = rest.split(" ", 1)[0].partition(":")
            while (await reader.readline()) not in (b"\r\n", b""):
                continue
            if not any(isinstance(r, ScopeRule) and host in r.allowed_hosts for r in self.rules):
                await _respond(writer, 403, f"egress to {host} is not permitted")
                return
            port = int(port_text or DEFAULT_HTTPS_PORT)
            injection = next(
                (r for r in self.rules if isinstance(r, InjectionRule) and r.host == host), None
            )
            if injection is None:
                await self._tunnel(reader, writer, host, port)
            else:
                await self._mitm(reader, writer, host, port, injection)
        finally:
            writer.close()

    async def _tunnel(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int
    ) -> None:
        """An admitted host with no key to inject: relay bytes opaquely, never terminating TLS."""
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=CONNECT_UPSTREAM_TIMEOUT_SECONDS
            )
        except (OSError, TimeoutError):
            await _respond(writer, 502, f"cannot reach {host}")
            return
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await _relay(reader, writer, upstream_reader, upstream_writer)

    async def _mitm(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        host: str,
        port: int,
        injection: InjectionRule,
    ) -> None:
        """Terminate the sandbox's TLS with a minted leaf, swap the sentinel for the real key, and
        re-originate the request upstream over verified TLS — the response streams straight back."""
        leaf_context = await self._leaf_context(host)
        client_reader, client_writer = await _start_tls_server(reader, writer, leaf_context)
        request = await _read_request_head(client_reader)
        if request is None:
            return
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, ssl=True, server_hostname=host),
                timeout=CONNECT_UPSTREAM_TIMEOUT_SECONDS,
            )
        except (OSError, TimeoutError, ssl.SSLError):
            return
        request_line, headers = request
        upstream_writer.write(request_line)
        upstream_writer.write(_inject(headers, injection))
        upstream_writer.write(b"\r\n")
        await upstream_writer.drain()
        self._meter(host)
        await _relay(client_reader, client_writer, upstream_reader, upstream_writer)

    async def _leaf_context(self, host: str) -> ssl.SSLContext:
        cached = self._contexts.get(host)
        if cached is not None:
            return cached
        async with self._mint_lock:
            cached = self._contexts.get(host)
            if cached is not None:
                return cached
            root = Path(self._workdir.name)
            cert_path = root / f"leaf-{host}.crt"
            ext_path = root / f"leaf-{host}.ext"
            ext_path.write_text(f"subjectAltName=DNS:{host}\nextendedKeyUsage=serverAuth\n")
            await _openssl(
                "req", "-new", "-key", str(root / "leaf.key"),
                "-subj", f"/CN={host}", "-out", str(root / f"leaf-{host}.csr"),
            )
            await _openssl(
                "x509", "-req", "-in", str(root / f"leaf-{host}.csr"),
                "-CA", str(root / "ca.crt"), "-CAkey", str(root / "ca.key"), "-CAcreateserial",
                "-days", CERT_VALID_DAYS, "-extfile", str(ext_path), "-out", str(cert_path),
            )
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certfile=str(cert_path), keyfile=str(root / "leaf.key"))
            context.set_alpn_protocols(["http/1.1"])
            self._contexts[host] = context
            return context

    def _meter(self, host: str) -> None:
        for rule in self.rules:
            if isinstance(rule, MeterRule) and rule.host == host:
                emit_metric("sandbox_egress_total", host=host, dimension=rule.dimension)


async def _start_tls_server(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, context: ssl.SSLContext
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Send the CONNECT 200 and upgrade the connection to TLS as the server side, keeping the same
    StreamReader/StreamWriter so the caller reads and writes decrypted bytes. Reading is paused
    before the 200 so the client's TLS ClientHello (which follows immediately) stays in the socket
    buffer for the SSL layer instead of being consumed into the plaintext reader."""
    loop = asyncio.get_running_loop()
    transport = writer.transport
    transport.pause_reading()
    writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    await writer.drain()
    protocol = transport.get_protocol()
    tls_transport = await loop.start_tls(transport, protocol, context, server_side=True)
    protocol._transport = tls_transport
    writer._transport = tls_transport
    return reader, writer


async def _read_request_head(
    reader: asyncio.StreamReader,
) -> tuple[bytes, list[bytes]] | None:
    """The request line and header lines (each still terminated), bounded so a hostile client cannot
    exhaust memory before the body is even streamed."""
    request_line = await reader.readline()
    if not request_line:
        return None
    headers: list[bytes] = []
    total = len(request_line)
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b""):
            break
        total += len(line)
        if total > MAX_HEADER_BYTES:
            return None
        headers.append(line)
    return request_line, headers


def _inject(headers: list[bytes], injection: InjectionRule) -> bytes:
    """Rewrite the header block: swap the sentinel value on the injected header for the real one,
    and force `Connection: close` so each request is a fresh MITM that re-applies the swap."""
    sentinel = injection.sentinel.encode()
    real = injection.real.encode()
    name = injection.header.encode()
    rebuilt = bytearray()
    for line in headers:
        field_name, _, value = line.partition(b":")
        if field_name.strip().lower() in (b"connection", b"proxy-connection"):
            continue
        if field_name.strip().lower() == name.lower() and value.strip() == sentinel:
            rebuilt += name + b": " + real + b"\r\n"
        else:
            rebuilt += line
    rebuilt += b"connection: close\r\n"
    return bytes(rebuilt)


async def _relay(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_reader: asyncio.StreamReader,
    upstream_writer: asyncio.StreamWriter,
) -> None:
    """Pump both directions until either closes; the response (upstream→client) reaching EOF ends
    the exchange, so a streamed response relays chunk by chunk and stops when the upstream shuts."""
    down = asyncio.create_task(_pump(upstream_reader, client_writer))
    up = asyncio.create_task(_pump(client_reader, upstream_writer))
    await asyncio.wait({down, up}, return_when=asyncio.FIRST_COMPLETED)
    for task in (down, up):
        task.cancel()
    upstream_writer.close()


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(RELAY_CHUNK_BYTES):
            writer.write(chunk)
            await writer.drain()
    except (OSError, asyncio.CancelledError):
        pass


async def _respond(writer: asyncio.StreamWriter, status: int, message: str) -> None:
    writer.write(f"HTTP/1.1 {status} {message}\r\n\r\n".encode())
    await writer.drain()
