# Terminal-Bench 3 local benchmark notice

This integration uses Terminal-Bench 3, also published as Frontier-Bench, from
https://github.com/harbor-framework/terminal-bench at release `v3.0.0`. The pinned release asset is
https://github.com/harbor-framework/terminal-bench/releases/download/v3.0.0/tasks.tar.gz
(`sha256:7b035a9768087cd2727925fea1531845497d40412b0cdeed34a9ec9f16c3ff3e`, 453,742,756
bytes).

The local subset contains these upstream tasks:

- `interleaved-vigenere` (`sha256:83a4f0074137ab36629d33f3f4045d62b63b0feb83295ff7cb6636cbedd31bc0`)
- `html-js-filter` (`sha256:832a5b309edca4f1a7c728da5f1ca530c2712f20a0b7f1db6d1bb6e3171a8866`)
- `kv-live-surgery` (`sha256:bb58097aee168627e1eea82a50feaf1e021d502fe18e0c24b4d88e5a88a6f53f`)

Runs use Harbor `0.21.0` from https://github.com/laude-institute/harbor. Terminal-Bench and Harbor
are provided under the Apache License 2.0. Their source and license terms remain with their upstream
projects.

Task archives and extracted task bytes remain local under `.local/terminal_bench/`; none are
committed to this repository.
