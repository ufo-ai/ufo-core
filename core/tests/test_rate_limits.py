from datetime import timedelta

import pytest
import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.schema import tables
from ufo.sdk.rate_limits import RateLimit, RateLimitExceeded, consume_rate_limits

pytestmark = pytest.mark.usefixtures("db")


async def test_rate_limits_refuse_the_first_dimension_over_its_maximum() -> None:
    limits = (
        RateLimit("target", "person@example.com", 2, timedelta(hours=1)),
        RateLimit("global", "all", 10, timedelta(hours=1)),
    )
    async with workspace_tx() as connection:
        await consume_rate_limits(connection, "email", limits)
        await consume_rate_limits(connection, "email", limits)
        with pytest.raises(RateLimitExceeded) as raised:
            await consume_rate_limits(connection, "email", limits)

    assert raised.value.dimension == "target"
    assert raised.value.maximum == 2
    async with workspace_tx(read_only=True) as connection:
        rows = (await connection.execute(sa.select(tables.rate_limit))).all()
    assert {(row.dimension, row.hits) for row in rows} == {("target", 3), ("global", 2)}
    assert all(row.subject_hash != b"person@example.com" for row in rows)


async def test_global_limit_spans_distinct_targets() -> None:
    async with workspace_tx() as connection:
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
