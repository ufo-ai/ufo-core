from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.schema import tables


@dataclass(frozen=True)
class RateLimit:
    dimension: str
    subject: str
    maximum: int
    window: timedelta


class RateLimitExceeded(RuntimeError):
    def __init__(self, dimension: str, maximum: int, retry_at: datetime) -> None:
        super().__init__(f"{dimension} rate limit exceeded")
        self.dimension = dimension
        self.maximum = maximum
        self.retry_at = retry_at


async def consume_rate_limits(
    connection: AsyncConnection, namespace: str, limits: tuple[RateLimit, ...]
) -> None:
    now = datetime.now(UTC)
    insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
    for limit in limits:
        window_seconds = int(limit.window.total_seconds())
        window_start = datetime.fromtimestamp(
            int(now.timestamp()) // window_seconds * window_seconds, UTC
        )
        hits = (
            await connection.execute(
                insert(tables.rate_limit)
                .values(
                    namespace=namespace,
                    dimension=limit.dimension,
                    subject_hash=sha256(limit.subject.encode()).digest(),
                    window_start=window_start,
                    hits=1,
                )
                .on_conflict_do_update(
                    index_elements=[
                        tables.rate_limit.c.namespace,
                        tables.rate_limit.c.dimension,
                        tables.rate_limit.c.subject_hash,
                        tables.rate_limit.c.window_start,
                    ],
                    set_={"hits": tables.rate_limit.c.hits + 1},
                )
                .returning(tables.rate_limit.c.hits)
            )
        ).scalar_one()
        if hits > limit.maximum:
            raise RateLimitExceeded(limit.dimension, limit.maximum, window_start + limit.window)
