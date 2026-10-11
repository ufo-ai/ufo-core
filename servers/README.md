# servers

The standalone Rust services. Each directory is one self-contained crate with its own `Cargo.lock`,
`Dockerfile`, tests, and CI workflow; each README says what its service does.

| Crate      | Binary        | Does                                                                 |
| ---------- | ------------- | -------------------------------------------------------------------- |
| `cache/`   | `ufo-cache`   | caches sandbox git clones and package installs beside the proxy      |
| `preview/` | `ufo-preview` | renders documents to PNG page images and videos to a frame           |
