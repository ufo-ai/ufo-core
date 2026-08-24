import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[3]
MANIFEST = ROOT / "client" / "Cargo.toml"
DOCKERFILE = ROOT / "dev" / "Dockerfile"
DECLARED = ("bench", "test", "example", "bin")


def _client_stage() -> str:
    body = DOCKERFILE.read_text()
    start = body.index("AS client")
    end = body.index("\nFROM ", start)
    return body[start:end]


def _copied_roots() -> set[str]:
    """The directories the client stage puts in the image, by their path under `client/`."""
    copied = set()
    for line in _client_stage().splitlines():
        for source in re.findall(r"^COPY (.+?) \./?", line):
            for path in source.split():
                if path.startswith("client/"):
                    copied.add(path.removeprefix("client/").split("/", 1)[0])
    return copied


def test_the_image_carries_every_target_the_manifest_declares() -> None:
    """Cargo validates every declared target's path when it loads the manifest, before it selects
    one to build. A declared target whose directory the image lacks refuses the whole manifest, so
    the stage fails on a crate it could otherwise build."""
    manifest = tomllib.loads(MANIFEST.read_text())
    copied = _copied_roots()
    for kind in DECLARED:
        for target in manifest.get(kind, []):
            declared = target.get("path", f"{kind}es/{target['name']}.rs")
            root = declared.split("/", 1)[0]
            assert root in copied, (
                f"client/Cargo.toml declares the {kind} {target['name']} at {declared}, "
                f"which dev/Dockerfile's client stage never copies"
            )
