import asyncio
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import TypedDict, cast
from uuid import uuid4

import sqlalchemy as sa

from ufo.config import load_config
from ufo.db import dispose_db, init_db, workspace_tx
from ufo.runtime.billing.accounting import mint_usage_exports
from ufo.runtime.workspace import ws

CONSUMER = "metronome"
EXPECTED_ROLE = "ufo_serve"
EXPECTED_INDEX = "ledger_export_pkey"
RELATION = "ledger_export"
MAX_EXECUTION_MS = 10.0
MAX_EXPORT_ROWS = 2


class PlanResult(TypedDict):
    status: str
    db_role: str
    relation: str
    index: str
    access_method: str
    execution_ms: float
    threshold_ms: float
    seq_scan: bool
    actual_export_rows: int
    query_sha256: str
    failures: list[str]


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("plan value is not an object")
    return cast(Mapping[str, object], value)


def _number(value: object) -> float:
    if not isinstance(value, int | float):
        raise ValueError("plan value is not numeric")
    return float(value)


def evaluate_plan(explained: object, role: str, query_sha256: str) -> PlanResult:
    root = _mapping(explained)
    execution_ms = _number(root.get("Execution Time"))
    nodes = [_mapping(root.get("Plan"))]
    for node in nodes:
        plans = node.get("Plans", ())
        if not isinstance(plans, list | tuple):
            raise ValueError("plan children are not a list")
        nodes.extend(_mapping(child) for child in plans)
    export_nodes = [node for node in nodes if node.get("Relation Name") == RELATION]
    indexes = {index for node in export_nodes if isinstance((index := node.get("Index Name")), str)}
    seq_scan = any(node.get("Node Type") == "Seq Scan" for node in export_nodes)
    actual_rows = round(
        sum(
            _number(node.get("Actual Rows", 0)) * _number(node.get("Actual Loops", 0))
            for node in export_nodes
        )
    )
    failures = []
    if role != EXPECTED_ROLE:
        failures.append("unexpected database role")
    if not export_nodes:
        failures.append("ledger_export is absent from the plan")
    if seq_scan:
        failures.append("ledger_export uses a sequential scan")
    if EXPECTED_INDEX not in indexes:
        failures.append("ledger_export_pkey is absent from the plan")
    if actual_rows > MAX_EXPORT_ROWS:
        failures.append("ledger_export row visits exceed the bound")
    if execution_ms >= MAX_EXECUTION_MS:
        failures.append("execution time exceeds the bound")
    return PlanResult(
        status="error" if failures else "ok",
        db_role=EXPECTED_ROLE if role == EXPECTED_ROLE else "unexpected",
        relation=RELATION,
        index=EXPECTED_INDEX if EXPECTED_INDEX in indexes else "none",
        access_method=(
            "seq_scan" if seq_scan else "index_scan" if EXPECTED_INDEX in indexes else "other"
        ),
        execution_ms=round(execution_ms, 3),
        threshold_ms=MAX_EXECUTION_MS,
        seq_scan=seq_scan,
        actual_export_rows=actual_rows,
        query_sha256=query_sha256,
        failures=failures,
    )


async def _probe() -> PlanResult:
    config = load_config()
    init_db(config.database.url)
    try:
        idle_workspace_id = uuid4()
        captured: list[tuple[str, tuple[object, ...]]] = []
        with ws(idle_workspace_id):
            async with workspace_tx() as connection:
                role = (await connection.execute(sa.text("select current_user"))).scalar_one()

                def record(
                    sync_connection: sa.Connection,
                    cursor: object,
                    statement: str,
                    parameters: object,
                    context: object,
                    executemany: bool,
                ) -> None:
                    if statement.lstrip().startswith("SELECT") and RELATION in statement:
                        if not isinstance(parameters, tuple):
                            raise ValueError("query parameters are not positional")
                        captured.append((statement, parameters))

                sa.event.listen(connection.sync_connection, "before_cursor_execute", record)
                try:
                    await mint_usage_exports(
                        connection,
                        idle_workspace_id,
                        CONSUMER,
                        datetime.now(UTC) - timedelta(days=7),
                        lambda model: None,
                    )
                finally:
                    sa.event.remove(connection.sync_connection, "before_cursor_execute", record)
                if len(captured) != 1:
                    raise ValueError("billing query capture count is not one")
                statement, parameters = captured[0]
                explained = (
                    await connection.exec_driver_sql(
                        f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {statement}", parameters
                    )
                ).scalar_one()[0]
        if not isinstance(role, str):
            raise ValueError("database role is not text")
        return evaluate_plan(
            explained,
            role,
            hashlib.sha256(statement.encode()).hexdigest(),
        )
    finally:
        await dispose_db()


def _failed(error: Exception) -> PlanResult:
    return PlanResult(
        status="error",
        db_role="unverified",
        relation=RELATION,
        index="none",
        access_method="unavailable",
        execution_ms=0.0,
        threshold_ms=MAX_EXECUTION_MS,
        seq_scan=False,
        actual_export_rows=0,
        query_sha256="",
        failures=[f"probe failed with {type(error).__name__}"],
    )


def main() -> int:
    try:
        result = asyncio.run(_probe())
    except Exception as error:
        result = _failed(error)
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
