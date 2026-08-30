import hashlib
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from ufo_testsupport import plugin

from sandbox.build_template import (
    CLIENT_STAGE_PATH,
    DOCKER_BASE_IMAGE,
    SYSTEM_SKILLS_STAGE_PATH,
    pod_dockerfile,
)

ROOT = Path(__file__).parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"
IMAGE_KEY_SCRIPT = ROOT / ".github" / "scripts" / "sandbox_image_key.sh"
REUSE_STEP = "Reuse the published sandbox image"
START_STEP = "Start pulling the published sandbox image"
PUBLISH_STEP = "Publish the sandbox image under its definition key"
IMAGE_REPOSITORY_EXPRESSION = "ghcr.io/${{ github.repository_owner }}/ufo-sandbox"
IMAGE_REPOSITORY = "ghcr.io/metalcraftai/ufo-sandbox"
BASE_REPOSITORY = DOCKER_BASE_IMAGE.split(":")[0]
BASE_DIGEST = "sha256:" + "1" * 64
MOVED_DIGEST = "sha256:" + "2" * 64
KEY_LENGTH = 16
WALLED_CALLS = 3
JOIN_WALLS = 1
# Every input the image key moves with, as the publisher's `paths` names them.
KEY_INPUTS = frozenset(
    {
        "sandbox/build_template.py",
        "client/**",
        "core/src/ufo/sandbox/client_binary.py",
        "core/src/ufo/sandbox/containment.py",
        "core/src/ufo/skills/**",
        "extensions/e2b/ufo_ext_e2b.py",
        "extensions/**/skills/**",
        "packs/**/skills/**",
        "uv.lock",
        ".github/scripts/sandbox_image_key.sh",
        ".github/workflows/sandbox-image.yml",
    }
)
HANG_SECONDS = "10"
MISS_NOTICE = "no published image for {} — the fixture builds it"
UNDERIVED_NOTICE = MISS_NOTICE.format("an underivable key")
DERIVED_NOTICE = MISS_NOTICE.format(f"[0-9a-f]{{{KEY_LENGTH}}}")
RENDERED = f"""# syntax=docker/dockerfile:1
ARG UNRELATED=1
FROM {DOCKER_BASE_IMAGE}
USER root
RUN echo one layer
"""

HANG = """_hang() {
  [ "${1:-0}" = 0 ] && return 0
  sleep "$1"
}
"""

UV_STUB = f"""#!/bin/sh
{HANG}_hang "${{UV_SLEEP:-0}}"
[ "${{UV_EXIT:-0}}" = 0 ] || exit "$UV_EXIT"
printf '%s\n' "$*" >>"$UV_CALLS"
case "$*" in
  *stage_client_binary*)
    mkdir -p "$(dirname "$STAGED_CLIENT")"
    : >"$STAGED_CLIENT"
    : >"$STAGED_SKILLS"
    ;;
  *) cat "$RENDERED_DOCKERFILE" ;;
esac
"""

DOCKER_STUB = f"""#!/bin/sh
{HANG}printf '%s\\n' "$*" >>"$DOCKER_CALLS"
case "$*" in
  *"imagetools inspect"*) printf '%s\\n' "$BASE_DIGEST" ;;
  "login"*) cat >/dev/null; _hang "${{LOGIN_SLEEP:-0}}" ;;
  "pull"*) _hang "${{PULL_SLEEP:-0}}"; exit "${{PULL_EXIT:-0}}" ;;
  "manifest inspect"*) exit "${{MANIFEST_EXIT:-1}}" ;;
  "image inspect"*) sleep "${{INSPECT_SLEEP:-0}}"; exit "${{INSPECT_EXIT:-0}}" ;;
  "build"*)
    [ -f "$STAGED_CLIENT" ] || exit 91
    [ -f "$STAGED_SKILLS" ] || exit 92
    cat >/dev/null
    ;;
esac
"""


def _sandbox(tmp_path: Path, name: str = "run", rendered: str = RENDERED) -> Path:
    root = tmp_path / name
    binaries = root / "bin"
    binaries.mkdir(parents=True)
    (binaries / "uv").write_text(UV_STUB)
    (binaries / "docker").write_text(DOCKER_STUB)
    for stub in binaries.iterdir():
        stub.chmod(0o755)
    (root / "rendered").write_text(rendered)
    return root


def _env(root: Path, **overrides: str) -> dict[str, str]:
    return {
        "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
        "RENDERED_DOCKERFILE": str(root / "rendered"),
        "DOCKER_CALLS": str(root / "docker-calls"),
        "UV_CALLS": str(root / "uv-calls"),
        "STAGED_CLIENT": str(root / "sandbox" / "artifacts" / "ufo"),
        "STAGED_SKILLS": str(root / "sandbox" / "artifacts" / "system-skills.zip"),
        "BASE_DIGEST": BASE_DIGEST,
        **overrides,
    }


def _export(
    monkeypatch: pytest.MonkeyPatch, root: Path, prebuilt: str | None = None, **overrides: str
) -> None:
    """The fixture's environment, with the client staging stubbed: what the image bakes comes from a
    compiled crate, and where that binary comes from is proven where it lives — here the question is
    which image the fixture runs."""
    monkeypatch.setattr(plugin, "stage_client_binary", lambda: CLIENT_STAGE_PATH)
    monkeypatch.setattr(plugin, "stage_system_skills", lambda: SYSTEM_SKILLS_STAGE_PATH)
    for name, value in _env(root, **overrides).items():
        monkeypatch.setenv(name, value)
    if prebuilt is None:
        monkeypatch.delenv(plugin.PREBUILT_IMAGE_ENV, raising=False)
    else:
        monkeypatch.setenv(plugin.PREBUILT_IMAGE_ENV, prebuilt)


def _calls(root: Path) -> list[str]:
    recorded = root / "docker-calls"
    return recorded.read_text().splitlines() if recorded.exists() else []


def _uv_calls(root: Path) -> list[str]:
    recorded = root / "uv-calls"
    return recorded.read_text().splitlines() if recorded.exists() else []


def _derive_key(root: Path, **overrides: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(IMAGE_KEY_SCRIPT), str(root / "sandbox.Dockerfile")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=_env(root, **overrides),
    )


def _outcome(call: Callable[[], object]) -> str:
    """What actually happened, named. `pytest.raises` cannot see a skip — `Skipped` derives from
    `BaseException` — so a call that skipped where it must fail would report green."""
    try:
        call()
    except BaseException as raised:
        return f"{type(raised).__name__}: {raised}"
    return "returned"


def _workflow(name: str) -> dict[str, object]:
    loaded = yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)
    assert isinstance(loaded, dict)
    return loaded


def _step(workflow: str, name: str) -> dict[str, object]:
    jobs = _workflow(workflow)["jobs"]
    found = [step for job in jobs.values() for step in job["steps"] if step.get("name") == name]
    assert len(found) == 1
    return found[0]


def _run_script(script: str, root: Path, **overrides: str) -> subprocess.CompletedProcess[str]:
    if "timeout " in script and shutil.which("timeout") is None:
        pytest.skip("coreutils timeout is absent, so the step's walls would not run at all")
    return subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=_env(
            root,
            RUNNER_TEMP=str(root),
            GITHUB_ENV=str(root / "github-env"),
            GH_TOKEN="registry-token",
            IMAGE_REPOSITORY=IMAGE_REPOSITORY,
            REGISTRY_USER="publisher",
            **overrides,
        ),
    )


def _walled(script: str, walls: int, name: str) -> str:
    shortened, walled = re.subn(r"timeout \d+ ", "timeout 1 ", script)
    assert walled == walls, f"{name} walls {walled} calls, not {walls}"
    return shortened


def _run_step(
    workflow: str, name: str, root: Path, *, shorten_walls: bool = False, **overrides: str
) -> subprocess.CompletedProcess[str]:
    step = _step(workflow, name)
    script = step["run"]
    assert "bash .github/scripts/sandbox_image_key.sh" in script
    assert step["env"]["IMAGE_REPOSITORY"] == IMAGE_REPOSITORY_EXPRESSION
    if shorten_walls:
        script = _walled(script, WALLED_CALLS, name)
    return _run_script(script, root, **overrides)


def _exported(root: Path) -> dict[str, str]:
    recorded = root / "github-env"
    if not recorded.exists():
        return {}
    return dict(line.split("=", 1) for line in recorded.read_text().splitlines())


def _run_pull(
    root: Path, *, shorten_walls: bool = False, **overrides: str
) -> subprocess.CompletedProcess[str]:
    started = _run_step(
        "integration.yaml", START_STEP, root, shorten_walls=shorten_walls, **overrides
    )
    exported = _exported(root)
    if started.returncode != 0 or "SANDBOX_PULL_IMAGE" not in exported:
        return started
    join = _step("integration.yaml", REUSE_STEP)
    assert join["if"] == "env.SANDBOX_PULL_IMAGE != ''"
    script = _walled(join["run"], JOIN_WALLS, REUSE_STEP) if shorten_walls else join["run"]
    joined = _run_script(script, root, **exported, **overrides)
    return subprocess.CompletedProcess(
        joined.args,
        joined.returncode,
        started.stdout + joined.stdout,
        started.stderr + joined.stderr,
    )


def test_the_key_pins_the_rendered_base_wherever_its_from_line_sits(tmp_path: Path) -> None:
    root = _sandbox(tmp_path)

    derived = _derive_key(root)

    assert derived.returncode == 0, derived.stderr
    pinned = (root / "sandbox.Dockerfile").read_text()
    assert pinned.splitlines() == [
        "# syntax=docker/dockerfile:1",
        "ARG UNRELATED=1",
        f"FROM {BASE_REPOSITORY}@{BASE_DIGEST}",
        "USER root",
        "RUN echo one layer",
    ]
    assert derived.stdout.strip() == hashlib.sha256(pinned.encode()).hexdigest()[:KEY_LENGTH]


def test_a_digest_pinned_base_resolves_to_one_digest(tmp_path: Path) -> None:
    root = _sandbox(
        tmp_path, rendered=RENDERED.replace(DOCKER_BASE_IMAGE, f"{BASE_REPOSITORY}@{MOVED_DIGEST}")
    )

    derived = _derive_key(root)

    assert derived.returncode == 0, derived.stderr
    assert f"FROM {BASE_REPOSITORY}@{BASE_DIGEST}\n" in (root / "sandbox.Dockerfile").read_text()


def test_a_moved_base_moves_the_key(tmp_path: Path) -> None:
    root = _sandbox(tmp_path)

    before = _derive_key(root)
    after = _derive_key(root, BASE_DIGEST=MOVED_DIGEST)

    assert before.returncode == 0, before.stderr
    assert after.returncode == 0, after.stderr
    assert before.stdout.strip() != after.stdout.strip()


@pytest.mark.parametrize(
    ("rendered", "digest", "message"),
    [
        (RENDERED.replace(f"FROM {DOCKER_BASE_IMAGE}\n", ""), BASE_DIGEST, "found 0"),
        (RENDERED + f"FROM {DOCKER_BASE_IMAGE}\n", BASE_DIGEST, "found 2"),
        (
            RENDERED.replace(DOCKER_BASE_IMAGE, f"{DOCKER_BASE_IMAGE} AS base"),
            BASE_DIGEST,
            "unexpected FROM line",
        ),
        (RENDERED, "<no value>", "unexpected base digest"),
    ],
)
def test_a_definition_the_script_cannot_pin_fails_loud(
    tmp_path: Path, rendered: str, digest: str, message: str
) -> None:
    root = _sandbox(tmp_path, rendered=rendered)

    derived = _derive_key(root, BASE_DIGEST=digest)

    assert derived.returncode != 0
    assert message in derived.stderr
    assert derived.stdout == ""
    assert not (root / "sandbox.Dockerfile").exists()


def test_a_failed_render_leaves_no_key(tmp_path: Path) -> None:
    root = _sandbox(tmp_path)

    derived = _derive_key(root, UV_EXIT="3")

    assert derived.returncode != 0
    assert derived.stdout == ""


def test_the_rendered_definition_carries_the_one_tagged_base_the_script_pins() -> None:
    """The script's fail-loud arms only hold the SDK to a shape it currently renders: exactly one
    tagged `FROM`, on a line the pinning rewrites whole."""
    assert [line for line in pod_dockerfile().splitlines() if line.startswith("FROM ")] == [
        f"FROM {DOCKER_BASE_IMAGE}"
    ]


def test_every_docker_gated_test_takes_the_image_from_the_fixture() -> None:
    """No test resolves the sandbox image for itself. The fixture is the one place that reads
    `UFO_SANDBOX_TEST_IMAGE`, and the one place that builds an image when it is unset — so a test
    that reads the variable by hand carries its own fallback, and a fallback is a tag nothing here
    builds. Three sites in the sites lifecycle proof did exactly that, naming `ufo-sandbox:latest`,
    and passed for as long as the integration job's opportunistic pull kept hitting; the first miss
    on main ran `docker run` against an image no runner had.

    The fixture lives in the globally registered plugin rather than a conftest, which is what lets a
    test outside `core/tests` ask for it at all."""
    assert callable(plugin.sandbox_image)
    tests = [
        path
        for path in ROOT.glob("**/tests/**/*.py")
        if ".venv" not in path.parts and "node_modules" not in path.parts
    ]
    assert tests, "no test files found to check"
    borrowed = [
        path.relative_to(ROOT).as_posix()
        for path in tests
        if plugin.PREBUILT_IMAGE_ENV in path.read_text() and path != Path(__file__)
    ]
    assert borrowed == [], (
        "these tests read the prebuilt-image variable themselves instead of taking the "
        f"`sandbox_image` fixture: {borrowed}"
    )


def test_the_consumer_names_the_image_the_publisher_pushed(tmp_path: Path) -> None:
    """Both ends of `UFO_SANDBOX_TEST_IMAGE` over one definition: the publisher pushes a tag, the
    integration step writes that same tag under the name the fixture reads."""
    publisher = _sandbox(tmp_path, "publisher")
    consumer = _sandbox(tmp_path, "consumer")

    published = _run_step("sandbox-image.yml", PUBLISH_STEP, publisher)
    consumed = _run_pull(consumer)

    assert published.returncode == 0, published.stderr
    assert consumed.returncode == 0, consumed.stderr
    pushed = [call.removeprefix("push ") for call in _calls(publisher) if call.startswith("push ")]
    assert pushed == [f"{IMAGE_REPOSITORY}:{_derive_key(publisher).stdout.strip()}"]
    assert _exported(consumer)[plugin.PREBUILT_IMAGE_ENV] == pushed[0]
    assert f"pull -q {pushed[0]}" in _calls(consumer)
    assert sum("stage_client_binary" in call for call in _uv_calls(publisher)) == 1
    assert sum("stage_system_skills" in call for call in _uv_calls(publisher)) == 1


@pytest.mark.parametrize("miss", [{"UV_EXIT": "3"}, {"PULL_EXIT": "1"}])
def test_a_miss_leaves_the_integration_job_its_tests(tmp_path: Path, miss: dict[str, str]) -> None:
    """A step that cannot name a published image must still exit 0: it runs before pytest in the
    same job, so failing it would take the Postgres and live-turn tests down with the pull."""
    root = _sandbox(tmp_path)

    consumed = _run_pull(root, **miss)

    assert consumed.returncode == 0, consumed.stderr
    assert "the fixture builds it" in consumed.stdout
    assert plugin.PREBUILT_IMAGE_ENV not in _exported(root)


@pytest.mark.parametrize(
    ("stalled", "reached", "notice"),
    [
        ({"UV_SLEEP": HANG_SECONDS}, [], UNDERIVED_NOTICE),
        ({"LOGIN_SLEEP": HANG_SECONDS}, ["buildx", "login"], DERIVED_NOTICE),
        ({"PULL_SLEEP": HANG_SECONDS}, ["buildx", "login", "pull"], DERIVED_NOTICE),
    ],
    ids=["derivation", "login", "pull"],
)
def test_a_call_that_never_returns_ends_as_a_miss(
    tmp_path: Path, stalled: dict[str, str], reached: list[str], notice: str
) -> None:
    """Every call the step makes off the runner is walled, so a stalled one costs a build and not
    the Postgres, serial and live-turn tests the same job runs after it. The walls run at their real
    lengths in CI; here each is rewritten to a second, and the rewrite counts them, so dropping one
    fails this test rather than leaving it green. Each parameter names the calls the step must have
    made, so a stall that ends at an earlier wall than the one it aims at cannot pass for it."""
    root = _sandbox(tmp_path)

    started = time.monotonic()
    consumed = _run_pull(root, shorten_walls=True, **stalled)
    elapsed = time.monotonic() - started

    assert consumed.returncode == 0, consumed.stderr
    assert re.search(notice, consumed.stdout), consumed.stdout
    assert [call.split()[0] for call in _calls(root)] == reached
    assert plugin.PREBUILT_IMAGE_ENV not in _exported(root)
    assert elapsed < float(HANG_SECONDS) / 2, elapsed


def test_the_publisher_fails_when_the_key_cannot_be_derived(tmp_path: Path) -> None:
    root = _sandbox(tmp_path)

    published = _run_step("sandbox-image.yml", PUBLISH_STEP, root, UV_EXIT="3")

    assert published.returncode != 0
    assert not [call for call in _calls(root) if call.startswith(("build ", "push "))]


def test_the_publisher_skips_a_key_it_already_published(tmp_path: Path) -> None:
    root = _sandbox(tmp_path)

    published = _run_step("sandbox-image.yml", PUBLISH_STEP, root, MANIFEST_EXIT="0")

    assert published.returncode == 0, published.stderr
    assert "already published" in published.stdout
    assert not [call for call in _calls(root) if call.startswith(("build ", "push "))]
    assert not any("stage_client_binary" in call for call in _uv_calls(root))
    assert not any("stage_system_skills" in call for call in _uv_calls(root))


def test_integration_names_the_musl_client_it_builds() -> None:
    job = _workflow("integration.yaml")["jobs"]["integration"]
    named = "client/target/x86_64-unknown-linux-musl/release/ufo"
    assert job["env"]["UFO_CLIENT_BINARY"] == named
    builds = [
        step
        for step in job["steps"]
        if step.get("working-directory") == "client" and "cargo build" in step.get("run", "")
    ]
    assert len(builds) == 1
    assert "--target x86_64-unknown-linux-musl" in builds[0]["run"]


def test_every_input_that_moves_the_key_triggers_the_publisher() -> None:
    """The paths list stays pinned exactly, and every file a layer COPYs has to be covered by a
    named key input — a key that moves with no publish behind it makes every PR rebuild the image,
    and a sandbox running an image older than the guard baked into it cannot run a file op at all.

    The staged client and skill archive are build products no trigger can name directly. Their
    tracked sources are the client crate and the three skill trees, which are also what the
    definition digest hashes."""
    triggers = _workflow("sandbox-image.yml")["on"]["push"]["paths"]

    assert set(triggers) == KEY_INPUTS
    staged = {
        str(CLIENT_STAGE_PATH.relative_to(plugin.SANDBOX_CONTEXT_ROOT)),
        str(SYSTEM_SKILLS_STAGE_PATH.relative_to(plugin.SANDBOX_CONTEXT_ROOT)),
    }
    prefixes = tuple(entry.removesuffix("/**") for entry in KEY_INPUTS)
    copied = [line.split()[1] for line in pod_dockerfile().splitlines() if line.startswith("COPY ")]
    assert copied
    assert staged <= set(copied)
    assert {"client/**", "core/src/ufo/skills/**", "extensions/**/skills/**"} <= KEY_INPUTS
    for source in copied:
        assert source in staged or source.startswith(prefixes), source


def test_a_named_prebuilt_image_replaces_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _sandbox(tmp_path)
    _export(monkeypatch, root, prebuilt=f"{IMAGE_REPOSITORY}:abc")

    assert plugin.sandbox_image.__wrapped__() == f"{IMAGE_REPOSITORY}:abc"
    assert _calls(root) == [f"image inspect {IMAGE_REPOSITORY}:abc"]


def test_an_unnamed_image_is_built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _sandbox(tmp_path)
    _export(monkeypatch, root)

    assert plugin.sandbox_image.__wrapped__() == plugin.SANDBOX_TEST_IMAGE
    assert _calls(root) == [
        f"build -t {plugin.SANDBOX_TEST_IMAGE} -f - {plugin.SANDBOX_CONTEXT_ROOT}"
    ]


def test_a_named_image_that_is_absent_fails_and_never_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _sandbox(tmp_path)
    _export(monkeypatch, root, prebuilt=f"{IMAGE_REPOSITORY}:gone", INSPECT_EXIT="1")

    outcome = _outcome(plugin.sandbox_image.__wrapped__)

    assert outcome.startswith("AssertionError: "), outcome
    assert "image inspect" in outcome


def test_a_breached_wall_names_the_call_that_breached_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _sandbox(tmp_path)
    _export(monkeypatch, root, INSPECT_SLEEP="5")

    outcome = _outcome(
        lambda: plugin.docker_or_fail(["docker", "image", "inspect", "ufo-sandbox:test"], timeout=1)
    )

    assert outcome.startswith("AssertionError: "), outcome
    assert "docker image inspect ufo-sandbox:test" in outcome
