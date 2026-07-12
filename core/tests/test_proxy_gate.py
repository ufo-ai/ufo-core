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
    results: list[_Result] = field(
        default_factory=lambda: [
            _Result("000\n5", "Could not resolve proxy"),
            _Result("000\n7", "Could not connect to proxy"),
            _Result("000\n28", "Proxy connection timed out"),
            _Result("000\n56", "Proxy connection reset"),
            _Result("403\n56"),
        ]
    )
    calls: list[tuple[str, str | None, float | None]] = field(default_factory=list)

    def run(
        self, command: str, *, user: str | None = None, timeout: float | None = None
    ) -> _Result:
        self.calls.append((command, user, timeout))
        if command == INSTALL_CA_COMMAND:
            return _Result("")
        return self.results.pop(0)


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


@dataclass
class _Clock:
    now: float = 0
    sleeps: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.now

    def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


def test_gate_installs_system_trust_then_waits_for_proxy_and_probes_tls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = _Sandbox()
    clock = _Clock()
    monkeypatch.setattr(
        proxy_gate,
        "Sandbox",
        SimpleNamespace(create=lambda **kwargs: sandbox),
    )
    monkeypatch.setattr(proxy_gate, "monotonic", clock.monotonic)
    monkeypatch.setattr(proxy_gate, "sleep", clock.sleep)

    proxy_gate.ProxyTlsGate("https://sandbox-proxy.test", "ca-pem").run()

    assert sandbox.files.writes == [(CA_STAGING_PATH, "ca-pem", "root")]
    assert sandbox.commands.calls[0] == (
        INSTALL_CA_COMMAND,
        "root",
        CA_INSTALL_TIMEOUT_SECONDS,
    )
    probes = [command for command, _, _ in sandbox.commands.calls[1:]]
    assert len(probes) == len(proxy_gate.CURL_PROXY_PENDING_EXIT_CODES) + 1
    assert all("--proxy-cacert" not in command for command in probes)
    assert all("https://invalid-run-token:@sandbox-proxy.test" in command for command in probes)
    assert all("%{http_connect}" in command for command in probes)
    assert all("printf '\\n%s' $?" in command for command in probes)
    assert all("|| true" not in command for command in probes)
    assert clock.sleeps == [proxy_gate.PROBE_DELAY_SECONDS] * len(
        proxy_gate.CURL_PROXY_PENDING_EXIT_CODES
    )
    assert sandbox.killed


def test_gate_bounds_pending_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    pending_probes = proxy_gate.PROXY_READY_TIMEOUT_SECONDS // proxy_gate.PROBE_DELAY_SECONDS + 1
    sandbox = _Sandbox(
        commands=_Commands(
            results=[
                _Result("000\n28", "Proxy connection timed out") for _ in range(pending_probes)
            ]
        )
    )
    clock = _Clock()
    monkeypatch.setattr(
        proxy_gate,
        "Sandbox",
        SimpleNamespace(create=lambda **kwargs: sandbox),
    )
    monkeypatch.setattr(proxy_gate, "monotonic", clock.monotonic)
    monkeypatch.setattr(proxy_gate, "sleep", clock.sleep)

    with pytest.raises(RuntimeError, match="curl exit 28"):
        proxy_gate.ProxyTlsGate("https://sandbox-proxy.test", "ca-pem").run()

    assert len(sandbox.commands.calls[1:]) == pending_probes
    assert clock.sleeps == [proxy_gate.PROBE_DELAY_SECONDS] * (pending_probes - 1)
    assert clock.now == proxy_gate.PROXY_READY_TIMEOUT_SECONDS
    assert proxy_gate.SANDBOX_TIMEOUT_SECONDS >= (
        CA_INSTALL_TIMEOUT_SECONDS
        + proxy_gate.PROXY_READY_TIMEOUT_SECONDS
        + proxy_gate.PROBE_TIMEOUT_SECONDS
    )
    assert sandbox.killed


@pytest.mark.parametrize(
    ("result", "error"),
    [
        (_Result("000\n35", "TLS handshake failed"), "curl exit 35"),
        (_Result("000\n60", "SSL certificate problem"), "curl exit 60"),
        (_Result("401\n0"), "curl exit 0"),
        (_Result("403"), "malformed curl result"),
    ],
)
def test_gate_fails_fast_on_non_pending_results(
    monkeypatch: pytest.MonkeyPatch,
    result: _Result,
    error: str,
) -> None:
    sandbox = _Sandbox(commands=_Commands(results=[result]))
    monkeypatch.setattr(
        proxy_gate,
        "Sandbox",
        SimpleNamespace(create=lambda **kwargs: sandbox),
    )
    monkeypatch.setattr(proxy_gate, "sleep", lambda _: pytest.fail("gate retried"))

    with pytest.raises(RuntimeError, match=error):
        proxy_gate.ProxyTlsGate("https://sandbox-proxy.test", "ca-pem").run()

    assert len(sandbox.commands.calls[1:]) == 1
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
    assert workflow.index("- name: Terraform apply") < workflow.index(
        "- name: Gate sandbox egress proxy TLS"
    )
