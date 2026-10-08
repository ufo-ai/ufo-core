from pathlib import Path

import yaml

WORKFLOW = Path(__file__).parents[3] / ".github" / "workflows" / "integration.yaml"
PULL_START = "Start pulling the published sandbox image"
PULL_JOIN = "Reuse the published sandbox image"
TESTS = "make test-integration SHARD=${{ matrix.shard }}"
BINARIES = {
    "client": {
        "prefix": "ufo-client-musl",
        "crate": "client",
        "path": "client/target/x86_64-unknown-linux-musl/release/ufo",
        "builds": (
            "sudo apt-get update && sudo apt-get install -y musl-tools",
            "cargo build --release --target x86_64-unknown-linux-musl",
        ),
        "actions": ("./.github/actions/client-gh",),
    },
}


def _steps() -> list[dict]:
    loaded = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    assert isinstance(loaded, dict)
    steps = loaded["jobs"]["integration"]["steps"]
    assert isinstance(steps, list)
    return steps


def _index(steps: list[dict], **match: str) -> int:
    found = [
        index
        for index, step in enumerate(steps)
        if all(step.get(key) == value for key, value in match.items())
    ]
    assert len(found) == 1, match
    return found[0]


def test_the_image_pull_runs_beside_the_whole_build_prefix() -> None:
    steps = _steps()
    start = _index(steps, name=PULL_START)
    join = _index(steps, name=PULL_JOIN)
    assert steps[start - 1] == {"run": "uv sync"}
    assert join == _index(steps, run=TESTS) - 1
    assert '>"$RUNNER_TEMP/sandbox-pull.log" 2>&1 &' in steps[start]["run"]
    assert 'echo "SANDBOX_PULL_PID=$!" >>"$GITHUB_ENV"' in steps[start]["run"]
    assert steps[join]["if"] == "env.SANDBOX_PULL_IMAGE != ''"
    assert 'tail --pid="$SANDBOX_PULL_PID"' in steps[join]["run"]
    assert not any("SANDBOX_PULL" in str(step) for step in steps[start + 1 : join])


def test_rust_binaries_are_content_keyed_and_built_only_on_a_miss() -> None:
    steps = _steps()
    toolchain = _index(steps, id="toolchain")
    assert steps[toolchain]["uses"] == "dtolnay/rust-toolchain@stable"
    for cache_id, binary in BINARIES.items():
        cache = _index(steps, id=cache_id)
        assert cache > toolchain
        assert steps[cache]["uses"] == "actions/cache@v4"
        assert steps[cache]["with"] == {
            "path": binary["path"],
            "key": (
                f"{binary['prefix']}-${{{{ steps.toolchain.outputs.cachekey }}}}-"
                f"${{{{ hashFiles('{binary['crate']}/**', '!{binary['crate']}/target/**') }}}}"
            ),
        }
        guarded = [
            step
            for step in steps
            if step.get("run") in binary["builds"]
            or step.get("uses") in binary["actions"]
            or (
                str(step.get("uses", "")).startswith("Swatinem/rust-cache@")
                and step["with"]["workspaces"] == binary["crate"]
            )
        ]
        assert len(guarded) == len(binary["builds"]) + len(binary["actions"]) + 1, cache_id
        for step in guarded:
            assert step["if"] == f"steps.{cache_id}.outputs.cache-hit != 'true'", step
            assert steps.index(step) > cache, step
    assert all("if" in step for step in steps if "cargo " in step.get("run", ""))
