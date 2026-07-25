import http.client
import http.server
import importlib.util
import os
import signal
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType
from typing import ClassVar

import pytest

SBXCRED_PATH = Path(__file__).parents[1] / "src/ufo/sandbox/image/sbxcred"


def _load_sbxcred() -> ModuleType:
    loader = SourceFileLoader("ufo_test_sbxcred", str(SBXCRED_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("cannot load sbxcred")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class _Upstream(http.server.BaseHTTPRequestHandler):
    status = 200
    body = b""
    paths: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        self.paths.append(self.path)
        self.send_response(self.status)
        self.send_header("content-length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def _running_server(
    handler: type[http.server.BaseHTTPRequestHandler],
) -> Iterator[http.server.ThreadingHTTPServer]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize(
    ("upstream_status", "upstream_body", "expected_status", "expected_body"),
    [
        (200, b'{"AccessKeyId":"AKIA"}', 200, b'{"AccessKeyId":"AKIA"}'),
        (404, b"missing", 404, None),
        (200, b"x" * 65537, 502, None),
    ],
)
def test_credential_relay_forwards_and_bounds_upstream_responses(
    tmp_path: Path,
    upstream_status: int,
    upstream_body: bytes,
    expected_status: int,
    expected_body: bytes | None,
) -> None:
    sbxcred = _load_sbxcred()
    _Upstream.status = upstream_status
    _Upstream.body = upstream_body
    _Upstream.paths = []
    token_path = tmp_path / "token"
    token_path.write_text("signed")
    relay_secret_path = tmp_path / "relay-secret"
    relay_secret_path.write_text("private")
    with _running_server(_Upstream) as upstream:
        sbxcred.CredentialRelay.upstream = (
            f"http://127.0.0.1:{upstream.server_address[1]}/sandbox-fs-credentials"
        )
        sbxcred.CredentialRelay.token_path = token_path
        sbxcred.CredentialRelay.relay_secret_path = relay_secret_path
        with _running_server(sbxcred.CredentialRelay) as relay:
            connection = http.client.HTTPConnection("127.0.0.1", relay.server_address[1])
            connection.request("GET", "/private")
            response = connection.getresponse()
            body = response.read()
            connection.close()

    assert response.status == expected_status
    assert _Upstream.paths == ["/sandbox-fs-credentials/signed"]
    if expected_body is not None:
        assert body == expected_body


def test_sbxcred_parent_returns_after_the_daemon_is_serving(tmp_path: Path) -> None:
    _Upstream.status = 200
    _Upstream.body = b'{"AccessKeyId":"AKIA"}'
    _Upstream.paths = []
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        relay_port = reservation.getsockname()[1]
    pid_path = tmp_path / "sbxcred.pid"
    token_path = tmp_path / "token"
    token_path.write_text("signed")
    relay_secret_path = tmp_path / "relay-secret"
    relay_secret_path.write_text("private")
    with _running_server(_Upstream) as upstream:
        result = subprocess.run(
            [
                sys.executable,
                str(SBXCRED_PATH),
                f"http://127.0.0.1:{upstream.server_address[1]}/sandbox-fs-credentials",
                str(token_path),
                str(relay_port),
                str(relay_secret_path),
                str(pid_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        daemon_pid = int(pid_path.read_text())
        try:
            connection = http.client.HTTPConnection("127.0.0.1", relay_port)
            connection.request("GET", "/private")
            response = connection.getresponse()
            body = response.read()
            connection.close()
        finally:
            os.kill(daemon_pid, signal.SIGTERM)

    assert result.returncode == 0
    assert response.status == 200
    assert body == b'{"AccessKeyId":"AKIA"}'
    assert _Upstream.paths == ["/sandbox-fs-credentials/signed"]


def test_sbxcred_parent_fails_when_the_daemon_cannot_signal_readiness(tmp_path: Path) -> None:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        relay_port = reservation.getsockname()[1]
    result = subprocess.run(
        [
            sys.executable,
            str(SBXCRED_PATH),
            "http://127.0.0.1:1/sandbox-fs-credentials",
            str(tmp_path / "token"),
            str(relay_port),
            str(tmp_path / "relay-secret"),
            str(tmp_path / "missing" / "sbxcred.pid"),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False,
    )

    assert result.returncode != 0
    assert b"credential relay failed to start" in result.stderr


def test_credential_relay_reads_a_rotated_token_without_restarting(tmp_path: Path) -> None:
    sbxcred = _load_sbxcred()
    _Upstream.status = 200
    _Upstream.body = b'{"AccessKeyId":"AKIA"}'
    _Upstream.paths = []
    token_path = tmp_path / "token"
    token_path.write_text("first")
    relay_secret_path = tmp_path / "relay-secret"
    relay_secret_path.write_text("private")
    with _running_server(_Upstream) as upstream:
        sbxcred.CredentialRelay.upstream = (
            f"http://127.0.0.1:{upstream.server_address[1]}/sandbox-fs-credentials"
        )
        sbxcred.CredentialRelay.token_path = token_path
        sbxcred.CredentialRelay.relay_secret_path = relay_secret_path
        with _running_server(sbxcred.CredentialRelay) as relay:
            connection = http.client.HTTPConnection("127.0.0.1", relay.server_address[1])
            connection.request("GET", "/private")
            connection.getresponse().read()
            staging = tmp_path / "token.next"
            staging.write_text("sec")
            connection.request("GET", "/private")
            connection.getresponse().read()
            staging.write_text("second")
            staging.replace(token_path)
            connection.request("GET", "/private")
            connection.getresponse().read()
            connection.close()

    assert _Upstream.paths == [
        "/sandbox-fs-credentials/first",
        "/sandbox-fs-credentials/first",
        "/sandbox-fs-credentials/second",
    ]


def test_credential_relay_rejects_other_local_paths(tmp_path: Path) -> None:
    sbxcred = _load_sbxcred()
    _Upstream.paths = []
    token_path = tmp_path / "token"
    token_path.write_text("signed")
    relay_secret_path = tmp_path / "relay-secret"
    relay_secret_path.write_text("private")
    with _running_server(_Upstream) as upstream:
        sbxcred.CredentialRelay.upstream = (
            f"http://127.0.0.1:{upstream.server_address[1]}/sandbox-fs-credentials"
        )
        sbxcred.CredentialRelay.token_path = token_path
        sbxcred.CredentialRelay.relay_secret_path = relay_secret_path
        with _running_server(sbxcred.CredentialRelay) as relay:
            connection = http.client.HTTPConnection("127.0.0.1", relay.server_address[1])
            connection.request("GET", "/signed")
            response = connection.getresponse()
            response.read()
            connection.close()

    assert response.status == 404
    assert _Upstream.paths == []


def test_credential_relay_distinguishes_an_unreadable_token_from_upstream_failure(
    tmp_path: Path,
) -> None:
    sbxcred = _load_sbxcred()
    sbxcred.CredentialRelay.token_path = tmp_path / "missing"
    relay_secret_path = tmp_path / "relay-secret"
    relay_secret_path.write_text("private")
    sbxcred.CredentialRelay.relay_secret_path = relay_secret_path
    with _running_server(sbxcred.CredentialRelay) as relay:
        connection = http.client.HTTPConnection("127.0.0.1", relay.server_address[1])
        connection.request("GET", "/private")
        response = connection.getresponse()
        response.read()
        connection.close()

    assert response.status == 500


def test_credential_relay_rejects_an_empty_local_secret(tmp_path: Path) -> None:
    sbxcred = _load_sbxcred()
    relay_secret_path = tmp_path / "relay-secret"
    relay_secret_path.write_text("")
    sbxcred.CredentialRelay.relay_secret_path = relay_secret_path
    with _running_server(sbxcred.CredentialRelay) as relay:
        connection = http.client.HTTPConnection("127.0.0.1", relay.server_address[1])
        connection.request("GET", "/")
        response = connection.getresponse()
        response.read()
        connection.close()

    assert response.status == 500
