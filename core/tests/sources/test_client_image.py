import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[3]
MANIFEST = ROOT / "client" / "Cargo.toml"
DOCKERFILE = ROOT / "dev" / "Dockerfile"
ACTION = ROOT / ".github" / "actions" / "client-gh" / "action.yml"
GH_SCRIPT = ROOT / "client" / "scripts" / "build-gh.sh"
BUILD_SCRIPT = ROOT / "client" / "build.rs"
DECLARED = ("bench", "test", "example", "bin")
RELEASE_JOBS = {
    "ci.yaml": ("sandbox-client",),
    "client.yml": ("bench", "build"),
    "deploy-production.yml": ("deploy",),
    "deploy.yml": ("client",),
    "integration.yaml": ("integration",),
    "sandbox-image.yml": ("publish",),
}
INTEGRATION = ROOT / ".github" / "workflows" / "integration.yaml"
CLIENT_PATH_STEP = (
    'echo "$GITHUB_WORKSPACE/client/target/x86_64-unknown-linux-musl/release" >> "$GITHUB_PATH"'
)


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
    assert "build.rs" in copied
    assert "licenses" in copied


def test_every_release_job_builds_the_gh_payload_first() -> None:
    for filename, jobs in RELEASE_JOBS.items():
        workflow = yaml.safe_load((ROOT / ".github" / "workflows" / filename).read_text())
        for name in jobs:
            steps = workflow["jobs"][name]["steps"]
            releases = [
                index
                for index, step in enumerate(steps)
                if "cargo build --release" in step.get("run", "")
                or "cargo codspeed build" in step.get("run", "")
            ]
            assert releases
            for index in releases:
                assert any(
                    step.get("uses") == "./.github/actions/client-gh" for step in steps[:index]
                )


def test_gh_payload_is_pinned_to_the_runtime_that_reads_the_bundle() -> None:
    steps = yaml.safe_load(ACTION.read_text())["runs"]["steps"]
    (setup,) = [step for step in steps if step.get("uses") == "actions/setup-go@v6"]
    assert setup["with"]["go-version"] == "1.27.0"
    script = GH_SCRIPT.read_text()
    assert "github.com/cli/cli/v2/cmd/gh@v2.97.0" in script
    assert "GOTOOLCHAIN=local" in script
    assert "gzip -9" in script
    assert "cargo:rerun-if-changed=" in BUILD_SCRIPT.read_text()
    (build,) = [step for step in steps if step.get("shell") == "bash"]
    assert "cygpath -w" in build["run"]


def test_gh_payload_is_cached_on_the_pins_that_determine_it() -> None:
    """Every pin the payload has — the toolchain, the cli release, the target — is spelled in
    build-gh.sh, so its hash is the key: bumping one moves the key, and a hit skips the toolchain
    install with the cross-compile. A restored archive answers to the same `gzip -t` a built one
    does, so a truncated entry fails here rather than inside the binary that embeds it."""
    steps = yaml.safe_load(ACTION.read_text())["runs"]["steps"]
    (cache,) = [step for step in steps if str(step.get("uses", "")).startswith("actions/cache@")]
    assert cache["with"]["path"] == "${{ runner.temp }}/ufo-gh-${{ inputs.target }}.gz"
    assert cache["with"]["key"] == (
        "ufo-gh-${{ inputs.target }}-${{ hashFiles('client/scripts/build-gh.sh') }}"
    )
    hit = f"steps.{cache['id']}.outputs.cache-hit"
    (setup,) = [step for step in steps if step.get("uses") == "actions/setup-go@v6"]
    assert setup["if"] == f"{hit} != 'true'"
    (build,) = [step for step in steps if step.get("shell") == "bash"]
    assert f'if [ "${{{{ {hit} }}}}" != true ]; then' in build["run"]
    assert 'gzip -t "$archive"' in build["run"]
    assert steps.index(cache) < steps.index(setup) < steps.index(build)


def test_integration_puts_the_client_it_builds_on_path() -> None:
    workflow = yaml.safe_load(INTEGRATION.read_text())
    steps = workflow["jobs"]["integration"]["steps"]
    build = next(
        index
        for index, step in enumerate(steps)
        if step.get("run") == "cargo build --release --target x86_64-unknown-linux-musl"
    )
    path = next(index for index, step in enumerate(steps) if step.get("run") == CLIENT_PATH_STEP)
    test = next(
        index
        for index, step in enumerate(steps)
        if step.get("run", "").startswith("make test-integration")
    )
    assert build < path < test


def test_integration_builds_the_sites_kit_its_tests_read() -> None:
    workflow = yaml.safe_load(INTEGRATION.read_text())
    steps = workflow["jobs"]["integration"]["steps"]
    pnpm = next(step for step in steps if step.get("uses") == "pnpm/action-setup@v4")
    node = next(step for step in steps if step.get("uses") == "actions/setup-node@v4")
    runs = [step.get("run") for step in steps]
    install = runs.index("pnpm -C extensions/web/frontend install --frozen-lockfile")
    build = runs.index(
        "pnpm -C extensions/web/frontend exec vite build --config vite.sdk.config.ts"
    )
    test = next(
        index for index, run in enumerate(runs) if (run or "").startswith("make test-integration")
    )
    assert pnpm["with"]["version"] == "11.24.0"
    assert node["with"] == {
        "node-version": 24,
        "cache": "pnpm",
        "cache-dependency-path": "extensions/web/frontend/pnpm-lock.yaml",
    }
    assert install < build < test
