# ufo-egress

`ufo-egress` is the proxy that every byte of sandbox network traffic passes through. An agent's
sandbox has no direct route to the internet: it opens a proxy tunnel here, and this program decides
whether that host may be reached, then carries the traffic if it may.

The proxy owns the wire and none of the policy. It verifies the signed token the sandbox presents,
asks the control plane what that token is allowed to reach, and enforces the answer on the
connection: refuse the host, tunnel it untouched, or terminate TLS with a certificate it mints for
the host and add the credential the caller is entitled to on the way out. It also pins the address
it dialed, reports the usage back to the control plane for metering, and relays the hosts that
belong to the cache daemon and the preview service to those programs instead of the public internet.
A host the control plane marks residential leaves through the configured residential provider's
gateway instead of the cluster's own address; with no gateway configured that host is answered 502,
because the origin refusing a datacenter address is why the rule named it.

It holds no customer key, no database, and no policy of its own: every secret and every decision
stays in the control plane. A proxy request with no valid token is answered 403, anything other than
a tunnel request is answered 405, and the process drains live tunnels on shutdown before it exits.

## Run it in development

Prerequisite: a stable Rust toolchain.

```bash
cd egress
export UFO_EGRESS_BIND=127.0.0.1
export UFO_EGRESS_PORT=8888
export UFO_TOKEN_SECRET=ufo-local-dev-token-secret
export UFO_EGRESS_CONTROL_URL=http://127.0.0.1:8710
export UFO_EGRESS_CONTROL_TOKEN=ufo-local-dev-egress-token
cargo run
```

The proxy is only useful with the control plane alive: a core `serve` reachable at
`UFO_EGRESS_CONTROL_URL` and holding the same `UFO_EGRESS_CONTROL_TOKEN`, and the same
`UFO_TOKEN_SECRET` the deploy signs sandbox tokens with. Started on its own it boots and answers, but
every tunnel request is refused, which is enough to check that it is listening:

```bash
curl -sS -x http://127.0.0.1:8888 https://example.com
```

`curl` reports `CONNECT tunnel failed, response 403` and exits non-zero, which is the refusal a
request with no valid token is supposed to get.

The realistic development setup is `make stack` from the repository root: it runs Postgres, the
gateway, core `serve`, and this proxy together with matching secrets, which is what makes a request
from inside a sandbox land anywhere.

With no certificate authority configured the process mints a throwaway one at startup, which is fine
for a single local process. Point it at a shared authority when a sandbox has to trust the same one
that core `serve` hands out.

| Variable | What it does |
|---|---|
| `UFO_TOKEN_SECRET` | Secret the sandbox tokens are signed with. Required. |
| `UFO_EGRESS_CONTROL_URL` | Base URL of the control plane the proxy asks for authorization, rules, and metering. Required. |
| `UFO_EGRESS_CONTROL_TOKEN` | Shared secret presented on every control-plane call. Required. |
| `UFO_EGRESS_BIND` | Address to listen on. Defaults to `0.0.0.0`. |
| `UFO_EGRESS_PORT` | Port to listen on. Defaults to an ephemeral port, so set it. |
| `UFO_EGRESS_CA_CERT`, `UFO_EGRESS_CA_KEY` | Certificate authority to mint per-host certificates from. `UFO_EGRESS_CA_CERT_FILE` and `UFO_EGRESS_CA_KEY_FILE` read the same values from files instead. Both unset mints a throwaway authority for this process. |
| `UFO_EGRESS_PUBLIC_URL` | Address a sandbox outside the cluster dials the proxy on. |
| `UFO_EGRESS_CACHE_DAEMON` | `host:port` of the cache daemon. Unset sends the cached hosts to their real origins. |
| `UFO_EGRESS_PREVIEW_DAEMON` | `host:port` of the preview service. Unset answers a render request 502. |
| `UFO_EGRESS_RESIDENTIAL_PROXY` | `http://[user:password@]host:port` of the residential provider's gateway. The hosts that take it are the control plane's `[sandbox] residential_hosts`. Unset answers such a host 502. |
| `UFO_EGRESS_GRACEFUL_SHUTDOWN_SECONDS` | How long live tunnels drain on shutdown. Defaults to 30. |
| `UFO_EGRESS_LOG` | Log filter. Defaults to `info`. Logs are JSON on stdout. |

Configuration is read once at boot, and a missing required value exits with status 2 rather than
starting a proxy that cannot work.

## Run the tests

```bash
cd egress
cargo test
```

That is the whole suite, and it needs no network, no database, and no services. The end-to-end tests
start a stand-in control plane and a local origin on loopback, then drive real tunnel requests
through the proxy and check the refusals, the relayed bytes, the injected credentials, and the
metering it posts back. One test checks the rule format against a fixture produced by the control
plane, so a change on either side that breaks the agreement fails here instead of on a live
deployment.

The static gates, which CI also runs:

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
```
