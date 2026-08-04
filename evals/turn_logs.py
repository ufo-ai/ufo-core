import asyncio
import math
import socket
import zlib
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import UUID

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
    ExportLogsServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue

from evals.harness.capability import TurnLog
from evals.harness.harness import Json, JsonObject

OTLP_MEDIA_TYPE = "application/x-protobuf"
MAX_OTLP_BODY_BYTES = 1024 * 1024
MAX_TURN_LOG_BYTES = 16 * 1024
MAX_RETAINED_TURNS = 256
MAX_JSON_DEPTH = 16
LOG_EXPORT_WAIT_SECONDS = 30.0
RECEIVER_START_WAIT_SECONDS = 30.0
RECEIVER_STOP_GRACE_SECONDS = 5
RECEIVER_STOP_WAIT_SECONDS = 30.0
RECEIVER_CANCEL_WAIT_SECONDS = 10.0
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class _ReadyServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, ready: asyncio.Event) -> None:
        super().__init__(config)
        self.ready = ready

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets)
        if self.started:
            self.ready.set()


@dataclass(frozen=True)
class TurnLogCollector:
    host: str
    port: int
    workspace_id: UUID
    event: str
    records: dict[UUID, TurnLog] = field(default_factory=dict)
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    receiver_failed: asyncio.Event = field(default_factory=asyncio.Event)
    capacity_exceeded: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        if not self.event:
            raise ValueError("turn log event must be non-empty")

    @classmethod
    def from_endpoint(
        cls,
        endpoint: str | None,
        workspace_id: UUID,
        event: str,
    ) -> "TurnLogCollector":
        if endpoint is None:
            raise ValueError("turn log collection requires a loopback [o11y].otlp_endpoint")
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in LOOPBACK_HOSTS
            or parsed.port is None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("[o11y].otlp_endpoint must be http://<loopback-host>:<port>")
        return cls(parsed.hostname, parsed.port, workspace_id, event)

    @asynccontextmanager
    async def serving(self) -> AsyncIterator[None]:
        ready = asyncio.Event()
        stopping = False
        server = _ReadyServer(
            uvicorn.Config(
                self._app(),
                host=self.host,
                port=self.port,
                log_level="warning",
                access_log=False,
                lifespan="off",
                ws="none",
                timeout_graceful_shutdown=RECEIVER_STOP_GRACE_SECONDS,
            ),
            ready,
        )

        async def run_server() -> None:
            await server.serve()
            if not stopping:
                raise RuntimeError("eval receiver stopped")

        task = asyncio.create_task(run_server())

        def observe_server(_done: asyncio.Task[None]) -> None:
            if not stopping:
                self.receiver_failed.set()

        task.add_done_callback(observe_server)
        ready_wait = asyncio.create_task(ready.wait())
        try:
            done, _ = await asyncio.wait(
                (task, ready_wait),
                timeout=RECEIVER_START_WAIT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if task in done:
                await task
            if ready_wait not in done:
                raise RuntimeError("eval receiver did not start")
            yield
        finally:
            stopping = True
            ready_wait.cancel()
            await asyncio.gather(ready_wait, return_exceptions=True)
            if ready.is_set():
                server.should_exit = True
                await self._stopped(task)
            else:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _stopped(self, task: "asyncio.Task[None]") -> None:
        done, _ = await asyncio.wait((task,), timeout=RECEIVER_STOP_WAIT_SECONDS)
        if task in done:
            await task
            return
        message = f"eval receiver did not stop within {RECEIVER_STOP_WAIT_SECONDS}s of should_exit"
        task.cancel()
        done, _ = await asyncio.wait((task,), timeout=RECEIVER_CANCEL_WAIT_SECONDS)
        if task not in done:
            raise RuntimeError(f"{message}, and ignored cancellation")
        if not task.cancelled():
            failure = task.exception()
            if failure is not None:
                raise RuntimeError(message) from failure
        raise RuntimeError(message)

    async def read(self, turn_id: UUID) -> TurnLog | None:
        async def wait_for_record() -> TurnLog:
            async with self.changed:
                await self.changed.wait_for(lambda: turn_id in self.records)
                return self.records.pop(turn_id)

        record_wait = asyncio.create_task(wait_for_record())
        failure_wait = asyncio.create_task(self.receiver_failed.wait())
        try:
            done, _ = await asyncio.wait(
                (record_wait, failure_wait),
                timeout=LOG_EXPORT_WAIT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if failure_wait in done:
                if self.capacity_exceeded.is_set():
                    raise RuntimeError("turn log collector capacity exceeded")
                raise RuntimeError("eval receiver stopped")
            if record_wait in done:
                return record_wait.result()
            await self.discard(turn_id)
            return None
        finally:
            record_wait.cancel()
            failure_wait.cancel()
            await asyncio.gather(record_wait, failure_wait, return_exceptions=True)

    async def discard(self, turn_id: UUID) -> None:
        async with self.changed:
            self.records.pop(turn_id, None)

    def _app(self) -> FastAPI:
        app = FastAPI()
        app.add_api_route("/v1/logs", self._logs, methods=["POST"])
        app.add_api_route("/v1/traces", self._discard, methods=["POST"])
        app.add_api_route("/v1/metrics", self._discard, methods=["POST"])
        return app

    async def _logs(self, request: Request) -> Response:
        encoding = request.headers.get("content-encoding")
        if encoding is not None and encoding.strip().lower() != "gzip":
            return Response(status_code=415)
        body = await _bounded_body(request)
        if encoding is not None:
            body = _decompress_gzip(body)
        payload = ExportLogsServiceRequest()
        try:
            payload.ParseFromString(body)
        except DecodeError:
            return Response(status_code=400)
        accepted: dict[UUID, TurnLog] = {}
        rejected = 0
        for resource_logs in payload.resource_logs:
            for scope_logs in resource_logs.scope_logs:
                for record in scope_logs.log_records:
                    if (
                        record.body.WhichOneof("value") != "string_value"
                        or record.body.string_value != self.event
                    ):
                        continue
                    if record.ByteSize() > MAX_TURN_LOG_BYTES:
                        rejected += 1
                        continue
                    try:
                        raw = _attributes(record.attributes)
                        record_workspace = _string(raw.pop("workspace_id", None))
                    except ValueError:
                        rejected += 1
                        continue
                    if record_workspace != str(self.workspace_id):
                        continue
                    try:
                        turn_id = UUID(_string(raw.pop("turn_id", None)))
                        accepted[turn_id] = TurnLog(
                            event=record.body.string_value,
                            turn_id=turn_id,
                            attributes={name: _json(value) for name, value in raw.items()},
                        )
                    except (TypeError, ValueError):
                        rejected += 1
        async with self.changed:
            new_turns = accepted.keys() - self.records.keys()
            if len(self.records) + len(new_turns) > MAX_RETAINED_TURNS:
                self.capacity_exceeded.set()
                self.receiver_failed.set()
                return Response(status_code=429)
            self.records.update(accepted)
            if accepted:
                self.changed.notify_all()
        response = ExportLogsServiceResponse()
        if rejected:
            response.partial_success.rejected_log_records = rejected
            response.partial_success.error_message = "invalid turn log"
        return Response(response.SerializeToString(), media_type=OTLP_MEDIA_TYPE)

    async def _discard(self, request: Request) -> Response:
        await _bounded_body(request)
        return Response(content=b"", media_type=OTLP_MEDIA_TYPE)


async def _bounded_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError as error:
            raise HTTPException(status_code=400) from error
        if declared < 0:
            raise HTTPException(status_code=400)
        if declared > MAX_OTLP_BODY_BYTES:
            raise HTTPException(status_code=413)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_OTLP_BODY_BYTES:
            raise HTTPException(status_code=413)
        body.extend(chunk)
    return bytes(body)


def _decompress_gzip(body: bytes) -> bytes:
    decoder = zlib.decompressobj(zlib.MAX_WBITS | 16)
    try:
        decoded = decoder.decompress(body, MAX_OTLP_BODY_BYTES + 1)
    except zlib.error as error:
        raise HTTPException(status_code=400) from error
    if len(decoded) > MAX_OTLP_BODY_BYTES or decoder.unconsumed_tail:
        raise HTTPException(status_code=413)
    if not decoder.eof or decoder.unused_data:
        raise HTTPException(status_code=400)
    return decoded


def _attributes(items: Iterable[KeyValue]) -> dict[str, AnyValue]:
    attributes: dict[str, AnyValue] = {}
    for item in items:
        if item.key in attributes:
            raise ValueError("log attributes must have unique names")
        attributes[item.key] = item.value
    return attributes


def _string(value: AnyValue | None) -> str:
    if value is None or value.WhichOneof("value") != "string_value":
        raise ValueError("log correlation attribute must be a string")
    return value.string_value


def _json(value: AnyValue, depth: int = 0) -> Json:
    if depth > MAX_JSON_DEPTH:
        raise ValueError("log attributes exceed the nesting limit")
    match value.WhichOneof("value"):
        case "string_value":
            return value.string_value
        case "bool_value":
            return value.bool_value
        case "int_value":
            return value.int_value
        case "double_value":
            if not math.isfinite(value.double_value):
                raise ValueError("log attributes require finite numbers")
            return value.double_value
        case "array_value":
            return [_json(item, depth + 1) for item in value.array_value.values]
        case "kvlist_value":
            return _json_object(value.kvlist_value.values, depth + 1)
        case _:
            raise ValueError("log attribute has an unsupported value")


def _json_object(items: Iterable[KeyValue], depth: int) -> JsonObject:
    result: JsonObject = {}
    for item in items:
        if item.key in result:
            raise ValueError("log object attributes must have unique names")
        result[item.key] = _json(item.value, depth)
    return result
