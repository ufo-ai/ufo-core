"""The sandbox cache's fixed address and the git config that routes fetches through it.

The cache daemon (RFC 0032) runs on the proxy pod as `cache.ufo.internal`. The egress proxy admits
that host only for an agent that already holds the public internet and relays its requests to the
daemon, which authenticates and isolates per request — so the rewrite a sandbox exports carries no
credential. The daemon mirrors only `CACHE_GIT_HOSTS`; any other host is refused, so the cache
cannot be steered at a private address. These are the two ends the rest of the system shares: the
host the proxy recognizes, and the git config that addresses the cache instead of the origin."""

CACHE_HOST = "cache.ufo.internal"
CACHE_GIT_HOSTS = ("github.com",)

# The public package registries and download CDNs the proxy transparently routes through the cache
# for an internet-holding agent. Unlike git, these need no sandbox config: the proxy intercepts the
# real host, so npm/pip/cargo/go are unchanged and a tarball's own absolute URL still hits the
# cache. The daemon's own UFO_CACHE_PKG_HOSTS allowlist must stay in step with this list.
CACHE_PKG_HOSTS = (
    "registry.npmjs.org",
    "pypi.org",
    "files.pythonhosted.org",
    "crates.io",
    "static.crates.io",
    "index.crates.io",
    "proxy.golang.org",
    "sum.golang.org",
)

CACHE_CALLBACK_HOST = "127.0.0.1"
CACHE_CALLBACK_PORT = 9111
CACHE_CONTROL_TOKEN_ENV = "UFO_CACHE_CONTROL_TOKEN"


def cache_git_config() -> tuple[tuple[str, str], ...]:
    """The git config that routes each cached host's fetches to the cache while keeping pushes
    direct: the `insteadOf` rewrites the fetch URL, and the identity `pushInsteadOf` on the origin
    outranks it for pushes."""
    settings: list[tuple[str, str]] = []
    for host in CACHE_GIT_HOSTS:
        settings.append((f"url.https://{CACHE_HOST}/git/{host}/.insteadOf", f"https://{host}/"))
        settings.append((f"url.https://{host}/.pushInsteadOf", f"https://{host}/"))
    return tuple(settings)


def parse_cache_daemon(value: str | None) -> tuple[str, int] | None:
    """The `host:port` of the local cache daemon, or None when the deploy runs no cache. Fail loud
    on a malformed value — it is deploy config, so a bad address is a deploy bug, not a fallback to
    off."""
    if value is None:
        return None
    host, separator, port = value.rpartition(":")
    if not separator or not host:
        raise ValueError(f"cache_daemon must be host:port, got {value!r}")
    return host, int(port)
