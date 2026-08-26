"""Terminal rendezvous across pods, backed by Redis Streams and the fleet's blob store.

RFC 0026's terminal-as-sandbox binds a conversation's workspace to the member's own directory
through a rendezvous that is correct only while one process serves both the held connection and the
turn's workflow. On the shared fleet those land on different pods, so this transport carries the
rendezvous over Redis (the control path — reliable, cursor-replayable, the `stream_hub` pattern)
and the blob store (the bytes), keyed so every request stays pod-agnostic:

    term:bind:{conversation}      the binding + liveness the connection pod refreshes under a TTL
    term:inflight:{conversation}  the binding the in-flight op pins, outliving the client's stream
    term:op:{conversation}      the op stream a turn XADDs and a held stream XREADs
    term:reply:{op_id}          the reply stream the client's POST XADDs and the sender XREADs
    term:lock:{conversation}    the per-conversation lock held across a send (one op at a time)
    term:deliv:{op_id}          the render marker that makes op delivery idempotent on reconnect
    term:opmeta:{op_id}         the op's member, for the reply/copy-in gate, alive for the op
    term/op/{op_id}             the copy-in body (blob) a write stages
    term/reply/{op_id}          a large copy-out reply (blob); small replies ride the reply stream

Clients are per running loop (`_client`, the `stream_hub` discipline): the turn's `send` runs on
the DBOS workflow loop while `next_op`/`connect`/`disconnect`/`resolve`/`staged` run on serve's, and
an asyncio Redis client binds its futures to the loop that made it. A client's wait always ends:
the sender's reply XREAD blocks until the reply record appears or the op's own deadline elapses,
then raises `TerminalGone` and clears the op's state — no path leaves a turn wedged."""

import asyncio
import base64
import json
import threading
from collections.abc import Coroutine
from contextlib import suppress
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.typing import EncodableT, FieldT, StreamEntry, XReadResponse

from ufo.sdk.o11y import warn
from ufo.sdk.terminal import (
    ARRIVAL_GRACE_SECONDS,
    OP_DEADLINE_SLACK_SECONDS,
    BlobNotFound,
    BlobStore,
    TerminalGone,
    TerminalOp,
    TerminalOpFailed,
    TerminalWorkspace,
)

_StreamFields = dict[bytes | str, bytes | str]


def _text(value: bytes | str) -> str:
    """A Redis string field as `str`. The clients decode responses, so a value is already `str` at
    runtime; the coercion satisfies the stub's `bytes | str` and stays correct either way."""
    return value if isinstance(value, str) else value.decode()


def _pairs(flat: object) -> _StreamFields:
    """The `[field, value, field, value, …]` array a Lua XRANGE entry carries, as a field map."""
    if not isinstance(flat, list):
        raise TypeError(f"expected a flat field list from the claim script, got {type(flat)}")
    items = [_text(item) for item in flat]
    return {items[i]: items[i + 1] for i in range(0, len(items) - 1, 2)}


def _bind_payload(cwd: str, member_id: UUID | None, runtime_id: str) -> str:
    """The binding a held connection or an in-flight op publishes: where the terminal stands and
    whose it is, the one shape both the liveness key and the inflight pin carry."""
    return json.dumps(
        {"cwd": cwd, "member_id": member_id.hex if member_id else None, "runtime_id": runtime_id}
    )


BIND_TTL_SECONDS = 60
BIND_REFRESH_SECONDS = 20
ARRIVAL_POLL_SECONDS = 0.5
OP_BLOCK_MS = 2_000
REPLY_BLOCK_MS = 5_000
MIN_BLOCK_MS = 1
"""The floor on a blocking XREAD. Redis reads `BLOCK 0` as block forever, so a reply poll with under
a millisecond of the deadline left would wait past the very deadline it enforces and never wake the
sender to raise `TerminalGone`."""
SOCKET_TIMEOUT_SECONDS = 10.0
SOCKET_CONNECT_TIMEOUT_SECONDS = 5.0
OP_STREAM_MAXLEN = 128
OP_STREAM_TTL_SECONDS = 24 * 3600
REPLY_STREAM_MAXLEN = 8
REPLY_STREAM_TTL_SECONDS = 3600
DELIV_PREFIX = "term:deliv:"
OPMETA_MARGIN_SECONDS = 60
OP_WINDOW_MARGIN_SECONDS = int(OP_DEADLINE_SLACK_SECONDS) + OPMETA_MARGIN_SECONDS
"""The margin an op's own keys outlive its timeout by. Strictly greater than the sender's own
deadline slack, so the delivery marker a connection pod claims — held for the whole window, not a
short lease — cannot lapse while a turn may still be awaiting the reply. Two held streams can share
one conversation (`ufo --resume` on the same channel), so a lapsed claim would let a second stream
re-render an op the first is still executing; the window forbids it, and once the window passes no
turn may still await, so nothing is left that could hand the op out again. The trade is that a claim
that never reached the client strands until the sender's own `TerminalGone` deadline — strictly
better than an `exec` running twice on the member's machine."""
LOCK_TTL_MARGIN_SECONDS = 90
"""The lock outlives the whole hold — the copy-in blob put, the op deadline, and the tear-down —
so it never expires under its holder and admits a second concurrent op into the stream."""
HARD_MARGIN_SECONDS = 30
BLOB_OP_TIMEOUT_SECONDS = 30.0
REPLY_INLINE_MAX_BYTES = 64 * 1024

_NEXT_OP_LUA = """
local stream = KEYS[1]
local margin = tonumber(ARGV[1])
local exclude = ARGV[2]
local prefix = ARGV[3]
local t = redis.call('TIME')
local now_ms = t[1] * 1000 + math.floor(t[2] / 1000)
local entries = redis.call('XRANGE', stream, '-', '+')
local last = '0'
for _, entry in ipairs(entries) do
  local id = entry[1]
  last = id
  local flat = entry[2]
  local f = {}
  for j = 1, #flat, 2 do f[flat[j]] = flat[j + 1] end
  local op_id = f['op_id']
  local id_ms = tonumber(string.match(id, '^(%d+)'))
  local window_ms = (tonumber(f['timeout_s']) + margin) * 1000
  if now_ms > id_ms + window_ms then
    redis.call('XDEL', stream, id)
  elseif op_id ~= exclude then
    if redis.call('EXISTS', prefix .. op_id) == 0 then
      redis.call('SET', prefix .. op_id, '1', 'PX', window_ms)
      return {'ok', id, flat}
    end
  end
end
return {'wait', last}
"""
"""Atomic read-and-claim on Redis's own clock: one round trip that scans the op stream oldest-first,
reaps any entry past its window (its stream-id timestamp plus its own timeout plus the margin — both
timestamps from Redis, so no cross-pod clock skew mis-reaps a live op), skips the op a reply POST is
excluding, and claims the first entry with no delivery marker by SETting the marker for that whole
window. Reading and claiming in one script closes the window where a concurrent reader re-claims
between a separate XREAD and SETNX; the window-long marker forbids a second held stream re-rendering
an op the first is still executing."""


def _stream_entries(batch: XReadResponse) -> list[StreamEntry]:
    """`xread` against a RESP2 connection answers `list[[stream, entries]]` — never the dict shape
    RESP3 would use — so an unexpected shape fails loud rather than misreading a stream name as an
    entry (the `stream_hub` guard)."""
    if not batch:
        return []
    if not isinstance(batch, list):
        raise TypeError(f"expected a RESP2 XREAD list response, got {type(batch).__name__}")
    entries: list[StreamEntry] = batch[0][1]
    return entries


@dataclass
class _Hold:
    """One conversation's connection held on this pod: the binding it publishes and the heartbeat
    task refreshing its liveness key, kept alive across the reconnects a held stream's cap makes
    routine (a newer held stream on the same pod shares the count, the last to leave stops the
    heartbeat). The binding is the row's; the key exists only so another pod's turn can find it."""

    cwd: str
    member_id: UUID | None
    runtime_id: str
    connections: int = 0
    task: asyncio.Task[None] | None = None


@dataclass(frozen=True)
class RedisTerminals:
    """The terminal `TerminalTransport` for the shared fleet — cross-pod over Redis and the blob."""

    url: str
    blob: BlobStore
    _clients: dict[asyncio.AbstractEventLoop, Redis] = field(default_factory=dict, compare=False)
    _holds: dict[UUID, _Hold] = field(default_factory=dict, compare=False)
    _tasks: set[asyncio.Task[None]] = field(default_factory=set, compare=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, compare=False)

    def _client(self) -> Redis:
        """The per-loop client, with the socket bounded explicitly. `redis>=5` pins no upper bound
        and `socket_timeout` defaults to `None` on redis-py 5/6/7 — a half-open socket on a blocking
        XREAD would then block the sender or the held stream forever. `SOCKET_TIMEOUT_SECONDS`
        exceeds every `block=` used here, so a legitimately idle blocking read still returns on its
        own `block` window while a truly stuck socket raises `TimeoutError` the loops recover from
        by reconnecting."""
        loop = asyncio.get_running_loop()
        client = self._clients.get(loop)
        if client is None:
            client = Redis.from_url(
                self.url,
                decode_responses=True,
                socket_timeout=SOCKET_TIMEOUT_SECONDS,
                socket_connect_timeout=SOCKET_CONNECT_TIMEOUT_SECONDS,
            )
            self._clients[loop] = client
        return client

    def _bind_key(self, conversation_id: UUID) -> str:
        return f"term:bind:{conversation_id}"

    def _inflight_key(self, conversation_id: UUID) -> str:
        return f"term:inflight:{conversation_id}"

    def _op_stream(self, conversation_id: UUID) -> str:
        return f"term:op:{conversation_id}"

    def _reply_stream(self, op_id: str) -> str:
        return f"term:reply:{op_id}"

    def _lock_key(self, conversation_id: UUID) -> str:
        return f"term:lock:{conversation_id}"

    def _deliv_key(self, op_id: str) -> str:
        return f"term:deliv:{op_id}"

    def _opmeta_key(self, op_id: str) -> str:
        return f"term:opmeta:{op_id}"

    def _body_blob(self, op_id: str) -> str:
        return f"term/op/{op_id}"

    def _reply_blob(self, op_id: str) -> str:
        return f"term/reply/{op_id}"

    def connect(
        self,
        conversation_id: UUID,
        cwd: str,
        member_id: UUID | None,
        runtime_id: str | None = None,
    ) -> None:
        """Publish this pod's held connection as the conversation's binding, refreshed under a TTL
        by a background heartbeat for the connection's life. Called from the held stream on serve's
        loop, so the heartbeat runs there and `disconnect` cancels it; the key expires on its own
        once no pod refreshes it, which is what lets the binding survive the ~1s reconnect gap
        without a delete racing the next pod's publish."""
        loop = asyncio.get_running_loop()
        with self._lock:
            hold = self._holds.get(conversation_id)
            resolved_runtime_id = runtime_id or conversation_id.hex
            if hold is None:
                hold = _Hold(cwd=cwd, member_id=member_id, runtime_id=resolved_runtime_id)
                hold.task = loop.create_task(
                    self._heartbeat(conversation_id, cwd, member_id, resolved_runtime_id)
                )
                self._holds[conversation_id] = hold
            hold.connections += 1

    def disconnect(self, conversation_id: UUID) -> None:
        with self._lock:
            hold = self._holds.get(conversation_id)
            if hold is None:
                return
            hold.connections -= 1
            if hold.connections <= 0:
                if hold.task is not None:
                    hold.task.cancel()
                del self._holds[conversation_id]

    async def _heartbeat(
        self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str
    ) -> None:
        """Refresh the binding key under its TTL while this pod holds the connection. On
        `disconnect` this task is cancelled and simply stops refreshing — it never deletes the key.
        A delete here would race the next pod's publish: the client reconnects on a ~1s poll and its
        new pod republishes the binding, and this pod's cancel could land after that, blanking a
        live terminal's binding for a whole refresh interval. Letting the TTL carry it means a
        binding no pod holds expires on its own, and one a peer holds is never disturbed."""
        payload = _bind_payload(cwd, member_id, runtime_id)
        key = self._bind_key(conversation_id)
        while True:
            with suppress(RedisError):
                await self._client().set(key, payload, ex=BIND_TTL_SECONDS)
            await asyncio.sleep(BIND_REFRESH_SECONDS)

    def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None:
        """The binding as this pod holds it locally — answered without I/O, so the pod that holds
        the connection reads it directly. A pod that never held this conversation returns None here;
        the turn's own path reads the binding over Redis through `arrived`, which is what serves the
        cross-pod open."""
        with self._lock:
            hold = self._holds.get(conversation_id)
            return (
                None
                if hold is None
                else TerminalWorkspace(
                    cwd=hold.cwd, member_id=hold.member_id, runtime_id=hold.runtime_id
                )
            )

    async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None:
        """The bound terminal read from Redis, waiting up to `grace_s` for the member's client to
        connect. The client's stream ends at every hold and reconnects on a ~1s poll, so a turn's
        open landing in that gap is the normal case — the wait is on the member's network, not on
        our own Redis, which is why it polls the binding key rather than failing at once."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + grace_s
        while True:
            bound = await self._read_binding(conversation_id)
            if bound is not None:
                return bound
            if loop.time() >= deadline:
                return None
            await asyncio.sleep(ARRIVAL_POLL_SECONDS)

    async def _read_binding(self, conversation_id: UUID) -> TerminalWorkspace | None:
        """The binding Redis holds: the connection pod's liveness key, or the copy the in-flight op
        pinned. The pin is what a long op needs — the client's held stream ends the moment it
        renders the directive, so the liveness key is gone for the whole of a build or a test run,
        and a second accessor (an off-turn attachment write, a subagent's op) would find no terminal
        where the in-process transport keeps the binding and queues behind the op."""
        client = self._client()
        raw = await client.get(self._bind_key(conversation_id))
        if raw is None:
            raw = await client.get(self._inflight_key(conversation_id))
        if raw is None:
            return None
        data = json.loads(_text(raw))
        member = data.get("member_id")
        return TerminalWorkspace(
            cwd=data["cwd"],
            member_id=UUID(member) if member else None,
            runtime_id=data.get("runtime_id", conversation_id.hex),
        )

    async def send(
        self,
        conversation_id: UUID,
        kind: str,
        timeout_s: int,
        name: str = "",
        arg: str = "",
        params: str = "",
        body: bytes | None = None,
    ) -> bytes:
        """Ask the conversation's terminal to run one op and answer its reply, raising
        `TerminalGone` when none is bound or the bound one stops answering. Two waits, each with its
        own budget, mirroring the in-process transport's separate turn-wait and op-wait: acquiring
        the per-conversation lock is bounded by its own `blocking_timeout` (a queued op waits out
        the op ahead of it), and only then does a fresh `wait_for` enclose the post-lock work — the
        copy-in blob put, the XADD, and the reply wait — so a queued op gets its full reply budget
        however long it waited for the lock, rather than a ceiling the lock wait already spent. Each
        phase ends, so the whole `send` ends. Any Redis fault becomes `TerminalGone` rather than
        escaping raw. Tear-down does not rest on this call's `finally`: every key an op owns expires
        at its window and any reader reaps the entry past it, so an op outlives a sender that dies
        mid-flight only until that window and never re-runs on the member's machine."""
        bound = await self.arrived(conversation_id, ARRIVAL_GRACE_SECONDS)
        if bound is None:
            raise TerminalGone("no terminal is connected to this conversation")
        op = TerminalOp(
            op_id=uuid4().hex,
            kind=kind,
            timeout_s=timeout_s,
            name=name,
            arg=arg,
            params=params,
        )
        deadline_s = timeout_s + OP_DEADLINE_SLACK_SECONDS
        lock = self._client().lock(
            self._lock_key(conversation_id),
            timeout=deadline_s + LOCK_TTL_MARGIN_SECONDS,
            blocking=True,
            blocking_timeout=deadline_s,
        )
        try:
            acquired = await lock.acquire()
        except RedisError as error:
            raise TerminalGone("the terminal rendezvous is unreachable") from error
        if not acquired:
            raise TerminalGone(f"the terminal did not free up within {timeout_s:.0f}s")
        try:
            return await asyncio.wait_for(
                self._run_op(conversation_id, op, body, bound, deadline_s),
                deadline_s + HARD_MARGIN_SECONDS,
            )
        except TimeoutError as error:
            raise TerminalGone(f"the terminal did not answer within {timeout_s:.0f}s") from error
        except RedisError as error:
            raise TerminalGone("the terminal rendezvous is unreachable") from error
        finally:
            with suppress(RedisError):
                await lock.release()

    async def _run_op(
        self,
        conversation_id: UUID,
        op: TerminalOp,
        body: bytes | None,
        bound: TerminalWorkspace,
        deadline_s: float,
    ) -> bytes:
        """The post-lock work under its own fresh deadline, the caller holding the per-conversation
        lock: pin the binding, stage the copy-in body, XADD the op, then await the reply. The op
        pins the binding for its own window because the client's held stream ends as soon as it
        renders the directive — an op that runs longer than the arrival grace would otherwise leave
        a concurrent accessor finding no terminal bound, where a terminal that runs one op at a time
        should queue behind this one. A cancel of the enclosing `wait_for` leaves tear-down to the
        op's window and the keys' TTLs."""
        window = op.timeout_s + OP_WINDOW_MARGIN_SECONDS
        entry_id: str | None = None
        try:
            client = self._client()
            meta = json.dumps(
                {
                    "conv": conversation_id.hex,
                    "member": bound.member_id.hex if bound.member_id else "",
                }
            )
            await client.set(self._opmeta_key(op.op_id), meta, ex=window)
            await client.set(
                self._inflight_key(conversation_id),
                _bind_payload(bound.cwd, bound.member_id, bound.runtime_id),
                ex=window,
            )
            if body is not None:
                await asyncio.wait_for(
                    self.blob.put(self._body_blob(op.op_id), body), BLOB_OP_TIMEOUT_SECONDS
                )
            entry_id = _text(
                await client.xadd(
                    self._op_stream(conversation_id),
                    self._op_fields(op),
                    maxlen=OP_STREAM_MAXLEN,
                    approximate=True,
                )
            )
            await client.expire(self._op_stream(conversation_id), OP_STREAM_TTL_SECONDS)
            return await self._await_reply(op.op_id, deadline_s, op.timeout_s)
        finally:
            await self._clear_op(conversation_id, op.op_id, entry_id)

    def _op_fields(self, op: TerminalOp) -> dict[FieldT, EncodableT]:
        return {
            "op_id": op.op_id,
            "kind": op.kind,
            "timeout_s": str(op.timeout_s),
            "name": op.name,
            "arg": op.arg,
            "params": op.params,
        }

    def _decode_op(self, fields: _StreamFields) -> TerminalOp:
        return TerminalOp(
            op_id=_text(fields["op_id"]),
            kind=_text(fields["kind"]),
            timeout_s=int(_text(fields["timeout_s"])),
            name=_text(fields.get("name", "")),
            arg=_text(fields.get("arg", "")),
            params=_text(fields.get("params", "")),
        )

    async def _await_reply(self, op_id: str, deadline_s: float, timeout_s: int) -> bytes:
        stream = self._reply_stream(op_id)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + deadline_s
        last = "0"
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TerminalGone(f"the terminal did not answer within {timeout_s:.0f}s")
            block = min(max(int(remaining * 1000), MIN_BLOCK_MS), REPLY_BLOCK_MS)
            try:
                entries = _stream_entries(
                    await self._client().xread({stream: last}, count=1, block=block)
                )
            except RedisTimeoutError:
                continue
            except RedisError as error:
                raise TerminalGone("the terminal rendezvous is unreachable") from error
            if not entries:
                continue
            _entry_id, fields = entries[0]
            if fields is None:
                continue
            return await self._decode_reply(op_id, fields)

    async def _decode_reply(self, op_id: str, fields: _StreamFields) -> bytes:
        failed = fields.get("failed")
        if failed is not None:
            raise TerminalOpFailed(_text(failed))
        if fields.get("blob") == "1":
            try:
                return await asyncio.wait_for(
                    self.blob.get(self._reply_blob(op_id)), BLOB_OP_TIMEOUT_SECONDS
                )
            except BlobNotFound as error:
                raise TerminalGone("the terminal's reply body was not stored") from error
            except TimeoutError as error:
                raise TerminalGone("the terminal's reply body could not be read") from error
        return base64.b64decode(_text(fields.get("b64", "")))

    async def next_op(self, conversation_id: UUID, exclude_op_id: str | None = None) -> TerminalOp:
        """The next op asked of this conversation's terminal, awaited by the held stream that
        renders it. One Lua script reads-and-claims atomically (scan oldest-first on Redis's own
        clock, reap entries past their window, skip the reply POST's excluded op, claim the first
        unclaimed one for its whole window), so a reconnecting pod re-renders only an op no pod has
        delivered — an `exec` never runs twice, and a second held stream on the same conversation
        cannot re-render one the first is still executing. When nothing is claimable it blocks for a
        newer entry, then re-scans; a Redis fault raises `TerminalGone`, ending the stream."""
        stream = self._op_stream(conversation_id)
        while True:
            try:
                result = await self._client().eval(
                    _NEXT_OP_LUA,
                    1,
                    stream,
                    str(OP_WINDOW_MARGIN_SECONDS),
                    exclude_op_id or "",
                    DELIV_PREFIX,
                )
            except RedisTimeoutError:
                continue
            except RedisError as error:
                raise TerminalGone("the terminal rendezvous is unreachable") from error
            if _text(result[0]) == "ok":
                return self._decode_op(_pairs(result[2]))
            last = _text(result[1])
            try:
                await self._client().xread({stream: last}, count=1, block=OP_BLOCK_MS)
            except RedisTimeoutError:
                continue
            except RedisError as error:
                raise TerminalGone("the terminal rendezvous is unreachable") from error

    async def staged(
        self, conversation_id: UUID, op_id: str, member_id: UUID | None = None
    ) -> bytes | None:
        """The copy-in body the in-flight op stages, fetched from the blob for the read projection
        serving it — only while the op is in flight, only for the member the binding named, and only
        in the conversation the op belongs to. The gate fails closed: a missing or mismatched opmeta
        serves nothing. Any pod can serve it: the bytes are the blob's, keyed by op id."""
        meta = await self._client().get(self._opmeta_key(op_id))
        if meta is None or not self._gate_ok(meta, conversation_id, member_id):
            return None
        try:
            return await asyncio.wait_for(
                self.blob.get(self._body_blob(op_id)), BLOB_OP_TIMEOUT_SECONDS
            )
        except (BlobNotFound, TimeoutError):
            return None

    def resolve(
        self,
        conversation_id: UUID,
        op_id: str,
        reply: bytes,
        failed: str | None = None,
        member_id: UUID | None = None,
    ) -> bool:
        """Answer the in-flight op — schedule the XADD to the reply stream on the running loop and
        return at once, because the reply POST route must not block on Redis. The waiting `send`'s
        own deadline is the wait that always ends, so a delayed or dropped delivery times the
        sender out rather than wedging it. Reports True: whether pod T still waits is its XREAD's to
        decide, pod-agnostically, and the reply POST cannot see across the fleet."""
        loop = asyncio.get_running_loop()
        self._spawn(self._deliver_reply(conversation_id, op_id, reply, failed, member_id), loop)
        return True

    async def _deliver_reply(
        self,
        conversation_id: UUID,
        op_id: str,
        reply: bytes,
        failed: str | None,
        member_id: UUID | None,
    ) -> None:
        client = self._client()
        meta = await client.get(self._opmeta_key(op_id))
        if meta is None:
            warn("terminal.redis_reply_dropped", op_id=op_id, reason="no op in flight")
            return
        if not self._gate_ok(meta, conversation_id, member_id):
            warn("terminal.redis_reply_dropped", op_id=op_id, reason="member or conversation gate")
            return
        fields: dict[FieldT, EncodableT]
        if failed is not None:
            fields = {"failed": failed}
        elif len(reply) > REPLY_INLINE_MAX_BYTES:
            await asyncio.wait_for(
                self.blob.put(self._reply_blob(op_id), reply), BLOB_OP_TIMEOUT_SECONDS
            )
            fields = {"blob": "1"}
        else:
            fields = {"b64": base64.b64encode(reply).decode()}
        await client.xadd(
            self._reply_stream(op_id), fields, maxlen=REPLY_STREAM_MAXLEN, approximate=True
        )
        await client.expire(self._reply_stream(op_id), REPLY_STREAM_TTL_SECONDS)

    def _gate_ok(
        self, meta_raw: bytes | str, conversation_id: UUID, member_id: UUID | None
    ) -> bool:
        """The reply/copy-in gate, matching the in-process transport and failing closed. The op must
        belong to this conversation, and a request that names a member must name the member the
        binding did — a named requester against a binding that named no member (an unlinked email)
        is refused, never admitted by an empty string short-circuiting the check."""
        data = json.loads(_text(meta_raw))
        if data.get("conv") != conversation_id.hex:
            return False
        expected = UUID(data["member"]) if data.get("member") else None
        return member_id is None or expected == member_id

    def in_flight(self, conversation_id: UUID) -> TerminalOp | None:
        """The op a turn awaits is on the turn's pod, not this one, so a cross-pod transport has no
        local view of it — an operator read answers None rather than a partial one."""
        return None

    async def _clear_op(self, conversation_id: UUID, op_id: str, entry_id: str | None) -> None:
        """Tear the op down whole: XDEL the op entry, then drop the markers, the reply stream, and
        the copy blobs. Exactly-once does not rest on this ordering — the atomic claim script and
        the window-long delivery marker are what forbid a re-render — so this is orderly cleanup,
        not the safety, and it is best-effort: a missed Redis delete reaps via the op's-window TTL,
        and each blob delete is bounded so a wedged store cannot push this call — which runs after
        the send's own hard deadline — minutes past the ceiling the turn already answered under."""
        client = self._client()
        with suppress(RedisError):
            if entry_id is not None:
                await client.xdel(self._op_stream(conversation_id), entry_id)
        for key in (
            self._opmeta_key(op_id),
            self._deliv_key(op_id),
            self._inflight_key(conversation_id),
            self._reply_stream(op_id),
        ):
            with suppress(RedisError):
                await client.delete(key)
        for blob_key in (self._body_blob(op_id), self._reply_blob(op_id)):
            with suppress(Exception):
                await asyncio.wait_for(self.blob.delete(blob_key), BLOB_OP_TIMEOUT_SECONDS)

    def _spawn(
        self, coro: Coroutine[object, object, None], loop: asyncio.AbstractEventLoop
    ) -> None:
        """Run a fire-and-return delivery as a tracked task, so a scheduled XADD is not GC'd
        mid-flight and its failure is logged rather than swallowed."""
        task = loop.create_task(self._logged(coro))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _logged(self, coro: Coroutine[object, object, None]) -> None:
        try:
            await coro
        except Exception as error:
            warn("terminal.redis_delivery_failed", error=str(error))
