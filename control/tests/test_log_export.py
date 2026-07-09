import logging
from typing import cast

from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    BatchLogRecordProcessor,
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)

from ufo_control import main


def test_root_handler_exports_every_record_except_the_exporters_own() -> None:
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    main._install_root_handler(provider)
    root = logging.getLogger()
    handler = root.handlers[-1]
    try:
        logging.getLogger("ufo_control.gateway").warning("claim failed")
        logging.getLogger("uvicorn.error").error("boom")
        logging.getLogger("opentelemetry.exporter.otlp").warning("export failed")
    finally:
        root.removeHandler(handler)
    records = [item.log_record for item in exporter.get_finished_logs()]
    assert [record.body for record in records] == ["claim failed", "boom"]
    assert [record.severity_text for record in records] == ["WARN", "ERROR"]


def test_export_logs_without_endpoint_keeps_stdout_only() -> None:
    handlers = list(logging.getLogger().handlers)
    main._export_logs(None)
    assert logging.getLogger().handlers == handlers


def test_export_logs_wires_the_collector_logs_url() -> None:
    main._export_logs("http://otel-collector.ufo-system.svc.cluster.local:4318/")
    root = logging.getLogger()
    handler = root.handlers[-1]
    assert isinstance(handler, LoggingHandler)
    try:
        provider = cast(LoggerProvider, handler._logger_provider)
        (processor,) = provider._multi_log_record_processor._log_record_processors
        exporter = cast(
            OTLPLogExporter, cast(BatchLogRecordProcessor, processor)._batch_processor._exporter
        )
        assert (
            exporter._endpoint == "http://otel-collector.ufo-system.svc.cluster.local:4318/v1/logs"
        )
    finally:
        root.removeHandler(handler)
        cast(LoggerProvider, handler._logger_provider).shutdown()
