"""The Redis-hub extension's manifest: one live-frame hub backend on the core `hubs` seam.

Selecting `hub.backend = "redis"` in config swaps core's in-process hub for a Redis Streams hub that
fans frames out across serve instances, which lifts the single-instance boot guard. The redis URL is
`config.hub.url`, carried like `database.url`; an unset URL fails loud when the backend is selected,
never silently at the first publish."""

from redis.asyncio import Redis

from ufo.sdk.manifest import HubSpec, Manifest
from ufo_ext_redis_hub.stream_hub import RedisStreamHub

NAME = "redis_hub"
VERSION = "0.1.0"
HUB_BACKEND = "redis"


def _build_hub(url: str | None) -> RedisStreamHub:
    if url is None:
        raise RuntimeError(
            "hub.url is required for the redis hub backend (e.g. redis://host:6379/0)"
        )
    return RedisStreamHub(client=Redis.from_url(url, decode_responses=True))


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        hubs=(HubSpec(backend=HUB_BACKEND, build=_build_hub),),
    )
