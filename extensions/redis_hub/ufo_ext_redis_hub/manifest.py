"""The Redis-hub extension's manifest: one live-frame hub backend and one terminal transport, both
on the same `config.hub.url`.

Selecting `hub.backend = "redis"` swaps core's in-process hub for a Redis Streams hub that fans
frames out across serve instances, which lifts the single-instance boot guard. Selecting
`terminal.backend = "redis"` swaps the in-process terminal rendezvous for one that carries the
member's connected terminal across pods over the same Redis and the fleet's blob store, so a turn
admitted on the pod that does not hold the member's connection still reaches their machine. Both
reuse `config.hub.url`, carried like `database.url`; an unset URL fails loud when the backend is
selected, never silently at the first use."""

from ufo.sdk.hub import Hub
from ufo.sdk.manifest import HubSpec, Manifest, TerminalTransportSpec
from ufo.sdk.terminal import BlobStore, TerminalTransport
from ufo_ext_redis_hub.stream_hub import RedisStreamHub
from ufo_ext_redis_hub.stream_terminal import RedisTerminals

NAME = "redis_hub"
VERSION = "0.1.0"
HUB_BACKEND = "redis"
TERMINAL_BACKEND = "redis"


def _build_hub(url: str | None) -> Hub:
    if url is None:
        raise RuntimeError(
            "hub.url is required for the redis hub backend (e.g. redis://host:6379/0)"
        )
    return RedisStreamHub(url=url)


def _build_terminal(url: str | None, blob: BlobStore) -> TerminalTransport:
    if url is None:
        raise RuntimeError(
            "hub.url is required for the redis terminal transport (e.g. redis://host:6379/0)"
        )
    return RedisTerminals(url=url, blob=blob)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        hubs=(HubSpec(backend=HUB_BACKEND, build=_build_hub),),
        terminal_transports=(
            TerminalTransportSpec(backend=TERMINAL_BACKEND, build=_build_terminal),
        ),
    )
