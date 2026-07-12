from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest
from ufo_ext_e2b import CA_INSTALL_TIMEOUT_SECONDS, CA_STAGING_PATH, INSTALL_CA_COMMAND

from sandbox import proxy_gate


@dataclass(frozen=True)
class _Result:
    stdout: str
    stderr: str = ""


@dataclass
class _Commands:
    statuses: list[str] = field(default_factory=lambda: ["000", "403"])
    calls: list[tuple[str, str | None, float | None]] = field(default_factory=list)

    def run(
        self, command: str, *, user: str | None = None, timeout: float | None = None
    ) -> _Result:
        self.calls.append((command, user, timeout))
        if command == INSTALL_CA_COMMAND:
            return _Result("")
        return _Result(self.statuses.pop(0), "connection pending")


@dataclass
class _Files:
    writes: list[tuple[str, str, str | None]] = field(default_factory=list)

    def write(self, path: str, data: str, *, user: str | None = None) -> None:
        self.writes.append((path, data, user))


@dataclass
class _Sandbox:
    commands: _Commands = field(default_factory=_Commands)
    files: _Files = field(default_factory=_Files)
    killed: bool = False

    def kill(self) -> None:
        self.killed = True


def test_gate_installs_system_trust_then_probes_real_curl_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = _Sandbox()
    monkeypatch.setattr(
        proxy_gate,
        "Sandbox",
        SimpleNamespace(create=lambda **kwargs: sandbox),
    )
    monkeypatch.setattr(proxy_gate.time, "sleep", lambda _: None)

    proxy_gate.ProxyTlsGate("https://sandbox-proxy.test", "ca-pem").run()

    assert sandbox.files.writes == [(CA_STAGING_PATH, "ca-pem", "root")]
    assert sandbox.commands.calls[0] == (
        INSTALL_CA_COMMAND,
        "root",
        CA_INSTALL_TIMEOUT_SECONDS,
    )
    probes = [command for command, _, _ in sandbox.commands.calls[1:]]
    assert len(probes) == 2
    assert all("--proxy-cacert" not in command for command in probes)
    assert all("https://invalid-run-token:@sandbox-proxy.test" in command for command in probes)
    assert all("%{http_connect}" in command for command in probes)
    assert sandbox.killed


def test_gate_rejects_a_plaintext_proxy_before_booting_a_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        proxy_gate,
        "Sandbox",
        SimpleNamespace(create=lambda **kwargs: pytest.fail("sandbox booted")),
    )

    with pytest.raises(RuntimeError, match="HTTPS"):
        proxy_gate.ProxyTlsGate("http://sandbox-proxy.test:8888", "ca-pem").run()


def test_deploy_runs_the_live_proxy_gate_after_apply() -> None:
    workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "deploy.yml").read_text()
    assert "Gate sandbox egress proxy TLS" in workflow
    assert "sandbox/proxy_gate.py" in workflow
    assert "sandbox_proxy_ca_cert" in workflow
