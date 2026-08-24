"""The preview service's fixed address and the deploy value that points the proxy at it.

The preview service (RFC 0037) renders shared files and document reads. It answers on
`preview.ufo.internal`: the egress proxy admits that host and relays its TLS-terminated requests to
the service. A share carries a single-key PUT capability; a read carries a fixed sentinel bearer
that the proxy replaces with the deploy's preview token only for this host. The real token never
enters the sandbox. The host is admitted whatever the agent's internet policy, because rendering a
file the sandbox already holds reaches nothing public. These are the two ends the rest of the
system shares: the host the proxy recognizes, and the address the deploy relays it to."""

PREVIEW_HOST = "preview.ufo.internal"
PREVIEW_AUTH_HEADER = "authorization"
PREVIEW_SENTINEL = "ufo-preview-token-sentinel"


def parse_preview_service(value: str | None) -> tuple[str, int] | None:
    """The `host:port` of the preview service, or None when the deploy runs none. Fail loud on a
    malformed value — it is deploy config, so a bad address is a deploy bug, not a fallback to
    off."""
    if value is None:
        return None
    host, separator, port = value.rpartition(":")
    if not separator or not host:
        raise ValueError(f"preview_service must be host:port, got {value!r}")
    return host, int(port)
