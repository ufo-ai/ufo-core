"""Live-frame hub backed by Redis Streams: one stream per turn a turn publishes frame deltas to and
a surface tails out, cursor-replayable. Frames flow through Redis — never Postgres or the blob store
— so a lost frame costs a redrawn token, never correctness; the durable terminal answer lives in the
turn row. Cross-process fan-out: any serve instance publishes and any tails, so this backend scales
serve out past the single instance the in-process hub allows."""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from pydantic import BaseModel
from redis.asyncio import Redis
from redis.typing import StreamEntry, XReadResponse

from ufo.sdk.hub import (
    CostTick,
    LiveFrame,
    Parked,
    SkillLoad,
    Terminal,
    TextDelta,
    ToolCall,
)

STREAM_PREFIX = "ufo:turn"
STREAM_MAXLEN = 10_000
STREAM_IDLE_TTL_SECONDS = 24 * 3600
SUBSCRIBE_BATCH = 100
SUBSCRIBE_BLOCK_MS = 5_000

_FRAME_KINDS: tuple[tuple[str, type[BaseModel]], ...] = (
    ("text_delta", TextDelta),
    ("terminal", Terminal),
    ("parked", Parked),
    ("cost_tick", CostTick),
    ("tool_call", ToolCall),
    ("skill_load", SkillLoad),
)
_KIND_BY_TYPE = {cls: kind for kind, cls in _FRAME_KINDS}
_TYPE_BY_KIND = {kind: cls for kind, cls in _FRAME_KINDS}


def frame_payload(frame: LiveFrame) -> dict[str, object]:
    """The wire form of one frame: its kind tag and its model fields, so a tail reconstructs the
    exact LiveFrame variant it was published as."""
    return {"kind": _KIND_BY_TYPE[type(frame)], "data": frame.model_dump(mode="json")}


def frame_from_payload(payload: dict[str, object]) -> LiveFrame:
    kind = payload["kind"]
    if not isinstance(kind, str) or kind not in _TYPE_BY_KIND:
        raise ValueError(f"unknown live-frame kind: {kind!r}")
    return cast(LiveFrame, _TYPE_BY_KIND[kind].model_validate(payload["data"]))


def _stream_id(entry_id: str) -> tuple[int, int]:
    ms, _, seq = entry_id.partition("-")
    return int(ms), int(seq or 0)


def _stream_entries(batch: XReadResponse) -> list[StreamEntry]:
    """`xread` against a RESP2 connection always answers `list[[stream, entries]]` — never the
    dict shape RESP3 would use — so an unexpected shape fails loud rather than misreading a
    stream name as an entry."""
    if not batch:
        return []
    if not isinstance(batch, list):
        raise TypeError(f"expected a RESP2 XREAD list response, got {type(batch).__name__}")
    entries: list[StreamEntry] = batch[0][1]
    return entries


@dataclass(frozen=True)
class RedisStreamHub:
    """One Redis Stream per turn, trimmed to STREAM_MAXLEN and expiring STREAM_IDLE_TTL_SECONDS
    after the last publish. `publish` appends a frame and returns its stream entry id as the cursor;
    `subscribe` blocks-reads forward from a cursor, replaying the retained stream from there before
    streaming new entries; `covers` reports whether the entry a cursor names is still retained (not
    trimmed away), so a reconnecting tail resumes gaplessly or redraws from the start."""

    client: Redis

    def _stream(self, turn_id: UUID) -> str:
        return f"{STREAM_PREFIX}:{turn_id}"

    async def publish(self, turn_id: UUID, frame: LiveFrame) -> str:
        stream = self._stream(turn_id)
        wire = json.dumps(frame_payload(frame), sort_keys=True, separators=(",", ":"))
        async with self.client.pipeline(transaction=False) as pipe:
            pipe.xadd(stream, {"frame": wire}, maxlen=STREAM_MAXLEN, approximate=True)
            pipe.expire(stream, STREAM_IDLE_TTL_SECONDS)
            results = await pipe.execute()
        return str(results[0])

    async def subscribe(
        self, turn_id: UUID, cursor: str = ""
    ) -> AsyncIterator[tuple[str, LiveFrame]]:
        stream = self._stream(turn_id)
        last = cursor or "0"
        while True:
            entries = _stream_entries(
                await self.client.xread({stream: last}, count=SUBSCRIBE_BATCH)
            )
            if not entries:
                entries = _stream_entries(
                    await self.client.xread(
                        {stream: last}, count=SUBSCRIBE_BATCH, block=SUBSCRIBE_BLOCK_MS
                    )
                )
                if not entries:
                    continue
            for entry_id, fields in entries:
                if entry_id is None or fields is None:
                    continue
                last = str(entry_id)
                yield last, frame_from_payload(json.loads(fields["frame"]))

    async def covers(self, turn_id: UUID, cursor: str) -> bool:
        if not cursor:
            return False
        first = await self.client.xrange(self._stream(turn_id), count=1)
        if not first:
            return False
        return _stream_id(str(first[0][0])) <= _stream_id(cursor)
