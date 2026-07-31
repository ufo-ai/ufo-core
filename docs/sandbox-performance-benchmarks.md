# Sandbox performance benchmarks

Filesystem and git timings measured on a live agent sandbox on 2026-07-30, after PR #822 moved
`/workspace` off the s3fs FUSE mount onto local ext4. The timings in issue #811 describe the old
s3fs-backed `/workspace` and are stale: they say a clone of this repository cannot complete, and
that a single file write costs about a second, neither of which is true on the current mount. This
page records what the sandbox actually does now, so the numbers people plan against come from the
filesystem they are running on.

## Environment

| Property | Value |
|---|---|
| Kernel | Linux 6.1.158+ x86_64 |
| CPUs | 2 vCPUs |
| Memory | 976 MiB total, ~658 MiB available at test time |
| `/workspace` filesystem | ext4 on `/dev/vda`, mount options `rw,relatime,discard` |
| FUSE | none over `/workspace` — only the kernel's own `fusectl` on `/sys/fs/fuse/connections` |
| Disk | 25 GB total, 18 GB free (27% used) |
| git | 2.47.3 |

## Filesystem operations

All paths under `/workspace`.

| Operation | Time | Per unit |
|---|---|---|
| Create 300 small files, 100 bytes each | 0.0083 s | ~28 µs/file |
| 20 sequential reads of one 11 KB file | 0.000145 s | ~7 µs/read |
| Single 50 MB write plus `fsync` | 0.0581 s | ~860 MB/s |
| 1,200 `os.stat` calls on existing files | 0.0020 s | — |
| Delete 300 files via recursive `rmtree` | 0.0040 s | — |

## Git clone

Cloning `metalcraftai/ufo` over HTTPS into `/workspace`. Seven full-clone runs:

| Run | Time |
|---|---|
| 1 | 4.14 s |
| 2 | 3.62 s |
| 3 | 8.41 s |
| 4 | 3.61 s |
| 5 | 3.70 s |
| 6 | 3.54 s |
| 7 | 3.80 s |

Median 3.70 s, fastest 3.54 s, slowest 8.41 s

| Operation | Time |
|---|---|
| Shallow clone, `--depth 1` | 2.78 s (single sample) |
| `git status --porcelain` on a warm full clone | 0.057 s (single sample) |

## Repository shape at measurement

| Property | Value |
|---|---|
| HEAD | `adf74645`, 2026-07-30, `tell hosted customers the portal exists (#875)` |
| Commits reachable from HEAD | 918 |
| Tracked files | 1,247 |
| Working tree plus history on disk | 35 MB, of which `.git` is 17 MB |
| `--depth 1` checkout on disk | 23 MB |

## Method notes

- Timings were taken with Python `time.perf_counter()` around each operation.
- The 50 MB write includes `flush()` and `fsync()`, so it reflects durable write cost.
- Each clone run targeted a fresh empty directory, removed between runs, with no local object cache
  and no reference repository, so every run pays full network and checkout cost.
- Single-sample figures are labelled as such rather than averaged.
- These numbers describe one sandbox instance on one day, and will move with host load and network
  conditions.
