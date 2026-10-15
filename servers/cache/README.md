# ufo-cache

`ufo-cache` is the caching daemon in the sandbox egress path. When a sandbox clones a repository or
installs packages, the request reaches this daemon instead of the public internet. The daemon keeps
its own copy on disk, so the next request for the same repository or the same package is answered
from that copy instead of an origin download.

Two kinds of traffic go through it. Git traffic is mirrored per caller: the daemon holds no
credential of its own, and for every git request it asks the control plane for the credential that
belongs to the workspace, the user, and the host named on that request. It can therefore only ever
hold the credential of the caller in front of it, and mirrors are kept apart by that same identity,
so one caller's private mirror is never served to another. Package traffic — public distro
archives, language registries, and artifact download hosts — is anonymous, and cached responses are
shared by everyone.

The daemon fetches allowlisted hosts only. It refuses a request for any other host, so a sandbox
cannot point the cache at a private address. Each part of the on-disk cache carries its own size
ceiling and evicts its oldest entries once it is above it. An optional durable tier — an S3 bucket
or a plain directory — keeps content across restarts, so a daemon that starts with an empty disk
restores instead of re-fetching from the origin.

`GET /_health` answers `ok`. The daemon reads its whole configuration once at boot and exits with
status 2 when a required value is missing, because a deploy launches it, not a person.

## Run it in development

Prerequisites: a stable Rust toolchain, and `git` on `PATH` — the daemon runs `git` itself for the
mirror work.

```bash
cd cache
export UFO_CACHE_LISTEN=127.0.0.1:9110
export UFO_CACHE_STATE=/tmp/ufo-cache-state
export UFO_CACHE_CONTROL_URL=http://127.0.0.1:8710
export UFO_CACHE_CONTROL_TOKEN=dev-cache-token
cargo run
```

In a second terminal:

```bash
curl -s http://127.0.0.1:9110/_health
```

Package requests need nothing else alive. Git requests do: each one asks the control plane for a
credential, so `UFO_CACHE_CONTROL_URL` must point at a running core `serve` that holds the same
value in `UFO_CACHE_CONTROL_TOKEN`. Without it a git request answers 502. Git requests also carry
the workspace identity that the egress proxy stamps on them, so they are driven through the proxy.

| Variable | What it does |
|---|---|
| `UFO_CACHE_LISTEN` | Address the daemon listens on. Required. |
| `UFO_CACHE_STATE` | Directory that holds the mirrors and the cached content. Required. |
| `UFO_CACHE_CONTROL_URL` | Base URL of the control plane the daemon asks for git credentials. Required. |
| `UFO_CACHE_CONTROL_TOKEN` | Shared secret presented on every credential request. Required. |
| `UFO_CACHE_GIT_HOSTS` | Comma-separated git hosts the daemon will mirror. Defaults to `github.com`. |
| `UFO_CACHE_PKG_HOSTS` | Comma-separated package hosts the daemon will cache. Defaults to the public apt, Cargo, RubyGems, Go, npm, and PyPI archives and artifact hosts. |
| `UFO_CACHE_DISK_LIMIT_BYTES` | Size ceiling for the git mirrors. Defaults to 4 GiB. |
| `UFO_CACHE_PKG_DISK_LIMIT_BYTES` | Size ceiling for the package cache. Defaults to 8 GiB. |
| `UFO_CACHE_PACK_CACHE_MB` | Size ceiling in MiB for cached git fetch responses; `0` turns that cache off. Defaults to 4096. |
| `UFO_CACHE_LFS_CACHE_MB` | Size ceiling in MiB for cached large-file objects; `0` turns that cache off. Defaults to 4096. |
| `UFO_CACHE_GIT_FRESH_TTL_SECS` | How long a successful upstream fetch keeps a mirror fresh; `0` fetches on every request. Defaults to 15. |
| `UFO_CACHE_UPSTREAM_SCHEME` | Scheme the daemon reaches upstreams with. Defaults to `https`; tests set `http`. |
| `UFO_CACHE_S3_BUCKET` | Bucket for the durable tier. Unset with no directory below leaves the tier off. |
| `UFO_CACHE_S3_ENDPOINT` | Endpoint for an S3-compatible store instead of AWS. |
| `UFO_CACHE_DURABLE_DIR` | Directory used as the durable tier when no bucket is named. |
| `UFO_CACHE_LOG` | Log filter. Defaults to `info`. Logs are JSON on stdout. |

## Run the tests

```bash
cd cache
cargo test
```

That is the whole suite, and it needs no network and no services: the git tests build repositories
in temporary directories and the package tests serve their own local origin. Both `git` and
`git-lfs` must be on `PATH`; without `git-lfs` the large-file tests fail.

One test exercises the durable tier against a real S3-compatible store. It skips itself when
`UFO_TEST_S3_ENDPOINT` is unset, which is why the plain run above stays green. To include it, start
a store and name it:

```bash
docker run -d --name minio -p 9000:9000 \
  -e MINIO_ROOT_USER=testkey00 -e MINIO_ROOT_PASSWORD=testsecret \
  cgr.dev/chainguard/minio:latest \
  server /data
UFO_TEST_S3_ENDPOINT=http://localhost:9000 AWS_ACCESS_KEY_ID=testkey00 \
  AWS_SECRET_ACCESS_KEY=testsecret AWS_REGION=us-east-1 cargo test
```

The static gates, which CI also runs:

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
```
