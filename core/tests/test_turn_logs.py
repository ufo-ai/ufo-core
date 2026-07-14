"""The eval collector's bounded structured-log transport."""

import asyncio
import gzip
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from opentelemetry._logs import SeverityNumber
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
    ExportLogsServiceResponse,
)
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

import evals.turn_logs as turn_logs
from evals.harness.capability import TurnLog
from evals.turn_logs import (
    MAX_JSON_DEPTH,
    MAX_OTLP_BODY_BYTES,
    MAX_RETAINED_TURNS,
    MAX_TURN_LOG_BYTES,
    TurnLogCollector,
    _ReadyServer,
)

TURN_EVENT = "eval.turn.context"


@pytest.mark.parametrize(
    "endpoint",
    (
        None,
        "https://127.0.0.1:4318",
        "http://collector:4318",
        "http://127.0.0.1:4318/base",
    ),
)
def test_turn_log_collector_requires_a_plain_loopback_otlp_endpoint(
    endpoint: str | None,
) -> None:
    with pytest.raises(ValueError, match="loopback"):
        TurnLogCollector.from_endpoint(endpoint, uuid4(), TURN_EVENT)


def test_turn_log_collector_requires_an_event() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        TurnLogCollector("127.0.0.1", 4318, uuid4(), "")


@pytest.mark.parametrize("compression", (None, Compression.Gzip))
async def test_turn_log_collector_receives_its_allowlisted_otlp_event(
    unused_tcp_port: int, compression: Compression | None
) -> None:
    workspace_id = uuid4()
    turn_id = uuid4()
    item_id = uuid4()
    collector = TurnLogCollector("127.0.0.1", unused_tcp_port, workspace_id, TURN_EVENT)
    exporter = OTLPLogExporter(
        endpoint=f"http://127.0.0.1:{unused_tcp_port}/v1/logs", compression=compression
    )
    provider = LoggerProvider()
    provider.add_log_record_processor(BatchLogRecordProcessor(exporter, schedule_delay_millis=50))
    logger = provider.get_logger("ufo-test")

    async with collector.serving():
        await asyncio.to_thread(
            logger.emit,
            severity_number=SeverityNumber.INFO,
            severity_text="INFO",
            body="ignored.event",
            attributes={"workspace_id": str(workspace_id), "turn_id": str(turn_id)},
        )
        await asyncio.to_thread(
            logger.emit,
            severity_number=SeverityNumber.INFO,
            severity_text="INFO",
            body=TURN_EVENT,
            attributes={"workspace_id": str(uuid4()), "turn_id": str(turn_id)},
        )
        await asyncio.to_thread(
            logger.emit,
            severity_number=SeverityNumber.INFO,
            severity_text="INFO",
            body=TURN_EVENT,
            attributes={
                "workspace_id": str(workspace_id),
                "turn_id": str(turn_id),
                "item_ids": [str(item_id)],
                "duration_ms": 2.5,
                "completed": True,
            },
        )
        await asyncio.to_thread(provider.force_flush)
        received = await collector.read(turn_id)

    await asyncio.to_thread(provider.shutdown)
    assert received == TurnLog(
        event=TURN_EVENT,
        turn_id=turn_id,
        attributes={
            "item_ids": [str(item_id)],
            "duration_ms": 2.5,
            "completed": True,
        },
    )


async def test_turn_log_collector_keeps_one_record_per_turn() -> None:
    workspace_id = uuid4()
    turn_id = uuid4()
    collector = TurnLogCollector("127.0.0.1", 4318, workspace_id, TURN_EVENT)
    payload = ExportLogsServiceRequest()
    _record(payload, workspace_id, turn_id, {"attempt": "first"})
    _record(payload, workspace_id, turn_id, {"attempt": "second"})

    response = await _post(collector, payload)

    assert response.status_code == 200
    assert len(collector.records) == 1
    received = await collector.read(turn_id)
    assert received is not None
    assert received.attributes == {"attempt": "second"}


async def test_turn_log_collector_fails_the_run_when_a_batch_exceeds_capacity() -> None:
    workspace_id = uuid4()
    turn_id = uuid4()
    collector = TurnLogCollector("127.0.0.1", 4318, workspace_id, TURN_EVENT)
    collector.records.update(
        {
            existing: TurnLog(event=TURN_EVENT, turn_id=existing, attributes={})
            for existing in (uuid4() for _ in range(MAX_RETAINED_TURNS - 1))
        }
    )
    payload = ExportLogsServiceRequest()
    _record(payload, workspace_id, turn_id, {})
    _record(payload, workspace_id, uuid4(), {})

    response = await _post(collector, payload)

    assert response.status_code == 429
    assert len(collector.records) == MAX_RETAINED_TURNS - 1
    assert turn_id not in collector.records
    assert collector.receiver_failed.is_set()
    with pytest.raises(RuntimeError, match="collector capacity exceeded"):
        await collector.read(turn_id)


async def test_turn_log_collector_rejects_an_oversized_record() -> None:
    workspace_id = uuid4()
    collector = TurnLogCollector("127.0.0.1", 4318, workspace_id, TURN_EVENT)
    payload = ExportLogsServiceRequest()
    _record(payload, workspace_id, uuid4(), {"large": "x" * MAX_TURN_LOG_BYTES})

    response = await _post(collector, payload)

    parsed = ExportLogsServiceResponse()
    parsed.ParseFromString(response.content)
    assert response.status_code == 200
    assert parsed.partial_success.rejected_log_records == 1
    assert collector.records == {}


async def test_turn_log_collector_rejects_deeply_nested_attributes() -> None:
    workspace_id = uuid4()
    collector = TurnLogCollector("127.0.0.1", 4318, workspace_id, TURN_EVENT)
    payload = ExportLogsServiceRequest()
    record = _record(payload, workspace_id, uuid4(), {})
    nested = record.attributes.add()
    nested.key = "nested"
    value = nested.value
    for _ in range(MAX_JSON_DEPTH + 2):
        value = value.array_value.values.add()
    value.string_value = "bounded"

    response = await _post(collector, payload)

    parsed = ExportLogsServiceResponse()
    parsed.ParseFromString(response.content)
    assert response.status_code == 200
    assert parsed.partial_success.rejected_log_records == 1
    assert collector.records == {}


async def test_turn_log_collector_reports_malformed_allowlisted_records() -> None:
    workspace_id = uuid4()
    collector = TurnLogCollector("127.0.0.1", 4318, workspace_id, TURN_EVENT)
    payload = ExportLogsServiceRequest()
    record = payload.resource_logs.add().scope_logs.add().log_records.add()
    record.body.string_value = TURN_EVENT
    for value in (str(uuid4()), str(uuid4())):
        attribute = record.attributes.add()
        attribute.key = "turn_id"
        attribute.value.string_value = value
    workspace = record.attributes.add()
    workspace.key = "workspace_id"
    workspace.value.string_value = str(workspace_id)

    response = await _post(collector, payload)

    parsed = ExportLogsServiceResponse()
    parsed.ParseFromString(response.content)
    assert response.status_code == 200
    assert parsed.partial_success.rejected_log_records == 1
    assert parsed.partial_success.error_message == "invalid turn log"
    assert collector.records == {}


async def test_turn_log_collector_rejects_invalid_otlp_requests() -> None:
    collector = TurnLogCollector("127.0.0.1", 4318, uuid4(), TURN_EVENT)
    payload = ExportLogsServiceRequest().SerializeToString()

    async def oversized_body() -> AsyncIterator[bytes]:
        yield b"x" * (MAX_OTLP_BODY_BYTES + 1)

    async with AsyncClient(
        transport=ASGITransport(app=collector._app()), base_url="http://collector"
    ) as client:
        compressed = await client.post(
            "/v1/logs",
            content=gzip.compress(payload),
            headers={"content-encoding": "gzip"},
        )
        unsupported_encoding = await client.post("/v1/logs", headers={"content-encoding": "br"})
        invalid_gzip = await client.post(
            "/v1/logs", content=b"invalid", headers={"content-encoding": "gzip"}
        )
        decompressed_oversized = await client.post(
            "/v1/logs",
            content=gzip.compress(b"x" * (MAX_OTLP_BODY_BYTES + 1)),
            headers={"content-encoding": "gzip"},
        )
        malformed = await client.post("/v1/logs", content=b"\x80")
        invalid_length = await client.post(
            "/v1/logs", content=b"", headers={"content-length": "invalid"}
        )
        negative_length = await client.post(
            "/v1/logs", content=b"", headers={"content-length": "-1"}
        )
        declared_oversized = await client.post(
            "/v1/logs",
            content=b"",
            headers={"content-length": str(MAX_OTLP_BODY_BYTES + 1)},
        )
        streamed_oversized = await client.post("/v1/logs", content=oversized_body())

    assert compressed.status_code == 200
    assert unsupported_encoding.status_code == 415
    assert invalid_gzip.status_code == 400
    assert decompressed_oversized.status_code == 413
    assert malformed.status_code == 400
    assert invalid_length.status_code == 400
    assert negative_length.status_code == 400
    assert declared_oversized.status_code == 413
    assert streamed_oversized.status_code == 413


async def test_turn_log_collector_cleans_up_a_timed_out_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turn_id = uuid4()
    collector = TurnLogCollector("127.0.0.1", 4318, uuid4(), TURN_EVENT)
    monkeypatch.setattr(turn_logs, "LOG_EXPORT_WAIT_SECONDS", 0)

    assert await collector.read(turn_id) is None
    assert turn_id not in collector.records


async def test_turn_log_collector_discards_a_turn() -> None:
    turn_id = uuid4()
    collector = TurnLogCollector("127.0.0.1", 4318, uuid4(), TURN_EVENT)
    collector.records[turn_id] = TurnLog(event=TURN_EVENT, turn_id=turn_id, attributes={})

    await collector.discard(turn_id)

    assert turn_id not in collector.records


async def test_turn_log_collector_fails_when_receiver_never_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def never_start(_server: object) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr("evals.turn_logs._ReadyServer.serve", never_start)
    monkeypatch.setattr(turn_logs, "RECEIVER_START_WAIT_SECONDS", 0)
    collector = TurnLogCollector("127.0.0.1", 4318, uuid4(), TURN_EVENT)
    with pytest.raises(RuntimeError, match="receiver did not start"):
        async with collector.serving():
            raise AssertionError("unreachable")


async def test_turn_log_collector_fails_when_receiver_stops_during_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def start_then_stop(server: _ReadyServer) -> None:
        server.ready.set()

    monkeypatch.setattr("evals.turn_logs._ReadyServer.serve", start_then_stop)
    collector = TurnLogCollector("127.0.0.1", 4318, uuid4(), TURN_EVENT)
    with pytest.raises(RuntimeError, match="receiver stopped"):
        async with collector.serving():
            raise AssertionError("unreachable")


async def test_turn_log_collector_fails_a_read_when_receiver_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = asyncio.Event()

    async def stop_after_start(server: _ReadyServer) -> None:
        server.ready.set()
        await stop.wait()

    monkeypatch.setattr("evals.turn_logs._ReadyServer.serve", stop_after_start)
    collector = TurnLogCollector("127.0.0.1", 4318, uuid4(), TURN_EVENT)
    async with asyncio.timeout(1):
        with pytest.raises(RuntimeError, match="receiver stopped"):
            async with collector.serving():
                stop.set()
                await collector.read(uuid4())


async def _post(collector: TurnLogCollector, payload: ExportLogsServiceRequest) -> object:
    async with AsyncClient(
        transport=ASGITransport(app=collector._app()), base_url="http://collector"
    ) as client:
        return await client.post("/v1/logs", content=payload.SerializeToString())


def _record(
    payload: ExportLogsServiceRequest,
    workspace_id: UUID,
    turn_id: UUID,
    attributes: dict[str, str],
):
    record = payload.resource_logs.add().scope_logs.add().log_records.add()
    record.body.string_value = TURN_EVENT
    for name, value in {
        "workspace_id": str(workspace_id),
        "turn_id": str(turn_id),
        **attributes,
    }.items():
        attribute = record.attributes.add()
        attribute.key = name
        attribute.value.string_value = value
    return record
