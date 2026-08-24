"""Where the `ufo` binary a sandbox runs comes from.

`ufo fs` and `ufo llm` are verbs on the compiled client, so a carrier that once baked the `sbx` and
`sbxfs` scripts bakes that one binary instead. A script is copied straight out of the source tree; a
binary has to be built before anything can copy it, so this is the single place that answers where
it came from — the local carrier's command PATH and the sandbox image's `/usr/local/bin` both ask
here, and a test that needs a real binary asks the same question rather than inventing a path.

Nothing here builds one. Building belongs to the client's own pipeline, which already cross-builds
one artifact per target, and a build started while a turn runs would spend minutes of that turn on a
toolchain the deploy may not even hold. So an absent binary is an error naming the command that
produces it, never a silent build.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

CLIENT_BINARY_NAME = "ufo"
CLIENT_BINARY_ENV = "UFO_CLIENT_BINARY"
"""An already-built `ufo` to use as is — how a CI job hands over the artifact the client workflow
built for a target, instead of paying for a second build of the same source."""
CLIENT_CRATE_DIR = Path(__file__).resolve().parents[4] / "client"
"""The client crate in this checkout. Core is imported from `core/src`, so the repository root is
four parents up from this module; an install that carries no crate there simply finds no candidate
under it and falls through to the environment override or an installed client."""
CLIENT_BUILD_PROFILES = ("release", "debug")


def client_binary(target: str | None = None) -> Path:
    """The `ufo` binary to put inside a sandbox, or a `RuntimeError` naming how to build one.

    `target` is a Rust target triple, for a caller that needs a binary for a platform other than
    this host: the sandbox image needs a Linux one whichever machine builds the image. Without a
    target this is a binary for the host itself, which is what the local carrier runs as a
    subprocess. A binary named by the environment is taken as given for either — which platform a
    handed artifact is for is the pipeline's own claim, not something this can second-guess.
    """
    override = os.environ.get(CLIENT_BINARY_ENV)
    if override:
        named = Path(override)
        if not named.is_file():
            raise RuntimeError(f"{CLIENT_BINARY_ENV} names no file: {named}")
        return named
    built = CLIENT_CRATE_DIR / "target" if target is None else CLIENT_CRATE_DIR / "target" / target
    for profile in CLIENT_BUILD_PROFILES:
        candidate = built / profile / CLIENT_BINARY_NAME
        if candidate.is_file():
            return candidate
    installed = shutil.which(CLIENT_BINARY_NAME) if target is None else None
    if installed:
        return Path(installed)
    triple = "" if target is None else f" --target {target}"
    raise RuntimeError(
        f"no {CLIENT_BINARY_NAME} binary for the sandbox: build one with `cargo build --release"
        f" --manifest-path client/Cargo.toml{triple}`, or point {CLIENT_BINARY_ENV} at one"
    )
