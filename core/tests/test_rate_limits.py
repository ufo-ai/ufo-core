from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from ufo.schema import tables
from ufo.sdk.rate_limits import RateLimit, RateLimitExceeded, consume_rate_limits


@pytest.fixture
async def connection() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as opened:
        await opened.run_sync(tables.rate_limit.create)
        yield opened
    await engine.dispose()


async def test_rate_limits_refuse_the_first_dimension_over_its_maximum(
    connection: AsyncConnection,
) -> None:
    limits = (
        RateLimit("target", "person@example.com", 2, timedelta(hours=1)),
        RateLimit("global", "all", 10, timedelta(hours=1)),
    )
    await consume_rate_limits(connection, "email", limits)
    await consume_rate_limits(connection, "email", limits)
    with pytest.raises(RateLimitExceeded) as raised:
        await consume_rate_limits(connection, "email", limits)

    assert raised.value.dimension == "target"
    assert raised.value.maximum == 2
    rows = (await connection.execute(sa.select(tables.rate_limit))).all()
    assert {(row.dimension, row.hits) for row in rows} == {("target", 3), ("global", 2)}
    assert all(row.subject_hash != b"person@example.com" for row in rows)


async def test_global_limit_spans_distinct_targets(connection: AsyncConnection) -> None:
    await consume_rate_limits(
        connection,
        "email",
        (
            RateLimit("target", "one@example.com", 10, timedelta(hours=1)),
            RateLimit("global", "all", 1, timedelta(hours=1)),
        ),
    )
    with pytest.raises(RateLimitExceeded) as raised:
        await consume_rate_limits(
            connection,
            "email",
            (
                RateLimit("target", "two@example.net", 10, timedelta(hours=1)),
                RateLimit("global", "all", 1, timedelta(hours=1)),
            ),
        )

    assert raised.value.dimension == "global"
