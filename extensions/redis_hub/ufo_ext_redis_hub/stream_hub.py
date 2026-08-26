"""Live-frame hub backed by Redis Streams: one stream per turn a turn publishes frame deltas to and
a surface tails out, cursor-replayable. Frames flow through Redis — never Postgres or the blob store
— so a lost frame costs a redrawn token, never correctness; the durable terminal answer lives in the
turn row. Cross-process fan-out: any serve instance publishes and any tails, so this backend scales
serve out past the single instance the in-process hub allows.

Publishers and subscribers live on different event loops in one process (DBOS runs dequeued
workflows on its own loop thread, surfaces on the serve loop), and an asyncio Redis client binds
its futures to the loop that created it — so the hub keeps one client per running loop, minted on
first use, never shared across loops. A timeout inside the blocking XREAD is the read's designed
idle outcome, not a fault: the subscribe loop reads again from its cursor, losing nothing."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import cast
from uuid import UUID

from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.typing import StreamEntry, XReadResponse

from ufo.sdk.hub import (
    Absorbed,
    Activity,
    CostTick,
    LiveFrame,
    Parked,
    Reply,
    Resumed,
    SubagentActivity,
    Terminal,
    TextDelta,
)

STREAM_PREFIX = "ufo:turn"
STREAM_MAXLEN = 10_000
STREAM_IDLE_TTL_SECONDS = 24 * 3600
SUBSCRIBE_BATCH = 100
SUBSCRIBE_BLOCK_MS = 5_000
ACTIVITY_PEEK_FRAMES = 500
TOOL_ACTIVITY_KIND = "tool_call"
SKILL_ACTIVITY_KIND = "skill_load"


class _ToolActivityWire(BaseModel):
    tool: str
    preview: str
    description: str = ""


class _SkillActivityWire(BaseModel):
    skill: str


_FRAME_KINDS: tuple[tuple[str, type[BaseModel]], ...] = (
    ("text_delta", TextDelta),
    ("terminal", Terminal),
    ("parked", Parked),
    ("cost_tick", CostTick),
    (TOOL_ACTIVITY_KIND, Activity),
    ("absorbed", Absorbed),
    ("resumed", Resumed),
    ("reply", Reply),
    ("subagent_activity", SubagentActivity),
)
_KIND_BY_TYPE = {cls: kind for kind, cls in _FRAME_KINDS}
_TYPE_BY_KIND = {kind: cls for kind, cls in _FRAME_KINDS}
_ACTIVITY_KINDS = frozenset({TOOL_ACTIVITY_KIND, SKILL_ACTIVITY_KIND})


def frame_payload(frame: LiveFrame) -> dict[str, object]:
    """The wire form of one frame: its kind tag and its model fields, so a tail reconstructs the
    exact LiveFrame variant it was published as."""
    if isinstance(frame, Activity):
        data = _ToolActivityWire(tool="activity", preview="", description=frame.text)
        return {"kind": TOOL_ACTIVITY_KIND, "data": data.model_dump(mode="json")}
    return {"kind": _KIND_BY_TYPE[type(frame)], "data": frame.model_dump(mode="json")}


def frame_from_payload(payload: dict[str, object]) -> LiveFrame:
    kind = payload["kind"]
    if kind == TOOL_ACTIVITY_KIND:
        tool_wire = _ToolActivityWire.model_validate(payload["data"])
        return Activity(text=tool_wire.description or tool_wire.tool)
    if kind == SKILL_ACTIVITY_KIND:
        skill_wire = _SkillActivityWire.model_validate(payload["data"])
        return Activity(text=f"Loading {skill_wire.skill}.")
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
    trimmed away), so a reconnecting tail resumes gaplessly or redraws from the start. Clients are
    per running loop (`_client`), so the DBOS loop's publishes and the serve loop's tails never
    share loop-bound connection state."""

    url: str
    _clients: dict[asyncio.AbstractEventLoop, Redis] = field(default_factory=dict)

    def _client(self) -> Redis:
        loop = asyncio.get_running_loop()
        client = self._clients.get(loop)
        if client is None:
            client = Redis.from_url(self.url, decode_responses=True)
            self._clients[loop] = client
        return client

    def _stream(self, turn_id: UUID) -> str:
        return f"{STREAM_PREFIX}:{turn_id}"

    async def publish(self, turn_id: UUID, frame: LiveFrame) -> str:
        stream = self._stream(turn_id)
        wire = json.dumps(frame_payload(frame), sort_keys=True, separators=(",", ":"))
        async with self._client().pipeline(transaction=False) as pipe:
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
            try:
                entries = _stream_entries(
                    await self._client().xread({stream: last}, count=SUBSCRIBE_BATCH)
                )
                if not entries:
                    entries = _stream_entries(
                        await self._client().xread(
                            {stream: last}, count=SUBSCRIBE_BATCH, block=SUBSCRIBE_BLOCK_MS
                        )
                    )
            except (TimeoutError, RedisTimeoutError):
                continue
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
        first = await self._client().xrange(self._stream(turn_id), count=1)
        if not first:
            return False
        return _stream_id(str(first[0][0])) <= _stream_id(cursor)

    async def latest_activity(self, turn_id: UUID) -> Activity | None:
        """The newest Activity among the turn's last `ACTIVITY_PEEK_FRAMES` stream
        entries, read newest-first in bounded pages. None when the stream is gone, or none of those
        entries is an activity frame.

        The bound is the peek's whole point: a turn only streaming text carries no activity frame at
        all, and paging a ten-thousand-entry stream to learn that is work a four-second poll repeats
        for every such agent. A turn that has published this many frames since its last tool call or
        skill load has been narrating prose for thousands of tokens, so that frame no longer names
        what it is doing and the honest answer is None. An entry's kind is on the wire beside its
        data, so only the frame this returns is ever decoded."""
        stream = self._stream(turn_id)
        newest = "+"
        remaining = ACTIVITY_PEEK_FRAMES
        while remaining:
            entries = await self._client().xrevrange(
                stream, max=newest, min="-", count=min(remaining, SUBSCRIBE_BATCH)
            )
            if not entries:
                return None
            oldest = ""
            for entry_id, fields in entries:
                if entry_id is None or fields is None:
                    continue
                oldest = str(entry_id)
                remaining -= 1
                payload = json.loads(fields["frame"])
                if payload["kind"] in _ACTIVITY_KINDS:
                    return cast(Activity, frame_from_payload(payload))
            if not oldest:
                return None
            newest = f"({oldest}"
        return None
