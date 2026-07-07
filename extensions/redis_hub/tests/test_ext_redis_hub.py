"""The Redis-hub extension's unit proof: the frame wire codec round-trips every LiveFrame kind, and
the manifest's build wires the backend from `config.hub.url` (a missing URL fails loud). The live
XADD/XREAD path against a real Redis — every frame kind round-tripped, cursor resume, and MAXLEN
trimming — is `core/tests/integration/test_redis_hub.py`, not here."""

import pytest
import ufo_ext_redis_hub.manifest as ext
from redis.asyncio import Redis
from ufo_ext_redis_hub.stream_hub import (
    RedisStreamHub,
    frame_from_payload,
    frame_payload,
)

from ufo.hub import CostTick, LiveFrame, Parked, SkillLoad, Terminal, ToolCall
from ufo.models.interface import TextDelta
from ufo.schema.records import TerminalFrame

FRAMES: tuple[LiveFrame, ...] = (
    TextDelta(text="hello"),
    Terminal(frame=TerminalFrame(status="done", text="answer", model="claude-opus-4-8")),
    Parked(message="over a spend cap"),
    CostTick(cost_micro_usd=110, tokens=10),
    ToolCall(tool="bash", preview='{"command":"ls"}'),
    SkillLoad(skill="memory"),
)


@pytest.mark.parametrize("frame", FRAMES)
def test_frame_wire_codec_round_trips_every_kind(frame: LiveFrame) -> None:
    assert frame_from_payload(frame_payload(frame)) == frame


def test_manifest_registers_the_redis_hub_backend() -> None:
    manifest = ext.manifest()
    assert {spec.backend for spec in manifest.hubs} == {ext.HUB_BACKEND}


def test_build_requires_a_url() -> None:
    with pytest.raises(RuntimeError, match=r"hub\.url is required"):
        ext._build_hub(None)


def test_build_constructs_a_hub_without_connecting() -> None:
    hub = ext._build_hub("redis://localhost:6379/0")
    assert isinstance(hub, RedisStreamHub)
    assert isinstance(hub.client, Redis)
