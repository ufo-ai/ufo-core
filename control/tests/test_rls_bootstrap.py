"""The bootstrap grants access only after every policy succeeds."""

import asyncio

import pytest

import ufo_control.main as control_main


def test_bootstrap_orders_policies_before_role_grants(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    async def policies(dsn: str) -> None:
        calls.append(("policies", dsn))

    async def role(dsn: str) -> None:
        calls.append(("role", dsn))

    monkeypatch.setattr(control_main, "owner_dsn", lambda: "owner-dsn")
    monkeypatch.setattr(control_main, "bootstrap_policies", policies)
    monkeypatch.setattr(control_main, "ensure_serve_role", role)

    asyncio.run(control_main._bootstrap())

    assert calls == [("policies", "owner-dsn"), ("role", "owner-dsn")]
