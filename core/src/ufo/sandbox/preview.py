"""The preview service's fixed address and the deploy value that points the proxy at it.

The preview service (RFC 0037) renders a shared document into a PNG. It answers on
`preview.ufo.internal`: the egress proxy admits that host and relays its TLS-terminated requests to
the service, which holds no credential of its own — each request carries the single-key capability
core minted for the one PUT that stores the rendered bytes. The host is admitted whatever the
agent's internet policy, because rendering a file the sandbox already holds reaches nothing public:
an agent narrowed off the internet still shares files, and the service fronts no origin, so
admitting it widens nothing. These are the two ends the rest of the system shares: the host the
proxy recognizes, and the address the deploy relays it to."""

PREVIEW_HOST = "preview.ufo.internal"


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
