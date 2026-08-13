import asyncio
import base64
import gc
import gzip
import logging
import os
import re
import socket
import ssl
import struct
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from pathlib import Path
from typing import NamedTuple
from uuid import UUID, uuid4

import dns.asyncresolver
import pytest
import sqlalchemy as sa
from cryptography import x509
from cryptography.fernet import Fernet
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from sqlalchemy.ext.asyncio import AsyncConnection

import ufo.sandbox.proxy.server as proxy_server
from ufo import o11y
from ufo.agent_scope import agent
from ufo.connectors import CliCredential, ForwardedResponse
from ufo.credentials import CredentialStore, HostChoice
from ufo.db import workspace_tx
from ufo.ext.manifest import CredentialSlot, InjectionTarget, Manifest
from ufo.grants import GrantStore, grant_sentinel
from ufo.loop.queue import GIT_PROXY_AUTH_CONFIG, _git_config_env, _git_credential_config
from ufo.sandbox.proxy.rules import (
    OPENAI_HOST,
    REQUEST_METER_DIMENSION,
    ForwardRule,
    InjectionRule,
    InternetRule,
    MeterRule,
    ScopeRule,
    derive_credential_rules,
    derive_manifest_rules,
)
from ufo.sandbox.proxy.server import (
    MAX_FORWARD_BODY_BYTES,
    MAX_HEADER_BYTES,
    MAX_REFUSAL_DRAIN_BYTES,
    RELAY_CHUNK_BYTES,
    EgressProxy,
    HttpTokenUsage,
    PerAgentRules,
    _drain_refused_body,
    _EgressMeter,
    _forward_match,
    _forward_response_bytes,
    _inject,
    _read_request_body,
    _Refusal,
    _relay,
    generate_ca,
)
from ufo.sandbox.session import RunToken, RunTokenCodec
from ufo.schema import tables
from ufo.schema.records import Usage
from ufo.workspace import ws, ws_current

SEARCH_HOST = "api.search.test"
MODEL_HOST = "api.anthropic.com"

FULL_TOKEN_USAGE = Usage(
    input_tokens=1000,
    output_tokens=2000,
    cache_read_tokens=3000,
    cache_write_1h_tokens=4000,
)
RUN_TOKENS = RunTokenCodec(b"proxy-test-run-token-secret")
STOP_DEADLINE_SECONDS = 5
GRACE_WINDOW_SECONDS = 1
ORPHAN_CLEANUP_SECONDS = 0.2
SSE_RESPONSE_HEAD = b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\n\r\n"
ANTHROPIC_SSE_BODY = (
    b"event: message_start\r\n"
    b'data: {"type":"message_start","message":{"id":"m","model":"claude-opus-4-8",'
    b'"usage":{"input_tokens":1000,"cache_read_input_tokens":3000,'
    b'"cache_creation_input_tokens":4000,"cache_creation":{'
    b'"ephemeral_5m_input_tokens":0,"ephemeral_1h_input_tokens":4000},'
    b'"output_tokens":1}}}\r\n\r\n'
    b"event: content_block_delta\r\n"
    b'data: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"hi"}}\r\n\r\n'
    b"event: message_delta\r\n"
    b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    b'"usage":{"output_tokens":2000}}\r\n\r\n'
    b"event: message_stop\r\n"
    b'data: {"type":"message_stop"}\r\n\r\n'
)
ANTHROPIC_SSE = SSE_RESPONSE_HEAD + ANTHROPIC_SSE_BODY
OPENAI_SSE_BODY = (
    b'data: {"id":"c","object":"chat.completion.chunk","model":"gpt-5.4",'
    b'"choices":[{"delta":{"content":"hi"}}],"usage":null}\n\n'
    b'data: {"id":"c","object":"chat.completion.chunk","model":"gpt-5.4","choices":[],'
    b'"usage":{"prompt_tokens":1000000,"completion_tokens":1000000,"total_tokens":2000000}}\n\n'
    b"data: [DONE]\n\n"
)
OPENAI_SSE = SSE_RESPONSE_HEAD + OPENAI_SSE_BODY
ANTHROPIC_JSON_BODY = (
    b"HTTP/1.1 200 OK\r\n"
    b"content-type: application/json\r\n"
    b"\r\n"
    b'{"id":"msg","type":"message","role":"assistant","model":"claude-opus-4-8",'
    b'"content":[{"type":"text","text":"hi"}],"stop_reason":"end_turn",'
    b'"usage":{"input_tokens":1000,"cache_read_input_tokens":3000,'
    b'"cache_creation_input_tokens":4000,"cache_creation":{'
    b'"ephemeral_5m_input_tokens":0,"ephemeral_1h_input_tokens":4000},'
    b'"output_tokens":2000}}'
)
OPENAI_JSON_BODY = (
    b"HTTP/1.1 200 OK\r\n"
    b"content-type: application/json\r\n"
    b"\r\n"
    b'{"id":"c","object":"chat.completion","model":"gpt-5.4",'
    b'"choices":[{"message":{"role":"assistant","content":"hi"}}],'
    b'"usage":{"prompt_tokens":1000000,"completion_tokens":1000000,"total_tokens":2000000}}'
)
JSON_RESPONSE_HEAD = b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n"
OPENAI_CACHED_JSON_BODY = (
    JSON_RESPONSE_HEAD + b'{"id":"c","object":"chat.completion","model":"gpt-5.5",'
    b'"choices":[{"message":{"role":"assistant","content":"hi"}}],'
    b'"usage":{"prompt_tokens":100000,"completion_tokens":500,"total_tokens":100500,'
    b'"prompt_tokens_details":{"cached_tokens":90000}}}'
)
OPENAI_RESPONSES_JSON_BODY = (
    JSON_RESPONSE_HEAD + b'{"id":"resp","object":"response","model":"gpt-5.6-terra",'
    b'"output":[{"type":"message","content":[{"type":"output_text","text":"hi"}]}],'
    b'"usage":{"input_tokens":100000,"input_tokens_details":{"cached_tokens":90000},'
    b'"output_tokens":500,"output_tokens_details":{"reasoning_tokens":100},'
    b'"total_tokens":100500}}'
)
OPENAI_UNREADABLE_USAGE_JSON_BODY = (
    JSON_RESPONSE_HEAD + b'{"id":"c","object":"chat.completion","model":"gpt-5.5",'
    b'"choices":[{"message":{"role":"assistant","content":"hi"}}],'
    b'"usage":{"tokens_read":100000,"tokens_written":500}}'
)
OPENAI_OVER_CACHED_JSON_BODY = (
    JSON_RESPONSE_HEAD + b'{"id":"c","object":"chat.completion","model":"gpt-5.5",'
    b'"choices":[{"message":{"role":"assistant","content":"hi"}}],'
    b'"usage":{"prompt_tokens":100000,"completion_tokens":500,"total_tokens":100500,'
    b'"prompt_tokens_details":{"cached_tokens":100001}}}'
)


def _fixed(rules: tuple = ()) -> PerAgentRules:
    """The real resolver with no grant store: every run resolves to this fixed base — a genuine
    (degenerate) resolution, not a fake, standing in where a test drives paths other than grants."""
    return PerAgentRules(base=rules, grants=None)


def _egress(resolver: PerAgentRules, ca_cert: str = "x", ca_key: str = "x") -> EgressProxy:
    """Wire the proxy from a real resolver: its `resolve` for rules and its `turn_live` for the
    keyed-host liveness gate — both genuine `PerAgentRules` methods, never a fake."""
    return EgressProxy(
        resolve=resolver.resolve,
        authorize=resolver.turn_live,
        ca_cert=ca_cert,
        ca_key=ca_key,
        run_tokens=RUN_TOKENS,
    )


async def _proxy() -> EgressProxy:
    cert, key = await generate_ca()
    proxy = _egress(_fixed(), ca_cert=cert, ca_key=key)
    await proxy.start(bind_host="127.0.0.1")
    return proxy


def _basic(run_token: str) -> str:
    return "Basic " + base64.b64encode(f"{run_token}:".encode()).decode()


async def _connect(port: int, host: str, run_token: str = "", target_port: int | str = 443) -> int:
    """Drive one CONNECT through the proxy over its bound socket and return the status code."""
    status, _ = await _connect_reason(port, host, run_token, target_port)
    return status


async def _connect_reason(
    port: int, host: str, run_token: str = "", target_port: int | str = 443
) -> tuple[int, bytes]:
    """One CONNECT's status and the proxy's own plain-text reason — what an HTTP client inside the
    sandbox surfaces, and the only place a refusal says why. Drains to EOF so any off-relay
    metering the exchange schedules is queued before the caller stops the proxy."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    head = f"CONNECT {host}:{target_port} HTTP/1.1\r\nHost: {host}\r\n"
    if run_token:
        head += f"Proxy-Authorization: {_basic(run_token)}\r\n"
    writer.write((head + "\r\n").encode())
    await writer.drain()
    status_line = await reader.readline()
    _, _, body = (await reader.read()).partition(b"\r\n\r\n")
    writer.close()
    return int(status_line.split()[1]), body


async def _get(port: int, path: str) -> tuple[int, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nhost: localhost\r\n\r\n".encode())
    await writer.drain()
    head, _, body = (await reader.read()).partition(b"\r\n\r\n")
    writer.close()
    return int(head.split()[1]), body


async def test_stop_waits_for_active_connection_tasks() -> None:
    proxy = _egress(_fixed())
    release = asyncio.Event()
    connection = asyncio.create_task(release.wait())
    proxy._connection_tasks.add(connection)
    stopping = asyncio.create_task(proxy.stop(graceful_shutdown_seconds=1))
    await asyncio.sleep(0)
    assert not stopping.done()
    release.set()
    await stopping


async def test_handle_refuses_connections_once_the_listener_closed() -> None:
    """The accept race: a connection accepted just before the listener closes schedules its
    handler after stop's drain snapshot, invisible to the timed wait and the cancel sweep. Entry
    refuses service the moment the listener stops, so a late handler finishes at once instead of
    parking `wait_closed()` beyond the drain window."""
    proxy = _egress(_fixed())
    await proxy.start(bind_host="127.0.0.1")
    assert proxy._server is not None
    proxy._server.close()
    ours, theirs = socket.socketpair()
    reader, writer = await asyncio.open_connection(sock=ours)
    await proxy._handle(reader, writer)
    assert not proxy._connection_tasks
    theirs.close()
    await proxy.stop(graceful_shutdown_seconds=0)


async def test_stop_cancels_a_started_servers_live_connection() -> None:
    """A handler parked on a live socket keeps `wait_closed()` pending forever, so stop must
    drain and cancel connections before waiting the server down — not after."""
    proxy = _egress(_fixed())
    endpoint = await proxy.start(bind_host="127.0.0.1")
    _, writer = await asyncio.open_connection("127.0.0.1", endpoint.port)
    for _ in range(100):
        if proxy._connection_tasks:
            break
        await asyncio.sleep(0)
    assert proxy._connection_tasks
    await asyncio.wait_for(proxy.stop(graceful_shutdown_seconds=0), timeout=5)
    assert not proxy._connection_tasks
    writer.close()


async def test_a_non_connect_method_is_refused() -> None:
    """The proxy speaks only CONNECT — it serves no HTTP resource of its own, so a plain GET
    answers 405 and nothing else."""
    proxy = await _proxy()
    try:
        port = proxy._server.sockets[0].getsockname()[1]
        status, body = await _get(port, "/anything")
    finally:
        await proxy.stop()

    assert status == 405
    assert b"only CONNECT is proxied" in body


async def test_a_resolution_error_is_not_cached_or_disguised_as_policy(
    caplog: pytest.LogCaptureFixture,
) -> None:
    base = (ScopeRule(allowed_hosts=frozenset({MODEL_HOST})),)
    granted = (
        *base,
        ScopeRule(allowed_hosts=frozenset({SEARCH_HOST})),
        InjectionRule(host=SEARCH_HOST, header="authorization", sentinel="s", real="r"),
    )
    calls = {"n": 0}

    async def flaky(run: RunToken | None) -> tuple[object, ...]:
        if run is None:
            return base
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient db blip")
        return granted

    proxy = EgressProxy(
        resolve=flaky,
        authorize=_fixed().turn_live,
        ca_cert="",
        ca_key="",
        run_tokens=RUN_TOKENS,
    )
    run = RunToken(uuid4(), uuid4())
    with (
        caplog.at_level(logging.ERROR, logger="ufo"),
        pytest.raises(RuntimeError, match="transient db blip"),
    ):
        await proxy._rules_for(run)
    failed = next(record for record in caplog.records if record.message == "egress.resolve_failed")
    assert failed.levelno == logging.ERROR
    assert failed.ufo == {
        "workspace_id": str(run.workspace_id),
        "turn": str(run.turn_id),
        "error_class": "RuntimeError",
    }
    assert await proxy._rules_for(run) == granted


async def test_a_resolution_error_is_service_unavailable_not_egress_denied(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A run whose own rules fault while the base resolves fine — a credential slot's store
    unreachable for that workspace. Substituting the base would answer the sandbox 403 for
    SEARCH_HOST, indistinguishable from the workspace never having been granted it. The fault is
    recorded once, where it is raised."""
    base = (ScopeRule(allowed_hosts=frozenset({MODEL_HOST})),)

    async def resolve(run: RunToken | None) -> tuple:
        if run is None:
            return base
        raise RuntimeError("rules unavailable")

    async def authorize(_run: RunToken) -> bool:
        return True

    cert, key = await generate_ca()
    proxy = EgressProxy(
        resolve=resolve,
        authorize=authorize,
        ca_cert=cert,
        ca_key=key,
        run_tokens=RUN_TOKENS,
    )
    endpoint = await proxy.start(bind_host="127.0.0.1")
    try:
        token = RUN_TOKENS.encode(RunToken(uuid4(), uuid4()))
        with caplog.at_level(logging.ERROR, logger="ufo"):
            assert await _connect_reason(endpoint.port, SEARCH_HOST, token) == (
                503,
                b"egress authorization unavailable",
            )
        assert [record.message for record in caplog.records if record.name == "ufo"] == [
            "egress.resolve_failed"
        ]
    finally:
        await proxy.stop()


async def test_concurrent_rule_cache_misses_share_one_resolution() -> None:
    calls = 0
    rules = (ScopeRule(allowed_hosts=frozenset({MODEL_HOST})),)

    async def resolve(_run: RunToken | None) -> tuple:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return rules

    proxy = EgressProxy(
        resolve=resolve,
        authorize=_fixed().turn_live,
        ca_cert="",
        ca_key="",
        run_tokens=RUN_TOKENS,
    )
    run = RunToken(uuid4(), uuid4())
    assert await asyncio.gather(*(proxy._rules_for(run) for _ in range(20))) == [rules] * 20
    assert calls == 1


async def test_concurrent_rule_cache_misses_share_one_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0
    callers = 20
    entered = 0
    start = asyncio.Event()
    all_entered = asyncio.Event()
    release = asyncio.Event()
    rules = (ScopeRule(allowed_hosts=frozenset({MODEL_HOST})),)

    async def resolve(_run: RunToken | None) -> tuple:
        nonlocal calls
        calls += 1
        if calls == 1:
            await release.wait()
            raise RuntimeError("rules unavailable")
        return rules

    proxy = EgressProxy(
        resolve=resolve,
        authorize=_fixed().turn_live,
        ca_cert="",
        ca_key="",
        run_tokens=RUN_TOKENS,
    )
    run = RunToken(uuid4(), uuid4())

    async def request_rules() -> tuple:
        nonlocal entered
        await start.wait()
        entered += 1
        if entered == callers:
            all_entered.set()
        return await proxy._rules_for(run)

    waiters = tuple(asyncio.create_task(request_rules()) for _ in range(callers))
    start.set()
    await all_entered.wait()
    assert calls == 1
    with caplog.at_level(logging.ERROR, logger="ufo"):
        release.set()
        results = await asyncio.gather(*waiters, return_exceptions=True)
    assert [record.message for record in caplog.records if record.name == "ufo"] == [
        "egress.resolve_failed"
    ]
    assert all(type(result) is RuntimeError for result in results)
    assert {str(result) for result in results} == {"rules unavailable"}
    assert proxy._rule_tasks == {}
    assert await proxy._rules_for(run) == rules
    assert calls == 2


async def test_cancelled_rule_waiter_leaves_shared_resolution_owned() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    rules = (ScopeRule(allowed_hosts=frozenset({MODEL_HOST})),)

    async def resolve(_run: RunToken | None) -> tuple:
        started.set()
        await release.wait()
        return rules

    proxy = EgressProxy(
        resolve=resolve,
        authorize=_fixed().turn_live,
        ca_cert="",
        ca_key="",
        run_tokens=RUN_TOKENS,
    )
    run = RunToken(uuid4(), uuid4())
    waiter = asyncio.create_task(proxy._rules_for(run))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    shared = proxy._rule_tasks[run]
    release.set()
    await shared
    assert proxy._rule_tasks == {}
    assert await proxy._rules_for(run) == rules


async def test_a_detached_failing_rule_resolution_is_never_reported_by_asyncio(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The last waiter cancelling detaches the shared resolution, so its fault has no reader. Left
    unretrieved, asyncio reports it on its own logger with the exception rendered in full — a text
    the OTLP bridge exports and field-name redaction cannot reach."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def resolve(_run: RunToken | None) -> tuple:
        started.set()
        await release.wait()
        raise RuntimeError("postgresql://ufo:hunter2@db.test/ufo is unreachable")

    proxy = EgressProxy(
        resolve=resolve,
        authorize=_fixed().turn_live,
        ca_cert="",
        ca_key="",
        run_tokens=RUN_TOKENS,
    )
    run = RunToken(uuid4(), uuid4())
    waiter = asyncio.create_task(proxy._rules_for(run))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    detached = proxy._rule_tasks[run]
    release.set()
    with caplog.at_level(logging.ERROR, logger="asyncio"):
        await asyncio.wait({detached})
        del detached
        gc.collect()
    assert [record.getMessage() for record in caplog.records if record.name == "asyncio"] == []


async def test_stop_drains_a_detached_failing_rule_resolution() -> None:
    started = asyncio.Event()
    finished = asyncio.Event()

    async def resolve(_run: RunToken | None) -> tuple:
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError as error:
            raise RuntimeError("rules unavailable") from error
        finally:
            finished.set()

    proxy = EgressProxy(
        resolve=resolve,
        authorize=_fixed().turn_live,
        ca_cert="",
        ca_key="",
        run_tokens=RUN_TOKENS,
    )
    run = RunToken(uuid4(), uuid4())
    waiter = asyncio.create_task(proxy._rules_for(run))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    shared = proxy._rule_tasks[run]
    try:
        await proxy.stop()
        assert finished.is_set()
        assert shared.done()
        assert proxy._rule_tasks == {}
    finally:
        if not shared.done():
            shared.cancel()
        await asyncio.gather(shared, return_exceptions=True)


async def test_a_cancelled_rule_resolution_reaches_no_loop_exception_handler(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`stop()` cancels an orphan that does not intercept the cancel, so it ends cancelled rather
    than faulted. Reading an exception off a cancelled task raises `CancelledError` in the
    done-callback itself, which the loop reports — the fault reader must skip that state."""
    started = asyncio.Event()

    async def resolve(_run: RunToken | None) -> tuple:
        started.set()
        await asyncio.Future()
        return ()

    proxy = EgressProxy(
        resolve=resolve,
        authorize=_fixed().turn_live,
        ca_cert="",
        ca_key="",
        run_tokens=RUN_TOKENS,
    )
    run = RunToken(uuid4(), uuid4())
    waiter = asyncio.create_task(proxy._rules_for(run))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    orphan = proxy._rule_tasks[run]
    loop = asyncio.get_running_loop()
    unhandled: list[dict] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        with caplog.at_level(logging.ERROR, logger="asyncio"):
            await proxy.stop()
            await asyncio.sleep(0)
        assert orphan.cancelled()
        assert unhandled == []
        assert [record.getMessage() for record in caplog.records if record.name == "asyncio"] == []
    finally:
        loop.set_exception_handler(previous)


async def test_stop_waits_out_the_grace_window_for_an_orphaned_rule_resolution() -> None:
    """The window's value is threaded through, not just its boundedness: an orphan whose cleanup
    needs several loop passes finishes inside a nonzero `graceful_shutdown_seconds`, where a
    zero-length wait would abandon it still pending."""
    started = asyncio.Event()
    resume = asyncio.Event()
    cleanup_passes = 10

    async def resolve(_run: RunToken | None) -> tuple:
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await resume.wait()
        return ()

    async def let_cleanup_finish() -> None:
        for _ in range(cleanup_passes):
            await asyncio.sleep(0)
        resume.set()

    proxy = EgressProxy(
        resolve=resolve,
        authorize=_fixed().turn_live,
        ca_cert="",
        ca_key="",
        run_tokens=RUN_TOKENS,
    )
    run = RunToken(uuid4(), uuid4())
    waiter = asyncio.create_task(proxy._rules_for(run))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    orphan = proxy._rule_tasks[run]
    releaser = asyncio.create_task(let_cleanup_finish())
    try:
        await proxy.stop(graceful_shutdown_seconds=STOP_DEADLINE_SECONDS)
        assert orphan.done()
    finally:
        resume.set()
        await asyncio.gather(releaser, orphan, return_exceptions=True)


async def test_stop_leaves_a_rule_resolution_that_outlives_its_cancel_pending() -> None:
    """An orphaned resolution gets the connection drain's window, never a second unbounded one: a
    cleanup that outlives its own cancel is left pending rather than parking the shutdown, so the
    caller's wait ends."""
    started = asyncio.Event()
    finish = asyncio.Event()

    async def resolve(_run: RunToken | None) -> tuple:
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await finish.wait()
        return ()

    proxy = EgressProxy(
        resolve=resolve,
        authorize=_fixed().turn_live,
        ca_cert="",
        ca_key="",
        run_tokens=RUN_TOKENS,
    )
    run = RunToken(uuid4(), uuid4())
    waiter = asyncio.create_task(proxy._rules_for(run))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    orphan = proxy._rule_tasks[run]
    try:
        await asyncio.wait_for(proxy.stop(), timeout=STOP_DEADLINE_SECONDS)
        assert not orphan.done()
    finally:
        finish.set()
        await asyncio.gather(orphan, return_exceptions=True)


async def test_stop_spends_one_grace_window_across_both_drains() -> None:
    """The connection drain and the orphaned-rule drain share one window, not one each. A parked
    connection spends the whole window, so the orphan is cancelled against nothing left and is
    abandoned pending — with a second window of its own it would instead finish its cleanup, and
    a caller mapping this to a termination grace period would overrun by the difference."""
    started = asyncio.Event()
    connection_parked = asyncio.Event()
    cleanup_started = asyncio.Event()

    async def resolve(_run: RunToken | None) -> tuple:
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cleanup_started.set()
            await asyncio.sleep(ORPHAN_CLEANUP_SECONDS)
        return ()

    async def authorize(_run: RunToken) -> bool:
        connection_parked.set()
        await asyncio.Future()
        return True

    cert, key = await generate_ca()
    proxy = EgressProxy(
        resolve=resolve,
        authorize=authorize,
        ca_cert=cert,
        ca_key=key,
        run_tokens=RUN_TOKENS,
    )
    endpoint = await proxy.start(bind_host="127.0.0.1")
    parked_token = _basic(RUN_TOKENS.encode(RunToken(uuid4(), uuid4())))
    _, parked = await asyncio.open_connection("127.0.0.1", endpoint.port)
    parked.write(
        f"CONNECT {MODEL_HOST}:443 HTTP/1.1\r\nHost: {MODEL_HOST}\r\n"
        f"Proxy-Authorization: {parked_token}\r\n\r\n".encode()
    )
    await parked.drain()
    await connection_parked.wait()
    run = RunToken(uuid4(), uuid4())
    waiter = asyncio.create_task(proxy._rules_for(run))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    orphan = proxy._rule_tasks[run]
    try:
        await proxy.stop(graceful_shutdown_seconds=GRACE_WINDOW_SECONDS)
        assert cleanup_started.is_set()
        assert not orphan.done()
    finally:
        parked.close()
        orphan.cancel()
        await asyncio.gather(orphan, return_exceptions=True)


async def test_rule_cache_refreshes_before_injected_tokens_expire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1000.0
    calls = 0

    async def resolve(_run: RunToken | None) -> tuple:
        nonlocal calls
        calls += 1
        return (InjectionRule(MODEL_HOST, "authorization", "s", f"token-{calls}"),)

    monkeypatch.setattr(proxy_server.time, "monotonic", lambda: now)
    proxy = EgressProxy(
        resolve=resolve,
        authorize=_fixed().turn_live,
        ca_cert="",
        ca_key="",
        run_tokens=RUN_TOKENS,
    )
    run = RunToken(uuid4(), uuid4())
    first = await proxy._rules_for(run)
    now += proxy_server.RULE_CACHE_TTL_SECONDS + 1
    second = await proxy._rules_for(run)
    assert first != second
    assert calls == 2


class _Seeded(NamedTuple):
    workspace_id: UUID
    turn_id: UUID
    agent_id: UUID
    member_id: UUID
    conversation_id: UUID


async def _seed_turn(
    connection: AsyncConnection,
    status: str = "running",
    speaker: bool = False,
    internet_access_allowed: bool = True,
) -> _Seeded:
    workspace_id, member_id, agent_id, conversation_id, turn_id = (uuid4() for _ in range(5))
    await connection.execute(
        sa.insert(tables.workspace).values(
            id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
        )
    )
    await connection.execute(
        sa.insert(tables.member).values(
            id=member_id,
            workspace_id=workspace_id,
            email="a@b.c",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    await connection.execute(
        sa.insert(tables.agent).values(
            id=agent_id,
            workspace_id=workspace_id,
            name="assistant",
            prompt="p",
            model="claude-opus-4-8",
            internet_access_allowed=internet_access_allowed,
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
            queue_key=uuid4().hex,
            member_id=member_id,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    terminal = None if status in ("queued", "running", "parked") else {"status": status}
    await connection.execute(
        sa.insert(tables.turn).values(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status=status,
            inbound="hi",
            speaker_member_id=member_id if speaker else None,
            terminal=terminal,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return _Seeded(workspace_id, turn_id, agent_id, member_id, conversation_id)


async def test_concurrent_first_contact_mints_one_leaf_per_host() -> None:
    proxy = await _proxy()
    try:
        first, second = await asyncio.gather(
            proxy._leaf_context("api.example.com"),
            proxy._leaf_context("api.example.com"),
        )
        assert first is second
        assert first is proxy._contexts["api.example.com"]
    finally:
        await proxy.stop()


async def test_distinct_hosts_get_distinct_contexts() -> None:
    proxy = await _proxy()
    try:
        one, two = await asyncio.gather(
            proxy._leaf_context("api.anthropic.com"),
            proxy._leaf_context("api.openai.com"),
        )
        assert one is not two
        assert proxy._contexts["api.anthropic.com"] is one
        assert proxy._contexts["api.openai.com"] is two
    finally:
        await proxy.stop()


async def test_a_host_that_is_really_a_path_never_names_a_file_outside_the_workdir(
    tmp_path: Path,
) -> None:
    """The CONNECT host arrives from inside the sandbox, and the rule match that lets it through is
    an equality allowlist, not a path check — so the host named the leaf files and openssl's `-out`
    with no containment of its own. It is cut to one filename component now, and a host that leaves
    nothing usable behind falls back rather than naming the workdir itself."""
    outside = tmp_path / "outside"
    outside.mkdir()
    proxy = await _proxy()
    try:
        workdir = Path(proxy._workdir.name)
        with pytest.raises(RuntimeError, match="openssl"):
            await proxy._leaf_context(f"../..{outside}/api.example.com")
        assert list(outside.iterdir()) == []
        assert (workdir / "leaf-api.example.com.ext").is_file()

        assert await proxy._leaf_context("..") is proxy._contexts[".."]
        assert (workdir / "leaf-unnamed.crt").is_file()
    finally:
        await proxy.stop()


async def test_the_leaf_extension_file_is_staged_rather_than_written_through_a_name(
    tmp_path: Path,
) -> None:
    """The extension file goes through the shared guard, which stages `O_CREAT|O_EXCL|O_NOFOLLOW`
    and renames: a name a symlink holds takes the rename, never the write, so the bytes cannot land
    in whatever it pointed at."""
    outside = tmp_path / "outside.ext"
    outside.write_text("untouched")
    proxy = await _proxy()
    try:
        planted = Path(proxy._workdir.name) / "leaf-api.example.com.ext"
        planted.symlink_to(outside)
        await proxy._leaf_context("api.example.com")
        assert outside.read_text() == "untouched"
        assert not planted.is_symlink()
        assert "subjectAltName=DNS:api.example.com" in planted.read_text()
    finally:
        await proxy.stop()


async def test_the_ca_and_leaf_outlive_a_long_running_process() -> None:
    """The proxy outlives every turn for the life of the process, so its boot-minted CA and every
    cached per-host leaf must stay valid far beyond a day — a deploy running past 24h with a 1-day
    cert would serve an expired chain and break in-sandbox TLS. The CA outlives the leaf it signs,
    so no leaf is left valid past its issuer."""
    now = datetime.now(UTC)
    cert_pem, _ = await generate_ca()
    assert x509.load_pem_x509_certificate(cert_pem.encode()).not_valid_after_utc > now + timedelta(
        days=300
    )

    proxy = await _proxy()
    try:
        await proxy._leaf_context(MODEL_HOST)
        root = Path(proxy._workdir.name)
        ca = x509.load_pem_x509_certificate((root / "ca.crt").read_bytes())
        leaf = x509.load_pem_x509_certificate((root / f"leaf-{MODEL_HOST}.crt").read_bytes())
    finally:
        await proxy.stop()
    assert leaf.not_valid_after_utc > now + timedelta(days=300)
    assert leaf.not_valid_after_utc <= ca.not_valid_after_utc


def test_egress_metering_counts_the_host_and_the_dimension_that_admitted_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The counter is a fleet total per metered host: one proxy serves every workspace."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(o11y.metrics, "get_meter", provider.get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    _egress(_fixed())._meter(SEARCH_HOST, (MeterRule(host=SEARCH_HOST, dimension="search"),))
    points = [
        point
        for resource in reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == "ufo.sandbox_egress_total"
        for point in metric.data.data_points
    ]
    assert [dict(point.attributes) for point in points] == [
        {"host": SEARCH_HOST, "dimension": "search"}
    ]


async def test_egress_write_attributes_a_row_to_the_turn(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)
    proxy = _egress(_fixed())
    run = RunToken(workspace_id, turn_id)
    rules = (MeterRule(host=SEARCH_HOST, dimension="search"),)
    await proxy._meter_ledger(SEARCH_HOST, run, rules)
    await proxy.stop()
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.ledger.c.dimension, tables.ledger.c.amount).where(
                    tables.ledger.c.turn_id == turn_id
                )
            )
        ).one()
    assert (row.dimension, int(row.amount)) == ("egress", 1)


async def test_meter_ledger_batches_credential_requests_in_one_transaction(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)
    rules = (
        MeterRule(host=SEARCH_HOST, dimension="search"),
        MeterRule(host=MODEL_HOST, dimension="tokens"),
    )
    proxy = _egress(_fixed(rules))
    run = RunToken(workspace_id, turn_id)
    transactions = 0
    original_workspace_tx = proxy_server.workspace_tx

    @asynccontextmanager
    async def counted_workspace_tx():
        nonlocal transactions
        transactions += 1
        async with original_workspace_tx() as connection:
            yield connection

    monkeypatch.setattr(proxy_server, "workspace_tx", counted_workspace_tx)
    await proxy._meter_ledger(MODEL_HOST, run, rules)
    assert proxy._meter_worker is None
    for _ in range(10):
        await proxy._meter_ledger(SEARCH_HOST, run, rules)
    assert proxy._meter_worker is not None
    await proxy.stop()
    async with workspace_tx() as connection:
        dimensions = (
            (
                await connection.execute(
                    sa.select(tables.ledger.c.dimension).where(tables.ledger.c.turn_id == turn_id)
                )
            )
            .scalars()
            .all()
        )
    assert list(dimensions) == ["egress"]
    async with workspace_tx() as connection:
        amount = (
            await connection.execute(
                sa.select(tables.ledger.c.amount).where(tables.ledger.c.turn_id == turn_id)
            )
        ).scalar_one()
    assert int(amount) == 10
    assert transactions == 1


async def test_meter_worker_continues_after_any_failed_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = _egress(_fixed())
    first = _EgressMeter(RunToken(uuid4(), uuid4()))
    second = _EgressMeter(RunToken(uuid4(), uuid4()))
    attempts: list[list[object]] = []

    async def write(records: list[object]) -> None:
        attempts.append(records)
        if len(attempts) == 1:
            raise RuntimeError("meter write failed outside the database driver")

    monkeypatch.setattr(proxy, "_write_meter_batch", write)
    await proxy._enqueue_meter(first)
    await proxy._meter_queue.join()
    assert proxy._meter_worker is not None
    assert not proxy._meter_worker.done()
    await proxy._enqueue_meter(second)
    await proxy._meter_queue.join()
    await proxy.stop()
    assert attempts == [[first], [second]]


async def test_failed_meter_run_does_not_drop_other_runs(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)
    proxy = _egress(_fixed())
    await proxy._write_meter_batch(
        [
            _EgressMeter(RunToken(workspace_id, uuid4())),
            _EgressMeter(RunToken(workspace_id, turn_id)),
        ]
    )
    async with workspace_tx() as connection:
        amount = (
            await connection.execute(
                sa.select(tables.ledger.c.amount).where(tables.ledger.c.turn_id == turn_id)
            )
        ).scalar_one()
    assert int(amount) == 1


async def test_meter_queue_applies_backpressure_at_its_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proxy_server, "METER_QUEUE_MAX", 1)
    proxy = _egress(_fixed())
    entered = asyncio.Event()
    release = asyncio.Event()

    async def write(records: list[object]) -> None:
        entered.set()
        await release.wait()

    monkeypatch.setattr(proxy, "_write_meter_batch", write)
    run = RunToken(uuid4(), uuid4())
    await proxy._enqueue_meter(_EgressMeter(run))
    await entered.wait()
    await proxy._enqueue_meter(_EgressMeter(run))
    blocked = asyncio.create_task(proxy._enqueue_meter(_EgressMeter(run)))
    await asyncio.sleep(0)
    assert proxy._meter_queue.full()
    assert not blocked.done()
    release.set()
    await blocked
    await proxy._meter_queue.join()
    await proxy.stop()


async def test_client_reset_while_awaiting_the_request_line_does_not_crash_the_server() -> None:
    """A peer that resets the connection while `_handle` awaits the request line (a health check
    probe, a client that hangs up early) is routine TCP behavior, not a bug — it must not surface as
    an unhandled exception in `client_connected_cb`, and the server must keep serving other
    connections afterward."""
    cert, key = await generate_ca()
    proxy = _egress(_fixed(), ca_cert=cert, ca_key=key)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    loop = asyncio.get_running_loop()
    unhandled: list[dict] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", endpoint.port))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        sock.close()
        await asyncio.sleep(0.1)
        assert unhandled == []
        assert await _connect(endpoint.port, MODEL_HOST) == 403
    finally:
        loop.set_exception_handler(previous)
        await proxy.stop()


async def test_client_reset_during_the_mitm_handshake_does_not_crash_the_server(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)
    rules = (
        ScopeRule(allowed_hosts=frozenset({MODEL_HOST})),
        InjectionRule(host=MODEL_HOST, header="x-api-key", sentinel="s", real="REAL-KEY"),
    )
    cert, key = await generate_ca()
    proxy = _egress(_fixed(rules), ca_cert=cert, ca_key=key)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    loop = asyncio.get_running_loop()
    unhandled: list[dict] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        token = RUN_TOKENS.encode(RunToken(workspace_id, turn_id))
        reader, writer = await asyncio.open_connection("127.0.0.1", endpoint.port)
        writer.write(
            f"CONNECT {MODEL_HOST}:443 HTTP/1.1\r\n"
            f"Proxy-Authorization: {_basic(token)}\r\n\r\n".encode()
        )
        await writer.drain()
        assert (await reader.readline()).startswith(b"HTTP/1.1 200")
        sock = writer.get_extra_info("socket")
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        writer.close()
        await asyncio.sleep(0.2)
        assert unhandled == []
        assert await _connect(endpoint.port, MODEL_HOST) == 403
    finally:
        loop.set_exception_handler(previous)
        await proxy.stop()


async def test_client_reset_after_the_mitm_handshake_does_not_crash_the_server(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)
    rules = (
        ScopeRule(allowed_hosts=frozenset({MODEL_HOST})),
        InjectionRule(host=MODEL_HOST, header="x-api-key", sentinel="s", real="REAL-KEY"),
    )
    cert, key = await generate_ca()
    proxy = _egress(_fixed(rules), ca_cert=cert, ca_key=key)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    loop = asyncio.get_running_loop()
    unhandled: list[dict] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))

    try:
        token = RUN_TOKENS.encode(RunToken(workspace_id, turn_id))
        reader, writer = await asyncio.open_connection("127.0.0.1", endpoint.port)
        writer.write(
            f"CONNECT {MODEL_HOST}:443 HTTP/1.1\r\n"
            f"Proxy-Authorization: {_basic(token)}\r\n\r\n".encode()
        )
        await writer.drain()
        assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")
        context = ssl.create_default_context(cadata=cert)
        await writer.start_tls(context, server_hostname=MODEL_HOST)
        sock = writer.get_extra_info("socket")
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        writer.transport.abort()
        await asyncio.sleep(0.2)
        assert unhandled == []
        assert await _connect(endpoint.port, MODEL_HOST) == 403
    finally:
        loop.set_exception_handler(previous)
        await proxy.stop()


async def test_pre_auth_header_deadline_closes_a_stalled_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proxy_server, "PROXY_HEADER_TIMEOUT_SECONDS", 0.01)
    proxy = await _proxy()
    try:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", proxy._server.sockets[0].getsockname()[1]
        )
        status = await reader.readline()
        writer.close()
    finally:
        await proxy.stop()
    assert status.startswith(b"HTTP/1.1 408 ")


async def test_pre_auth_headers_are_bounded() -> None:
    proxy = await _proxy()
    try:
        port = proxy._server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            b"CONNECT example.com:443 HTTP/1.1\r\nx-padding: "
            + b"x" * MAX_HEADER_BYTES
            + b"\r\n\r\n"
        )
        await writer.drain()
        status = await reader.readline()
        writer.close()
    finally:
        await proxy.stop()
    assert status.startswith(b"HTTP/1.1 431 ")


async def test_post_tls_headers_are_bounded(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)
    rules = (
        ScopeRule(allowed_hosts=frozenset({MODEL_HOST})),
        InjectionRule(host=MODEL_HOST, header="x-api-key", sentinel="s", real="REAL-KEY"),
    )
    cert, key = await generate_ca()
    proxy = _egress(_fixed(rules), ca_cert=cert, ca_key=key)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    token = RUN_TOKENS.encode(RunToken(workspace_id, turn_id))
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", endpoint.port)
        writer.write(
            f"CONNECT {MODEL_HOST}:443 HTTP/1.1\r\n"
            f"Proxy-Authorization: {_basic(token)}\r\n\r\n".encode()
        )
        await writer.drain()
        assert (await reader.readline()).startswith(b"HTTP/1.1 200 ")
        while (await reader.readline()) not in (b"\r\n", b""):
            pass
        context = ssl.create_default_context(cadata=cert)
        await writer.start_tls(context, server_hostname=MODEL_HOST)
        writer.write(b"GET / HTTP/1.1\r\nx-padding: " + b"x" * MAX_HEADER_BYTES + b"\r\n\r\n")
        await writer.drain()
        status = await reader.readline()
        await reader.read()
        writer.close()
    finally:
        await proxy.stop()
    assert status.startswith(b"HTTP/1.1 431 ")


async def test_proxy_connection_count_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proxy_server, "MAX_PROXY_CONNECTIONS", 1)
    proxy = await _proxy()
    port = proxy._server.sockets[0].getsockname()[1]
    first_reader, first_writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        for _ in range(10):
            if proxy._active_connections == 1:
                break
            await asyncio.sleep(0)
        second_reader, second_writer = await asyncio.open_connection("127.0.0.1", port)
        assert (await second_reader.readline()).startswith(b"HTTP/1.1 503 ")
        await second_reader.read()
        second_writer.close()
    finally:
        first_writer.close()
        await first_reader.read()
        await proxy.stop()


async def test_proxy_connection_limit_preserves_capacity_between_workspaces(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(proxy_server, "MAX_PROXY_CONNECTIONS", 3)
    monkeypatch.setattr(proxy_server, "MAX_PROXY_CONNECTIONS_PER_WORKSPACE", 1)
    async with workspace_tx() as connection:
        first = await _seed_turn(connection)
        second = await _seed_turn(connection)

    async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read()
        writer.close()

    stub = await asyncio.start_server(upstream, "127.0.0.1", 0)
    stub_port = stub.sockets[0].getsockname()[1]
    cert, key = await generate_ca()
    resolver = _fixed((ScopeRule(allowed_hosts=frozenset({"127.0.0.1"})),))
    proxy = EgressProxy(
        resolve=resolver.resolve,
        authorize=resolver.turn_live,
        ca_cert=cert,
        ca_key=key,
        run_tokens=RUN_TOKENS,
    )
    endpoint = await proxy.start(bind_host="127.0.0.1")
    clients: list[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = []

    async def connect(run: RunToken) -> int:
        reader, writer = await asyncio.open_connection("127.0.0.1", endpoint.port)
        clients.append((reader, writer))
        token = RUN_TOKENS.encode(run)
        writer.write(
            f"CONNECT 127.0.0.1:{stub_port} HTTP/1.1\r\n"
            f"Proxy-Authorization: {_basic(token)}\r\n\r\n".encode()
        )
        await writer.drain()
        return int((await reader.readline()).split()[1])

    try:
        assert await connect(RunToken(first.workspace_id, first.turn_id)) == 200
        assert await connect(RunToken(first.workspace_id, first.turn_id)) == 429
        assert await connect(RunToken(second.workspace_id, second.turn_id)) == 200
        assert proxy._workspace_connections == {
            first.workspace_id: 1,
            second.workspace_id: 1,
        }
    finally:
        for _, writer in clients:
            writer.close()
        await proxy.stop()
        stub.close()
        await stub.wait_closed()


async def test_a_db_fault_in_the_authorize_gate_returns_service_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:

    async def refused(run: RunToken) -> bool:
        raise ConnectionRefusedError("db connection refused")

    rules = (
        ScopeRule(allowed_hosts=frozenset({MODEL_HOST})),
        InjectionRule(host=MODEL_HOST, header="x-api-key", sentinel="s", real="REAL-KEY"),
    )
    cert, key = await generate_ca()
    proxy = EgressProxy(
        resolve=_fixed(rules).resolve,
        authorize=refused,
        ca_cert=cert,
        ca_key=key,
        run_tokens=RUN_TOKENS,
    )
    endpoint = await proxy.start(bind_host="127.0.0.1")
    loop = asyncio.get_running_loop()
    unhandled: list[dict] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        run = RunToken(workspace_id=uuid4(), turn_id=uuid4())
        token = RUN_TOKENS.encode(run)
        with caplog.at_level(logging.ERROR, logger="ufo"):
            assert await _connect_reason(endpoint.port, MODEL_HOST, token) == (
                503,
                b"egress authorization unavailable",
            )
        await asyncio.sleep(0.1)
        assert unhandled == []
        (failed,) = [record for record in caplog.records if record.name == "ufo"]
        assert failed.message == "egress.authorize_failed"
        assert failed.levelno == logging.ERROR
        assert failed.ufo == {
            "workspace_id": str(run.workspace_id),
            "turn": str(run.turn_id),
            "error_class": "ConnectionRefusedError",
        }
    finally:
        loop.set_exception_handler(previous)
        await proxy.stop()


async def test_keyed_host_connect_denied_without_a_live_turn(db: None) -> None:
    """The turn-liveness gate: a keyed host (one carrying an InjectionRule) is MITM'd — hence its
    real key injected — only while the run token names a running turn. A tokenless CONNECT, a token
    for a turn that has ended, and a token for a turn that never existed are each refused at 403 and
    never reach `_mitm`, so the real key never leaves the proxy."""
    async with workspace_tx() as connection:
        workspace_id, running_turn, *_ = await _seed_turn(connection)
        _, ended_turn, *_ = await _seed_turn(connection, status="done")
    base = (
        ScopeRule(allowed_hosts=frozenset({MODEL_HOST})),
        InjectionRule(host=MODEL_HOST, header="x-api-key", sentinel="s", real="REAL-KEY"),
        MeterRule(host=MODEL_HOST, dimension="tokens"),
    )
    cert, key = await generate_ca()
    proxy = _egress(_fixed(base), ca_cert=cert, ca_key=key)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    try:
        assert await _connect(endpoint.port, MODEL_HOST) == 403
        ended = RUN_TOKENS.encode(RunToken(workspace_id, ended_turn))
        assert await _connect(endpoint.port, MODEL_HOST, ended) == 403
        unknown = RUN_TOKENS.encode(RunToken(workspace_id, uuid4()))
        assert await _connect(endpoint.port, MODEL_HOST, unknown) == 403
        assert await proxy.authorize(RunToken(workspace_id, running_turn)) is True
        assert await proxy.authorize(RunToken(workspace_id, ended_turn)) is False
    finally:
        await proxy.stop()


async def test_exact_scope_tunnel_requires_a_live_turn() -> None:
    resolutions = 0

    async def ended(_run: RunToken) -> bool:
        return False

    rules = (ScopeRule(allowed_hosts=frozenset({MODEL_HOST})),)

    async def resolve(_run: RunToken | None) -> tuple:
        nonlocal resolutions
        resolutions += 1
        return rules

    cert, key = await generate_ca()
    proxy = EgressProxy(
        resolve=resolve,
        authorize=ended,
        ca_cert=cert,
        ca_key=key,
        run_tokens=RUN_TOKENS,
    )
    endpoint = await proxy.start(bind_host="127.0.0.1")
    try:
        token = RUN_TOKENS.encode(RunToken(uuid4(), uuid4()))
        assert await _connect(endpoint.port, MODEL_HOST, token) == 403
    finally:
        await proxy.stop()
    assert resolutions == 0


async def test_forged_run_token_is_rejected_before_rule_resolution() -> None:
    calls = 0

    async def resolve(_run: RunToken | None) -> tuple:
        nonlocal calls
        calls += 1
        return (ScopeRule(allowed_hosts=frozenset({MODEL_HOST})),)

    cert, key = await generate_ca()
    proxy = EgressProxy(
        resolve=resolve,
        authorize=_fixed().turn_live,
        ca_cert=cert,
        ca_key=key,
        run_tokens=RUN_TOKENS,
    )
    endpoint = await proxy.start(bind_host="127.0.0.1")
    forged = RunTokenCodec(b"attacker").encode(RunToken(uuid4(), uuid4()))
    try:
        assert await _connect(endpoint.port, MODEL_HOST, forged) == 403
    finally:
        await proxy.stop()
    assert calls == 0


async def test_public_internet_rejects_private_addresses_and_ended_turns(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, running_turn, *_ = await _seed_turn(connection)
        ended_workspace, ended_turn, *_ = await _seed_turn(connection, status="done")
    cert, key = await generate_ca()
    resolver = PerAgentRules(base=(), grants=None, internet=(InternetRule(),))
    proxy = _egress(resolver, ca_cert=cert, ca_key=key)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    try:
        running = RUN_TOKENS.encode(RunToken(workspace_id, running_turn))
        ended = RUN_TOKENS.encode(RunToken(ended_workspace, ended_turn))
        assert await resolver.resolve(RunToken(workspace_id, running_turn)) == (InternetRule(),)
        assert await resolver.resolve(RunToken(workspace_id, uuid4())) == ()
        assert await _connect(endpoint.port, "169.254.169.254", running) == 403
        assert await _connect(endpoint.port, "8.8.8.8", ended) == 403
        assert await _connect(endpoint.port, "example.com", target_port="abc") == 400
        assert await _connect(endpoint.port, "example.com", running, "abc") == 400
        assert await _connect(endpoint.port, "example.com", running, 65536) == 400
        assert await _connect(endpoint.port, "a..b", running) == 403
        assert await _connect(endpoint.port, r"a\999z.com", running) == 403
    finally:
        await proxy.stop()


async def test_agent_internet_policy_is_cached_for_the_turn(db: None) -> None:
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    resolver = PerAgentRules(base=(), grants=None, internet=(InternetRule(),))
    proxy = _egress(resolver)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    first_run = RunToken(seeded.workspace_id, seeded.turn_id)
    try:
        assert await proxy._rules_for(first_run) == (InternetRule(),)

        second_turn = uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.agent)
                .values(internet_access_allowed=False)
                .where(tables.agent.c.id == seeded.agent_id)
            )
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=second_turn,
                    workspace_id=seeded.workspace_id,
                    conversation_id=seeded.conversation_id,
                    agent_id=seeded.agent_id,
                    seq=2,
                    status="running",
                    inbound="again",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )

        second_run = RunToken(seeded.workspace_id, second_turn)
        assert await proxy._rules_for(first_run) == (InternetRule(),)
        assert await proxy._rules_for(second_run) == ()
        assert await _connect(endpoint.port, "8.8.8.8", RUN_TOKENS.encode(second_run)) == 403
    finally:
        await proxy.stop()


async def test_public_internet_tunnels_and_meters_a_live_turn(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)

    async def local_public(_host: str, _port: int) -> str:
        return "127.0.0.1"

    stub = await asyncio.start_server(lambda _reader, writer: writer.close(), "127.0.0.1", 0)
    stub_port = stub.sockets[0].getsockname()[1]
    cert, key = await generate_ca()
    resolver = PerAgentRules(base=(), grants=None, internet=(InternetRule(),))
    proxy = EgressProxy(
        resolve=resolver.resolve,
        authorize=resolver.turn_live,
        ca_cert=cert,
        ca_key=key,
        run_tokens=RUN_TOKENS,
        resolve_public=local_public,
    )
    endpoint = await proxy.start(bind_host="127.0.0.1")
    token = RUN_TOKENS.encode(RunToken(workspace_id, turn_id))
    try:
        assert await _connect(endpoint.port, SEARCH_HOST, token, stub_port) == 200
    finally:
        await proxy.stop()
        stub.close()
        await stub.wait_closed()
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.ledger.c.dimension, tables.ledger.c.amount).where(
                    tables.ledger.c.turn_id == turn_id
                )
            )
        ).one()
    assert (row.dimension, int(row.amount)) == ("egress", 1)


async def _git_ls_remote(
    proxy_port: int, run_token: str, host: str, port: int, git_config: Mapping[str, str]
) -> str:
    """Drive the real `git` binary at `host:port` through the proxy, with the run token in the proxy
    URL exactly as a carrier threads it, and answer what git reported."""
    process = await asyncio.create_subprocess_exec(
        "git",
        "ls-remote",
        f"https://{host}:{port}/owner/repo.git",
        env={
            "PATH": os.environ.get("PATH", ""),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "https_proxy": f"http://{run_token}:@127.0.0.1:{proxy_port}",
            **git_config,
        },
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
    return stderr.decode(errors="replace")


async def test_real_git_reaches_the_public_internet_only_with_the_proxy_auth_config(
    db: None,
) -> None:
    """The whole chain a public `git clone` rides, with nothing hand-built: a manifest declaring
    `sandbox_internet` derives the `InternetRule` that admits any globally routable host — no
    per-host ScopeRule exists or is needed — and the turn's `GIT_PROXY_AUTH_CONFIG` is what lets git
    present the run token that reaches that rule at all.

    git's default `http.proxyAuthMethod=anyauth` waits for a `407` challenge the proxy never sends,
    so an unconfigured CONNECT arrives unattributed and is rejected before rule resolution.
    Configured, the tunnel opens and meters."""
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)

    async def local_public(_host: str, _port: int) -> str:
        return "127.0.0.1"

    upstream_connections = 0

    async def upstream(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal upstream_connections
        upstream_connections += 1
        writer.close()

    stub = await asyncio.start_server(upstream, "127.0.0.1", 0)
    stub_port = stub.sockets[0].getsockname()[1]
    cert, key = await generate_ca()
    internet = derive_manifest_rules((Manifest(name="repl", version="1", sandbox_internet=True),))
    assert internet == (InternetRule(),)
    resolver = PerAgentRules(base=(), grants=None, internet=internet)
    proxy = EgressProxy(
        resolve=resolver.resolve,
        authorize=resolver.turn_live,
        ca_cert=cert,
        ca_key=key,
        run_tokens=RUN_TOKENS,
        resolve_public=local_public,
    )
    endpoint = await proxy.start(bind_host="127.0.0.1")
    token = RUN_TOKENS.encode(RunToken(seeded.workspace_id, seeded.turn_id))
    try:
        assert not [rule for rule in await resolver.resolve(None) if isinstance(rule, ScopeRule)]

        unconfigured = await _git_ls_remote(endpoint.port, token, "git.test", stub_port, {})
        assert re.search(r"\b403\b", unconfigured)
        assert upstream_connections == 0

        configured = await _git_ls_remote(
            endpoint.port, token, "git.test", stub_port, _git_config_env(GIT_PROXY_AUTH_CONFIG)
        )
        assert not re.search(r"\b403\b", configured)
        assert upstream_connections == 1
    finally:
        await proxy.stop()
        stub.close()
        await stub.wait_closed()
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.ledger.c.dimension, tables.ledger.c.amount).where(
                    tables.ledger.c.turn_id == seeded.turn_id
                )
            )
        ).one()
    assert (row.dimension, int(row.amount)) == ("egress", 1)


async def test_public_internet_accepts_only_globally_routable_ipv4(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = _egress(_fixed())
    try:
        assert await proxy._resolve_public_address("8.8.8.8", 443) == "8.8.8.8"
        blocked = (
            "127.0.0.1",
            "10.0.0.1",
            "169.254.169.254",
            "100.64.0.1",
            "224.0.0.1",
            "64:ff9b::a9fe:a9fe",
            "::7f00:1",
        )
        for host in blocked:
            with pytest.raises(PermissionError):
                await proxy._resolve_public_address(host, 443)

        async def resolve(host: object, *_args: object, **_kwargs: object) -> tuple[str, ...]:
            text = str(host).rstrip(".")
            addresses = {
                "localhost": ("127.0.0.1",),
                "mixed.test": ("8.8.8.8", "127.0.0.1"),
            }.get(text, ("8.8.8.8",))
            return addresses

        monkeypatch.setattr(dns.asyncresolver, "resolve", resolve)
        with pytest.raises(PermissionError):
            await proxy._resolve_public_address("localhost", 443)
        with pytest.raises(PermissionError):
            await proxy._resolve_public_address("mixed.test", 443)
        assert await proxy._resolve_public_address("example.com", 443) == "8.8.8.8"
    finally:
        await proxy.stop()


async def test_tunnel_meters_a_granted_host(db: None) -> None:
    """A granted host injects nothing (its token is held server-side), so it is tunnelled opaquely —
    yet reaching it must still be metered. A real CONNECT to a MeterRule host writes one `egress`
    ledger row keyed to the turn, metered at CONNECT granularity since the opaque tunnel hides the
    individual requests inside it."""
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)

    async def upstream(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    stub = await asyncio.start_server(upstream, "127.0.0.1", 0)
    granted_host = "127.0.0.1"
    stub_port = stub.sockets[0].getsockname()[1]
    rules = (
        ScopeRule(allowed_hosts=frozenset({granted_host})),
        MeterRule(host=granted_host, dimension="requests"),
    )
    cert, key = await generate_ca()
    proxy = _egress(_fixed(rules), ca_cert=cert, ca_key=key)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    run_token = RUN_TOKENS.encode(RunToken(workspace_id, turn_id))
    try:
        assert await _connect(endpoint.port, granted_host, run_token, stub_port) == 200
    finally:
        await proxy.stop()
        stub.close()
        await stub.wait_closed()
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.ledger.c.dimension, tables.ledger.c.amount).where(
                    tables.ledger.c.turn_id == turn_id
                )
            )
        ).one()
    assert (row.dimension, int(row.amount)) == ("egress", 1)


def _candidates() -> list[InjectionRule]:
    return [
        InjectionRule(host="h", header="authorization", sentinel="Bearer S1", real="Bearer R1"),
        InjectionRule(host="h", header="authorization", sentinel="Bearer S2", real="Bearer R2"),
    ]


def test_inject_swaps_only_the_matching_sentinel_and_forces_close() -> None:
    out = _inject([b"authorization: Bearer S2\r\n", b"connection: keep-alive\r\n"], _candidates())
    assert b"authorization: Bearer R2\r\n" in out
    assert b"R1" not in out
    assert b"keep-alive" not in out
    assert out.endswith(b"connection: close\r\n")


def test_inject_swaps_every_key_a_provider_takes_on_one_request() -> None:
    """A provider that authenticates with two keys is two InjectionRules on one host, and one
    request carries both: each header is swapped from its own sentinel, so `DD-API-KEY` and
    `DD-APPLICATION-KEY` reach Datadog together while the sandbox held neither."""
    out = _inject(
        [b"DD-API-KEY: SENTINEL_DD_API\r\n", b"DD-APPLICATION-KEY: SENTINEL_DD_APP\r\n"],
        [
            InjectionRule(
                host="api.us5.datadoghq.com",
                header="DD-API-KEY",
                sentinel="SENTINEL_DD_API",
                real="dd-api-real",
            ),
            InjectionRule(
                host="api.us5.datadoghq.com",
                header="DD-APPLICATION-KEY",
                sentinel="SENTINEL_DD_APP",
                real="dd-app-real",
            ),
        ],
    )
    assert b"DD-API-KEY: dd-api-real\r\n" in out
    assert b"DD-APPLICATION-KEY: dd-app-real\r\n" in out
    assert b"SENTINEL" not in out


DATADOG_SITES = HostChoice(
    slot="datadog_api_host",
    description="Datadog site for this org.",
    hosts=("api.datadoghq.com", "api.us5.datadoghq.com"),
    default="api.datadoghq.com",
    env="DD_HOST",
)


async def test_resolve_injects_the_run_tokens_own_workspace_secret(db: None) -> None:
    """The keyed-provider path end to end at the resolver: one shared proxy, two workspaces, one
    declaration. Each turn's rules carry that workspace's own stored secret on the host its own
    companion slot pins, and a workspace that stored nothing gets no keyed egress at all — so
    nothing about workspace A is reachable from a run token for workspace B."""
    async with workspace_tx() as connection:
        first_workspace, first_turn, *_ = await _seed_turn(connection)
        second_workspace, second_turn, *_ = await _seed_turn(connection)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(first_workspace, "datadog_api_key", "first-real-key")
    await store.put(first_workspace, "datadog_api_host", "api.us5.datadoghq.com")
    slot = CredentialSlot(
        name="datadog_api_key",
        description="api key",
        injection=InjectionTarget(
            host=DATADOG_SITES,
            header="DD-API-KEY",
            sentinel="SENTINEL_DD_API",
            env="DD_API_KEY",
            dimension=REQUEST_METER_DIMENSION,
        ),
    )
    resolver = PerAgentRules(base=(), grants=None, credentials=store, slots=(slot,))
    keyed = await resolver.resolve(RunToken(first_workspace, first_turn))
    assert (
        InjectionRule(
            host="api.us5.datadoghq.com",
            header="DD-API-KEY",
            sentinel="SENTINEL_DD_API",
            real="first-real-key",
        )
        in keyed
    )
    assert ScopeRule(allowed_hosts=frozenset({"api.us5.datadoghq.com"})) in keyed
    assert MeterRule(host="api.us5.datadoghq.com", dimension=REQUEST_METER_DIMENSION) in keyed
    unkeyed = await resolver.resolve(RunToken(second_workspace, second_turn))
    assert not [rule for rule in unkeyed if isinstance(rule, InjectionRule)]


def test_inject_passes_a_foreign_sentinel_upstream_untouched() -> None:
    out = _inject([b"authorization: Bearer FOREIGN\r\n"], _candidates())
    assert b"authorization: Bearer FOREIGN\r\n" in out
    assert b"R1" not in out and b"R2" not in out


def test_sse_usage_parses_an_anthropic_stream() -> None:
    accumulator = HttpTokenUsage(MODEL_HOST)
    accumulator.feed(ANTHROPIC_SSE)
    assert accumulator.usage() == ("claude-opus-4-8", FULL_TOKEN_USAGE)


def test_sse_usage_reassembles_across_chunk_boundaries() -> None:
    accumulator = HttpTokenUsage(MODEL_HOST)
    for start in range(0, len(ANTHROPIC_SSE), 7):
        accumulator.feed(ANTHROPIC_SSE[start : start + 7])
    assert accumulator.usage() == ("claude-opus-4-8", FULL_TOKEN_USAGE)


def test_sse_usage_parses_an_openai_stream() -> None:
    accumulator = HttpTokenUsage(OPENAI_HOST)
    accumulator.feed(OPENAI_SSE)
    assert accumulator.usage() == (
        "gpt-5.4",
        Usage(input_tokens=1_000_000, output_tokens=1_000_000),
    )


def test_sse_usage_without_a_usage_event_is_none() -> None:
    accumulator = HttpTokenUsage(MODEL_HOST)
    accumulator.feed(
        SSE_RESPONSE_HEAD + b'event: content_block_delta\r\ndata: {"type":"content_block_delta",'
        b'"delta":{"text":"hi"}}\r\n\r\n'
    )
    assert accumulator.usage() is None


def test_json_body_usage_parses_an_anthropic_response() -> None:
    """A non-streaming Anthropic response is one JSON body with a top-level `usage` block, not
    `data:` SSE events; its usage is recovered so a single-JSON in-sandbox completion is metered."""
    accumulator = HttpTokenUsage(MODEL_HOST)
    accumulator.feed(ANTHROPIC_JSON_BODY)
    assert accumulator.usage() == ("claude-opus-4-8", FULL_TOKEN_USAGE)


def test_json_body_usage_parses_an_openai_response() -> None:
    accumulator = HttpTokenUsage(OPENAI_HOST)
    accumulator.feed(OPENAI_JSON_BODY)
    assert accumulator.usage() == (
        "gpt-5.4",
        Usage(input_tokens=1_000_000, output_tokens=1_000_000),
    )


def test_a_usage_object_the_parser_cannot_read_is_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A body carrying a usage object in neither OpenAI shape parses to nothing — and says so: a
    billable call that falls to zero leaves a trace rather than passing as a successful parse."""
    accumulator = HttpTokenUsage(OPENAI_HOST)
    with caplog.at_level(logging.INFO, logger="ufo"):
        accumulator.feed(OPENAI_UNREADABLE_USAGE_JSON_BODY)
        assert accumulator.usage() is None
    assert [record.message for record in caplog.records if record.name == "ufo"] == [
        "egress.tokens_usage_unparsed"
    ]


def test_cached_tokens_over_the_prompt_count_are_clamped_and_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A cached count above the prompt it is part of is an impossible split, but this parser reads
    along a relayed response the sandbox client already has: it clamps the cache-read share to the
    prompt — never a negative fresh-input count — and reports the reading, rather than failing a
    call that succeeded over a billing guard the way the host adapters do."""
    accumulator = HttpTokenUsage(OPENAI_HOST)
    with caplog.at_level(logging.INFO, logger="ufo"):
        accumulator.feed(OPENAI_OVER_CACHED_JSON_BODY)
        assert accumulator.usage() == (
            "gpt-5.5",
            Usage(input_tokens=0, output_tokens=500, cache_read_tokens=100_000),
        )
    assert [record.message for record in caplog.records if record.name == "ufo"] == [
        "egress.tokens_cached_over_prompt"
    ]


def test_json_body_usage_reassembles_across_chunk_boundaries() -> None:
    accumulator = HttpTokenUsage(MODEL_HOST)
    for start in range(0, len(ANTHROPIC_JSON_BODY), 7):
        accumulator.feed(ANTHROPIC_JSON_BODY[start : start + 7])
    assert accumulator.usage() == ("claude-opus-4-8", FULL_TOKEN_USAGE)


def test_sse_usage_decodes_chunked_http_framing() -> None:
    framed = bytearray(
        b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ntransfer-encoding: chunked\r\n\r\n"
    )
    for start in range(0, len(ANTHROPIC_SSE_BODY), 11):
        chunk = ANTHROPIC_SSE_BODY[start : start + 11]
        framed.extend(f"{len(chunk):x}\r\n".encode())
        framed.extend(chunk)
        framed.extend(b"\r\n")
    framed.extend(b"0\r\n\r\n")
    accumulator = HttpTokenUsage(MODEL_HOST)
    for start in range(0, len(framed), 7):
        accumulator.feed(framed[start : start + 7])
    assert accumulator.usage() == ("claude-opus-4-8", FULL_TOKEN_USAGE)


def test_sse_usage_decodes_gzipped_http_body() -> None:
    compressed = gzip.compress(OPENAI_SSE_BODY)
    response = (
        b"HTTP/1.1 200 OK\r\n"
        b"content-type: text/event-stream\r\n"
        b"content-encoding: gzip\r\n"
        b"content-length: " + str(len(compressed)).encode() + b"\r\n\r\n" + compressed
    )
    accumulator = HttpTokenUsage(OPENAI_HOST)
    for start in range(0, len(response), 7):
        accumulator.feed(response[start : start + 7])
    assert accumulator.usage() == (
        "gpt-5.4",
        Usage(input_tokens=1_000_000, output_tokens=1_000_000),
    )


def test_compressed_usage_overflow_is_bounded_and_refused() -> None:
    compressed = gzip.compress(b"x" * (proxy_server.MAX_SSE_BUFFER_BYTES + 1))
    response = b"HTTP/1.1 200 OK\r\ncontent-encoding: gzip\r\n\r\n" + compressed
    accumulator = HttpTokenUsage(OPENAI_HOST)
    accumulator.feed(response)
    assert accumulator.usage() is None
    assert accumulator._overflowed


RELAY_EXCHANGE_TIMEOUT_SECONDS = 10
RELAY_IDLE_TEST_TIMEOUT_SECONDS = 0.2


async def _stream_pair() -> tuple[
    tuple[asyncio.StreamReader, asyncio.StreamWriter],
    tuple[asyncio.StreamReader, asyncio.StreamWriter],
]:
    left, right = socket.socketpair()
    left.setblocking(False)
    right.setblocking(False)
    return await asyncio.open_connection(sock=left), await asyncio.open_connection(sock=right)


async def test_relay_tees_the_full_body_to_the_client_while_metering_usage() -> None:
    """The model host's response streams to the client byte-for-byte unchanged AND is teed to the
    accumulator — the meter reads the stream without holding the client's bytes back."""
    (proxy_client_r, proxy_client_w), (peer_client_r, peer_client_w) = await _stream_pair()
    (proxy_up_r, proxy_up_w), (_peer_up_r, peer_up_w) = await _stream_pair()
    accumulator = HttpTokenUsage(MODEL_HOST)
    relay = asyncio.create_task(
        _relay(proxy_client_r, proxy_client_w, proxy_up_r, proxy_up_w, accumulator.feed)
    )
    peer_up_w.write(ANTHROPIC_SSE)
    await peer_up_w.drain()
    peer_up_w.close()
    received = await peer_client_r.readexactly(len(ANTHROPIC_SSE))
    await relay
    assert received == ANTHROPIC_SSE
    assert accumulator.usage() == ("claude-opus-4-8", FULL_TOKEN_USAGE)
    proxy_client_w.close()
    peer_client_w.close()


async def test_relay_keeps_streaming_after_the_client_half_closes() -> None:
    request = b"git-upload-pack request"
    response = (
        b"HTTP/1.1 200 OK\r\n"
        b"content-type: application/x-git-upload-pack-result\r\n"
        b"transfer-encoding: chunked\r\n"
        b"connection: close\r\n\r\n"
        b"9\r\npack-data\r\n"
        b"0\r\n\r\n"
    )
    (proxy_client_r, proxy_client_w), (peer_client_r, peer_client_w) = await _stream_pair()
    (proxy_up_r, proxy_up_w), (peer_up_r, peer_up_w) = await _stream_pair()
    relay = asyncio.create_task(_relay(proxy_client_r, proxy_client_w, proxy_up_r, proxy_up_w))
    try:
        async with asyncio.timeout(RELAY_EXCHANGE_TIMEOUT_SECONDS):
            peer_client_w.write(request)
            await peer_client_w.drain()
            peer_client_w.write_eof()
            assert await peer_up_r.read() == request
            peer_up_w.write(response)
            await peer_up_w.drain()
            peer_up_w.write_eof()
            assert await peer_client_r.readexactly(len(response)) == response
            await relay
    finally:
        relay.cancel()
        await asyncio.gather(relay, return_exceptions=True)
        for writer in (proxy_client_w, peer_client_w, peer_up_w):
            writer.close()


async def test_relay_keeps_streaming_when_tls_upstream_cannot_half_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = b"git-upload-pack request"
    response = b"pack-data"
    (proxy_client_r, proxy_client_w), (peer_client_r, peer_client_w) = await _stream_pair()
    (proxy_up_r, proxy_up_w), (peer_up_r, peer_up_w) = await _stream_pair()
    request_finished = asyncio.Event()

    def cannot_write_eof() -> bool:
        request_finished.set()
        return False

    monkeypatch.setattr(proxy_up_w, "can_write_eof", cannot_write_eof)
    relay = asyncio.create_task(_relay(proxy_client_r, proxy_client_w, proxy_up_r, proxy_up_w))
    try:
        async with asyncio.timeout(RELAY_EXCHANGE_TIMEOUT_SECONDS):
            peer_client_w.write(request)
            await peer_client_w.drain()
            peer_client_w.write_eof()
            assert await peer_up_r.readexactly(len(request)) == request
            await request_finished.wait()
            peer_up_w.write(response)
            await peer_up_w.drain()
            peer_up_w.write_eof()
            assert await peer_client_r.readexactly(len(response)) == response
            await relay
    finally:
        relay.cancel()
        await asyncio.gather(relay, return_exceptions=True)
        for writer in (proxy_client_w, peer_client_w, peer_up_w):
            writer.close()


async def test_relay_closes_a_silent_upstream_after_client_half_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proxy_server, "RELAY_RESPONSE_IDLE_TIMEOUT_SECONDS", 0.01)
    (proxy_client_r, proxy_client_w), (_peer_client_r, peer_client_w) = await _stream_pair()
    (proxy_up_r, proxy_up_w), (peer_up_r, peer_up_w) = await _stream_pair()
    relay = asyncio.create_task(_relay(proxy_client_r, proxy_client_w, proxy_up_r, proxy_up_w))
    try:
        peer_client_w.write_eof()
        assert await peer_up_r.read() == b""
        completed, _ = await asyncio.wait({relay}, timeout=RELAY_IDLE_TEST_TIMEOUT_SECONDS)
        assert relay in completed
        await relay
        assert proxy_up_w.is_closing()
    finally:
        relay.cancel()
        await asyncio.gather(relay, return_exceptions=True)
        for writer in (proxy_client_w, peer_client_w, peer_up_w):
            writer.close()


async def test_relay_cancellation_tears_down_pumps_and_upstream() -> None:
    """stop()'s drain cancels the outer connection task at `_relay`'s await; `asyncio.wait` does
    not cascade to the pump tasks it was waiting on, so the teardown must run on the way out —
    or a live tunnel's pumps and upstream socket outlive the drained signal."""
    (proxy_client_r, proxy_client_w), (_peer_client_r, peer_client_w) = await _stream_pair()
    (proxy_up_r, proxy_up_w), (_peer_up_r, peer_up_w) = await _stream_pair()
    relay = asyncio.create_task(_relay(proxy_client_r, proxy_client_w, proxy_up_r, proxy_up_w))
    await asyncio.sleep(0)
    relay.cancel()
    with pytest.raises(asyncio.CancelledError):
        await relay
    assert proxy_up_w.is_closing()
    for writer in (proxy_client_w, peer_client_w, peer_up_w):
        writer.close()


async def test_model_host_relay_meters_sandbox_tokens_to_the_turn(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)
    proxy = _egress(_fixed())
    accumulator = HttpTokenUsage(MODEL_HOST)
    accumulator.feed(ANTHROPIC_SSE)
    await proxy._meter_tokens(RunToken(workspace_id, turn_id), accumulator)
    assert proxy._meter_worker is not None
    await proxy.stop()
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.dimension,
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                    tables.ledger.c.model,
                ).where(tables.ledger.c.turn_id == turn_id)
            )
        ).one()
    assert (row.dimension, int(row.amount), int(row.priced_micro_usd), row.model) == (
        "sandbox_tokens",
        10_000,
        96_500,
        "claude-opus-4-8",
    )


async def test_meter_batch_sums_token_usage_for_the_same_run_and_model(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)
    proxy = _egress(_fixed())
    run = RunToken(workspace_id, turn_id)
    first = HttpTokenUsage(MODEL_HOST)
    first.feed(ANTHROPIC_SSE)
    second = HttpTokenUsage(MODEL_HOST)
    second.feed(ANTHROPIC_SSE)
    await proxy._meter_tokens(run, first)
    await proxy._meter_tokens(run, second)
    await proxy.stop()
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                    tables.ledger.c.model,
                ).where(tables.ledger.c.turn_id == turn_id)
            )
        ).one()
    assert (int(row.amount), int(row.priced_micro_usd), row.model) == (
        20_000,
        193_000,
        "claude-opus-4-8",
    )


async def test_model_host_relay_meters_a_non_streaming_json_body(db: None) -> None:
    """A non-streaming single-JSON completion is metered through the same path as an SSE stream: the
    teed body's top-level usage is parsed and written under `sandbox_tokens`, so it is not free."""
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)
    proxy = _egress(_fixed())
    accumulator = HttpTokenUsage(MODEL_HOST)
    accumulator.feed(ANTHROPIC_JSON_BODY)
    await proxy._meter_tokens(RunToken(workspace_id, turn_id), accumulator)
    assert proxy._meter_worker is not None
    await proxy.stop()
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.dimension,
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                    tables.ledger.c.model,
                ).where(tables.ledger.c.turn_id == turn_id)
            )
        ).one()
    assert (row.dimension, int(row.amount), int(row.priced_micro_usd), row.model) == (
        "sandbox_tokens",
        10_000,
        96_500,
        "claude-opus-4-8",
    )


async def _sandbox_token_row(turn_id: UUID) -> sa.Row:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(
                    tables.ledger.c.dimension,
                    tables.ledger.c.amount,
                    tables.ledger.c.prompt_tokens,
                    tables.ledger.c.cache_read_tokens,
                    tables.ledger.c.priced_micro_usd,
                    tables.ledger.c.model,
                ).where(tables.ledger.c.turn_id == turn_id)
            )
        ).one()


async def test_openai_cached_prompt_tokens_are_metered_at_the_cache_read_rate(db: None) -> None:
    """A 100,000-token prompt of which 90,000 were served from cache costs 110,000 micro-USD on
    `gpt-5.5`, not the 515,000 the whole prompt would cost at the fresh input rate: the cached share
    is split out of the input count and priced as a cache read. The row carries that split, so the
    dimension's cache share is a read of the same row its tokens and cost come from."""
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)
    proxy = _egress(_fixed())
    accumulator = HttpTokenUsage(OPENAI_HOST)
    accumulator.feed(OPENAI_CACHED_JSON_BODY)
    await proxy._meter_tokens(RunToken(workspace_id, turn_id), accumulator)
    await proxy.stop()
    row = await _sandbox_token_row(turn_id)
    assert (
        row.dimension,
        int(row.amount),
        int(row.prompt_tokens),
        int(row.cache_read_tokens),
        int(row.priced_micro_usd),
        row.model,
    ) == ("sandbox_tokens", 100_500, 100_000, 90_000, 110_000, "gpt-5.5")


async def test_a_responses_shaped_body_is_metered_rather_than_billed_nothing(db: None) -> None:
    """An in-sandbox call to the Responses surface reports input/output-named usage; it is metered
    under `sandbox_tokens` with the same cached split, where matching only the Chat Completions
    names parsed every count as zero and wrote no row at all."""
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)
    proxy = _egress(_fixed())
    accumulator = HttpTokenUsage(OPENAI_HOST)
    accumulator.feed(OPENAI_RESPONSES_JSON_BODY)
    await proxy._meter_tokens(RunToken(workspace_id, turn_id), accumulator)
    await proxy.stop()
    row = await _sandbox_token_row(turn_id)
    assert (
        row.dimension,
        int(row.amount),
        int(row.prompt_tokens),
        int(row.cache_read_tokens),
        int(row.priced_micro_usd),
        row.model,
    ) == ("sandbox_tokens", 100_500, 100_000, 90_000, 44_000, "gpt-5.6-terra")


async def test_model_host_relay_skips_when_no_usage_is_reported(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)
    proxy = _egress(_fixed())
    accumulator = HttpTokenUsage(MODEL_HOST)
    accumulator.feed(
        SSE_RESPONSE_HEAD + b'data: {"type":"content_block_delta","delta":{"text":"hi"}}\n\n'
    )
    await proxy._meter_tokens(RunToken(workspace_id, turn_id), accumulator)
    assert proxy._meter_worker is None
    await proxy.stop()
    async with workspace_tx() as connection:
        count = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.ledger)
                .where(tables.ledger.c.turn_id == turn_id)
            )
        ).scalar_one()
    assert count == 0


FORWARD_HOST = "api.hub.test"
REFUSAL_EXCHANGE_TIMEOUT_SECONDS = 10
OVER_CAP_BODY_BYTES = 4 * MAX_FORWARD_BODY_BYTES


@dataclass
class _RecordingForwarder:
    """A real RequestForwarder standing in for the broker dependency: it records the one call the
    proxy makes and answers a canned provider response, so the assertion reads what crossed the
    seam — never a mock's own bookkeeping."""

    calls: list[tuple[str, str, str, dict[str, str], bytes]] = field(default_factory=list)

    async def forward(
        self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes
    ) -> ForwardedResponse:
        self.calls.append((account_id, method, url, dict(headers), body))
        return ForwardedResponse(
            status=201,
            headers={"content-type": "application/json", "x-hub": "yes"},
            body=b'{"login":"me"}',
        )


def _forward_rule(forwarder: _RecordingForwarder, account: str = "acct-1") -> ForwardRule:
    return ForwardRule(
        host=FORWARD_HOST,
        header="authorization",
        sentinel=grant_sentinel(account),
        account_id=account,
        forward=forwarder,
    )


async def test_a_sentinel_cli_request_forwards_through_the_broker(db: None) -> None:
    """The wire analog of a connector tool call, end to end over real sockets: the sandbox CONNECTs
    with its live turn's token, the proxy MITMs the granted host, and the request whose auth header
    carries the grant sentinel is executed through the broker under the granted account — the
    provider response streams back, the auth header never leaves the proxy, and the request is
    metered to the turn."""
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)
    forwarder = _RecordingForwarder()
    sentinel = grant_sentinel("acct-1")
    rules = (
        ScopeRule(allowed_hosts=frozenset({FORWARD_HOST})),
        MeterRule(host=FORWARD_HOST, dimension="requests"),
        _forward_rule(forwarder),
    )
    cert, key = await generate_ca()
    proxy = _egress(_fixed(rules), ca_cert=cert, ca_key=key)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    token = RUN_TOKENS.encode(RunToken(workspace_id, turn_id))
    body = b'{"title":"hi"}'
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", endpoint.port)
        writer.write(
            f"CONNECT {FORWARD_HOST}:443 HTTP/1.1\r\n"
            f"Proxy-Authorization: {_basic(token)}\r\n\r\n".encode()
        )
        await writer.drain()
        assert b"200" in await reader.readline()
        while (await reader.readline()) not in (b"\r\n", b""):
            pass
        context = ssl.create_default_context(cadata=cert)
        await writer.start_tls(context, server_hostname=FORWARD_HOST)
        writer.write(
            b"POST /repos/o/r/issues HTTP/1.1\r\n"
            b"host: " + FORWARD_HOST.encode() + b"\r\n"
            b"authorization: token " + sentinel.encode() + b"\r\n"
            b"content-type: application/json\r\n"
            b"content-length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        )
        await writer.drain()
        response = await reader.read()
        writer.close()
    finally:
        await proxy.stop()
    assert response.startswith(b"HTTP/1.1 201")
    assert b'{"login":"me"}' in response
    assert b"x-hub: yes" in response
    account, method, url, headers, sent_body = forwarder.calls[0]
    assert account == "acct-1"
    assert method == "POST"
    assert url == f"https://{FORWARD_HOST}/repos/o/r/issues"
    assert sent_body == body
    assert "authorization" not in {name.lower() for name in headers}
    assert headers.get("content-type") == "application/json"
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.ledger.c.dimension, tables.ledger.c.amount).where(
                    tables.ledger.c.turn_id == turn_id
                )
            )
        ).one()
    assert (row.dimension, int(row.amount)) == ("egress", 1)


async def test_a_forward_host_connect_requires_a_live_turn(db: None) -> None:
    """A ForwardRule draws a real credential broker-side, so its host gates on turn liveness exactly
    as a keyed host does: a token for an ended turn is refused at CONNECT."""
    async with workspace_tx() as connection:
        workspace_id, ended_turn, *_ = await _seed_turn(connection, status="done")
    rules = (
        ScopeRule(allowed_hosts=frozenset({FORWARD_HOST})),
        _forward_rule(_RecordingForwarder()),
    )
    cert, key = await generate_ca()
    proxy = _egress(_fixed(rules), ca_cert=cert, ca_key=key)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    try:
        ended = RUN_TOKENS.encode(RunToken(workspace_id, ended_turn))
        assert await _connect(endpoint.port, FORWARD_HOST, ended) == 403
    finally:
        await proxy.stop()


def test_forward_match_selects_by_exact_or_scheme_prefixed_sentinel() -> None:
    forwarder = _RecordingForwarder()
    rule = _forward_rule(forwarder)
    sentinel = rule.sentinel.encode()
    assert _forward_match([b"authorization: token " + sentinel + b"\r\n"], [rule]) is rule
    assert _forward_match([b"Authorization: " + sentinel + b"\r\n"], [rule]) is rule
    assert _forward_match([b"authorization: token other\r\n"], [rule]) is None
    assert _forward_match([b"x-api-key: " + sentinel + b"\r\n"], [rule]) is None
    assert _forward_match([b"authorization:\r\n"], [rule]) is None


async def test_an_over_cap_forward_body_answers_a_readable_413(db: None) -> None:
    """The refusal the sandbox never saw. The proxy decided an over-cap body before reading any of
    it, then answered and closed while the client was still uploading — so the client's own write
    died on a closed socket (`use of closed network connection`, or a truncated read the caller
    reports as `unexpected end of JSON input`) and the 413 never arrived. The size ceiling was
    reachable only by bisection. The client now completes its send and reads a framed 413 naming
    both the limit and what it sent, and the broker is never called.

    The body clears the cap several times over because a marginal one is swallowed whole by
    loopback socket buffers, which hides the stall this asserts. The bound covers teardown as well
    as the exchange: an undrained refusal does not only lose the message, it leaves the client
    writing into a socket the proxy has stopped reading, so the proxy's own TLS close cannot
    complete and asyncio parks that connection on its 30s SSL shutdown timeout — one wedged task per
    refusal in a pod shared by every workspace."""
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)
    forwarder = _RecordingForwarder()
    sentinel = grant_sentinel("acct-1")
    rules = (
        ScopeRule(allowed_hosts=frozenset({FORWARD_HOST})),
        _forward_rule(forwarder),
    )
    cert, key = await generate_ca()
    proxy = _egress(_fixed(rules), ca_cert=cert, ca_key=key)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    token = RUN_TOKENS.encode(RunToken(workspace_id, turn_id))
    body = b"x" * OVER_CAP_BODY_BYTES
    async with asyncio.timeout(REFUSAL_EXCHANGE_TIMEOUT_SECONDS):
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", endpoint.port)
            writer.write(
                f"CONNECT {FORWARD_HOST}:443 HTTP/1.1\r\n"
                f"Proxy-Authorization: {_basic(token)}\r\n\r\n".encode()
            )
            await writer.drain()
            assert b"200" in await reader.readline()
            while (await reader.readline()) not in (b"\r\n", b""):
                pass
            context = ssl.create_default_context(cadata=cert)
            await writer.start_tls(context, server_hostname=FORWARD_HOST)
            writer.write(
                b"POST /repos/o/r/issues HTTP/1.1\r\n"
                b"host: " + FORWARD_HOST.encode() + b"\r\n"
                b"authorization: token " + sentinel.encode() + b"\r\n"
                b"content-type: application/json\r\n"
                b"content-length: " + str(len(body)).encode() + b"\r\n\r\n" + body
            )
            await writer.drain()
            response = await reader.read()
            writer.close()
        finally:
            await proxy.stop()
    head, _, refusal = response.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 413 Request Entity Too Large")
    assert b"content-length: " + str(len(refusal)).encode() in head
    assert str(MAX_FORWARD_BODY_BYTES).encode() in refusal
    assert str(len(body)).encode() in refusal
    assert not forwarder.calls


async def test_a_mid_stream_chunked_forward_body_answers_a_readable_411(db: None) -> None:
    """A chunked client streams its body right after the headers without waiting for a response, so
    the refusal is decided while it is mid-write — closing under it would repeat the over-cap wedge
    for this cause: its send dies on a closed socket and the 411 is never read. The body is drained
    to the client's own close instead, so the send completes and the framed 411 naming chunked
    arrives. The chunks clear loopback socket buffers several times over, which would stall this
    send against an undrained refusal, and the broker is never called."""
    async with workspace_tx() as connection:
        workspace_id, turn_id, *_ = await _seed_turn(connection)
    forwarder = _RecordingForwarder()
    sentinel = grant_sentinel("acct-1")
    rules = (
        ScopeRule(allowed_hosts=frozenset({FORWARD_HOST})),
        _forward_rule(forwarder),
    )
    cert, key = await generate_ca()
    proxy = _egress(_fixed(rules), ca_cert=cert, ca_key=key)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    token = RUN_TOKENS.encode(RunToken(workspace_id, turn_id))
    chunk = b"y" * RELAY_CHUNK_BYTES
    async with asyncio.timeout(REFUSAL_EXCHANGE_TIMEOUT_SECONDS):
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", endpoint.port)
            writer.write(
                f"CONNECT {FORWARD_HOST}:443 HTTP/1.1\r\n"
                f"Proxy-Authorization: {_basic(token)}\r\n\r\n".encode()
            )
            await writer.drain()
            assert b"200" in await reader.readline()
            while (await reader.readline()) not in (b"\r\n", b""):
                pass
            context = ssl.create_default_context(cadata=cert)
            await writer.start_tls(context, server_hostname=FORWARD_HOST)
            writer.write(
                b"POST /repos/o/r/issues HTTP/1.1\r\n"
                b"host: " + FORWARD_HOST.encode() + b"\r\n"
                b"authorization: token " + sentinel.encode() + b"\r\n"
                b"content-type: application/json\r\n"
                b"transfer-encoding: chunked\r\n\r\n"
            )
            for _ in range(64):
                writer.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                await writer.drain()
            head = await reader.readuntil(b"\r\n\r\n")
            content_length = next(
                int(line.partition(b":")[2])
                for line in head.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            refusal = await reader.readexactly(content_length)
            writer.close()
        finally:
            await proxy.stop()
    assert head.startswith(b"HTTP/1.1 411 Length Required")
    assert b"chunked" in refusal
    assert not forwarder.calls


async def test_read_request_body_names_the_cause_of_each_refusal() -> None:
    """Four causes answered one None, so a size ceiling was indistinguishable from a chunked body
    and neither named its numbers. Each answers its own status now, and each reports the `pending`
    bytes to drain — all four are decided from the headers alone, before a body byte is read, so a
    client streaming right behind its headers is mid-upload whichever cause refused it: over-cap
    reports its declared remainder, the three whose length is undeclared or untrusted report the
    drain cap and only the client's own close ends their drain. A negative Content-Length is caught
    here rather than at `readexactly`, whose ValueError (not IncompleteReadError) would escape
    uncaught and drop the connection with no response."""
    reader = asyncio.StreamReader()
    refusals = {
        name: await _read_request_body(reader, [header])
        for name, header in (
            ("negative", b"content-length: -1\r\n"),
            ("unparseable", b"content-length: nope\r\n"),
            ("chunked", b"transfer-encoding: chunked\r\n"),
            ("oversized", f"content-length: {MAX_FORWARD_BODY_BYTES + 1}\r\n".encode()),
        )
    }
    assert all(isinstance(refusal, _Refusal) for refusal in refusals.values())
    assert [refusal.status for refusal in refusals.values()] == [
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.LENGTH_REQUIRED,
        HTTPStatus.LENGTH_REQUIRED,
        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
    ]
    assert [refusal.pending for refusal in refusals.values()] == [
        MAX_REFUSAL_DRAIN_BYTES,
        MAX_REFUSAL_DRAIN_BYTES,
        MAX_REFUSAL_DRAIN_BYTES,
        MAX_FORWARD_BODY_BYTES + 1,
    ]
    assert "negative" in refusals["negative"].message
    assert "unparseable" in refusals["unparseable"].message
    assert "chunked" in refusals["chunked"].message
    assert str(MAX_FORWARD_BODY_BYTES) in refusals["oversized"].message


async def test_read_request_body_names_a_truncated_body() -> None:
    """The fifth refusal: a client that declares more than it sends leaves `readexactly` short of
    its count. The message names how far the body actually got, so a client that died mid-upload is
    not read as one that hit the size ceiling."""
    reader = asyncio.StreamReader()
    reader.feed_data(b"x" * 10)
    reader.feed_eof()
    refusal = await _read_request_body(reader, [b"content-length: 20\r\n"])
    assert isinstance(refusal, _Refusal)
    assert refusal.status == HTTPStatus.BAD_REQUEST
    assert refusal.pending == 0
    assert "10 of 20" in refusal.message


async def test_drain_refused_body_consumes_the_pending_bytes() -> None:
    """The refused client is mid-upload, so its send only completes once these bytes are taken off
    the wire — until then it cannot get to the refusal already written to it."""
    reader = asyncio.StreamReader()
    reader.feed_data(b"y" * 4096)
    await _drain_refused_body(reader, 4096)
    reader.feed_eof()
    assert await reader.read() == b""


async def test_drain_refused_body_stops_at_its_bound() -> None:
    """Draining is a courtesy to the client, not an obligation to read whatever it declared: a body
    past the bound leaves the rest unread and the connection closes under it."""
    reader = asyncio.StreamReader()
    reader.feed_data(b"y" * (MAX_REFUSAL_DRAIN_BYTES + 4096))
    await _drain_refused_body(reader, MAX_REFUSAL_DRAIN_BYTES + 4096)
    reader.feed_eof()
    assert len(await reader.read()) == 4096


async def test_drain_refused_body_gives_up_on_a_stalled_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The drain is bounded in time as well as bytes: a peer that stops sending without closing —
    its refusal already on the wire — would otherwise park `read` forever, holding the connection
    task for as long as it holds the socket. Past the deadline the connection closes and the
    client's own write error stands."""
    monkeypatch.setattr(proxy_server, "REFUSAL_DRAIN_TIMEOUT_SECONDS", 0.05)
    reader = asyncio.StreamReader()
    reader.feed_data(b"y" * 100)
    async with asyncio.timeout(REFUSAL_EXCHANGE_TIMEOUT_SECONDS):
        await _drain_refused_body(reader, 4096)


async def test_drain_refused_body_ends_at_the_clients_close() -> None:
    """A chunked refusal declares no count, so its `pending` is the drain cap and the real
    terminator is the client's own close once it has read the refusal — EOF ends the drain, never
    a byte tally."""
    reader = asyncio.StreamReader()
    reader.feed_data(b"y" * 100)
    reader.feed_eof()
    async with asyncio.timeout(REFUSAL_EXCHANGE_TIMEOUT_SECONDS):
        await _drain_refused_body(reader, MAX_REFUSAL_DRAIN_BYTES)
    assert await reader.read() == b""


def test_forward_response_bytes_drops_headers_carrying_crlf() -> None:
    """The broker's response headers come straight from Composio's JSON envelope with no wire
    validation, so a value (or name) carrying an embedded CR/LF would split the response written
    back into the sandbox's TLS stream. Such a header is dropped, never emitted — the safe ones
    still pass, and the framing headers this proxy owns are always present."""
    response = ForwardedResponse(
        status=200,
        headers={
            "x-ok": "fine",
            "x-split": "v\r\nInjected: evil",
            "x-newline": "a\nb",
            "bad\r\nname": "x",
        },
        body=b"{}",
    )
    raw = _forward_response_bytes(response)
    assert b"x-ok: fine\r\n" in raw
    assert b"Injected: evil" not in raw
    assert b"x-newline" not in raw
    assert b"bad" not in raw
    assert b"content-length: 2\r\n" in raw
    assert raw.endswith(b"connection: close\r\n\r\n{}")


async def test_resolve_derives_forward_rules_for_the_acting_member(db: None) -> None:
    """The signed process claim selects private grants; the turn's speaker is only attribution."""
    async with workspace_tx() as connection:
        spoken = await _seed_turn(connection, speaker=True)
    store = GrantStore()
    with ws(spoken.workspace_id), agent(spoken.agent_id):
        await store.record(
            provider="hub",
            account_id="acct-1",
            host=FORWARD_HOST,
            grantor_member_id=spoken.member_id,
            conversation_id=spoken.conversation_id,
            shared=False,
        )
    cli = CliCredential(env="HUB_TOKEN", header="authorization", forward=_RecordingForwarder())
    resolver = PerAgentRules(base=(), grants=store, clis={"hub": cli})
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(speaker_member_id=None)
            .where(tables.turn.c.id == spoken.turn_id)
        )
    rules = await resolver.resolve(RunToken(spoken.workspace_id, spoken.turn_id, spoken.member_id))
    forward = next(rule for rule in rules if isinstance(rule, ForwardRule))
    assert forward.sentinel == grant_sentinel("acct-1")
    assert forward.account_id == "acct-1"
    silent = await resolver.resolve(RunToken(spoken.workspace_id, spoken.turn_id))
    assert not any(isinstance(rule, ForwardRule) for rule in silent)


async def test_proxy_turn_reads_bind_the_run_workspace(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    original = proxy_server.workspace_tx
    scopes: list[UUID] = []

    @asynccontextmanager
    async def scoped_tx() -> AsyncIterator[AsyncConnection]:
        scopes.append(ws_current().workspace_id)
        async with original() as connection:
            yield connection

    monkeypatch.setattr(proxy_server, "workspace_tx", scoped_tx)
    resolver = PerAgentRules(base=(), grants=None)
    run = RunToken(seeded.workspace_id, seeded.turn_id)

    await resolver.resolve(run)
    await resolver.turn_live(run)

    assert scopes == [seeded.workspace_id, seeded.workspace_id]


async def test_real_git_presents_the_credential_sentinel_to_the_credentialed_host(
    db: None,
) -> None:
    """The real `git` binary, given the turn's own config env, resolves the sentinel header for a
    URL on the credentialed host and nothing for any other host. That is the sandbox half of the
    swap: git sends `Authorization: <sentinel>`, the proxy's InjectionRule matches that exact value
    and rewrites it to the Basic credential upstream, so the secret never enters the container."""
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(seeded.workspace_id, "github_git_token", "ghp-real")
    slots = (
        CredentialSlot(
            name="github_git_token",
            description="git token",
            injection=InjectionTarget(
                host="github.com",
                header="Authorization",
                sentinel="SENTINEL_GIT",
                git_basic_user="x-access-token",
            ),
        ),
    )
    env = _git_config_env(
        (*GIT_PROXY_AUTH_CONFIG, *await _git_credential_config(store, slots, seeded.workspace_id))
    )

    async def urlmatch(url: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "git",
            "config",
            "--get-urlmatch",
            "http.extraheader",
            url,
            env={
                "PATH": os.environ.get("PATH", ""),
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
                **env,
            },
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=60)
        return stdout.decode().strip()

    assert await urlmatch("https://github.com/owner/private.git") == "Authorization: SENTINEL_GIT"
    assert await urlmatch("https://gitlab.test/owner/other.git") == ""


async def test_an_unusable_binding_exports_nothing_and_admits_nothing(db: None) -> None:
    """Both roles read one answer. A workspace whose source holds a binding this deploy cannot use
    has the member's own token stored too — the value that made the two roles disagree.

    The export must not configure git off that stored token, because the same slot's wire rules are
    withheld: git would then send a sentinel to a host the proxy admits nothing for, and the member
    would watch a clone fail against a credential the sandbox said it had. `slot_is_set` and
    `derive_credential_rules` are asserted in one test on purpose — they are two roles answering one
    question, and a fixture that proved only one would let them drift apart again."""
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(seeded.workspace_id, "github_git_token", "ghp-member-own")

    class _Unusable:
        async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None:
            raise ValueError("installation binding does not open")

        async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool:
            raise ValueError("installation binding does not open")

    slots = (
        CredentialSlot(
            name="github_git_token",
            description="git token",
            source=_Unusable(),
            injection=InjectionTarget(
                host="github.com",
                header="Authorization",
                sentinel="SENTINEL_GIT",
                git_basic_user="x-access-token",
            ),
        ),
    )

    assert await _git_credential_config(store, slots, seeded.workspace_id) == ()

    rules = await derive_credential_rules(slots, seeded.workspace_id, store)

    assert rules == ()
