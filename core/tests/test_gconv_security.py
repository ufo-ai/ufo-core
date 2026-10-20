import os
import subprocess
import uuid
from pathlib import Path

import pytest

from ufo.gconv import DISABLE_COMMAND

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    "path",
    [
        "servers/preview/Dockerfile",
        "servers/preview/testbed.Dockerfile",
        "servers/cache/Dockerfile",
        "servers/egress/Dockerfile",
    ],
)
def test_runtime_builds_enforce_converter_policy(path: str) -> None:
    text = (ROOT / path).read_text().replace("\\\n", " ")
    assert " ".join(f"RUN {DISABLE_COMMAND}".split()) in " ".join(text.split())


@pytest.mark.docker
def test_real_glibc_rejects_disabled_converters_and_keeps_normal_encodings() -> None:
    tag = f"ufo-gconv-test:{uuid.uuid4().hex}"
    base = os.environ.get("UFO_GCONV_TEST_BASE", "debian:trixie-slim")
    dockerfile = f"FROM {base}\nUSER root\nRUN {DISABLE_COMMAND}\n"
    try:
        subprocess.run(
            ["docker", "build", "--network", "none", "-t", tag, "-"],
            input=dockerfile,
            text=True,
            check=True,
            timeout=120,
        )
    finally:
        subprocess.run(["docker", "image", "rm", tag], check=False, capture_output=True)
