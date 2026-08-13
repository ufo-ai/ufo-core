"""The client-binary route: one native terminal client per build target, served from the deploy's
binary directory, refused as the same 404 for an unknown target, an unconfigured deploy, and a
target the directory does not hold."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ufo_control.gateway import CLIENT_BIN_DIR_ENV, CLIENT_TARGETS, gateway_app

DARWIN_TARGET = "aarch64-apple-darwin"
WINDOWS_TARGET = "x86_64-pc-windows-msvc"
BINARY_BYTES = b"\x7fELF\x00binary bytes"


def _client() -> TestClient:
    return TestClient(gateway_app())


def _stage(directory: Path, target: str, name: str) -> None:
    (directory / target).mkdir()
    (directory / target / name).write_bytes(BINARY_BYTES)


def test_serves_the_target_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stage(tmp_path, DARWIN_TARGET, "ufo")
    monkeypatch.setenv(CLIENT_BIN_DIR_ENV, str(tmp_path))
    response = _client().get(f"/ufo/bin/{DARWIN_TARGET}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.content == BINARY_BYTES


def test_windows_target_serves_the_exe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stage(tmp_path, WINDOWS_TARGET, "ufo.exe")
    monkeypatch.setenv(CLIENT_BIN_DIR_ENV, str(tmp_path))
    response = _client().get(f"/ufo/bin/{WINDOWS_TARGET}")
    assert response.status_code == 200
    assert response.content == BINARY_BYTES


def test_unknown_target_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLIENT_BIN_DIR_ENV, str(tmp_path))
    response = _client().get("/ufo/bin/mips-unknown-none")
    assert response.status_code == 404
    assert response.text == "no client binary for mips-unknown-none"


def test_unconfigured_deploy_refuses_every_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CLIENT_BIN_DIR_ENV, raising=False)
    for target in CLIENT_TARGETS:
        response = _client().get(f"/ufo/bin/{target}")
        assert response.status_code == 404
        assert response.text == f"no client binary for {target}"


def test_absent_binary_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLIENT_BIN_DIR_ENV, str(tmp_path))
    response = _client().get(f"/ufo/bin/{DARWIN_TARGET}")
    assert response.status_code == 404
    assert response.text == f"no client binary for {DARWIN_TARGET}"
