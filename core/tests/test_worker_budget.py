"""What `-n auto` is allowed to ask for. A worker is a second copy of the application, so the
count the machine affords comes from its memory; sizing by cores is what took a 3931 MB sandbox to
99.4% and stopped it answering."""

from collections.abc import Callable
from pathlib import Path

import pytest
from ufo_testsupport.plugin import (
    MEMORY_PER_WORKER_MB,
    _total_memory_mb,
    pytest_xdist_auto_num_workers,
)

pytest_plugins = ("pytester",)


def _workers(monkeypatch: pytest.MonkeyPatch, cpus: int, total_mb: int | None) -> int:
    monkeypatch.setattr("ufo_testsupport.plugin.os.cpu_count", lambda: cpus)
    monkeypatch.setattr("ufo_testsupport.plugin._total_memory_mb", lambda: total_mb)
    return pytest_xdist_auto_num_workers(config=None)  # type: ignore[arg-type]


def test_the_box_that_died_now_runs_within_its_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """The measured case: four cores and 3931 MB is the sandbox that reached 99.4% and wedged."""
    assert _workers(monkeypatch, cpus=4, total_mb=3931) == 2


def test_ci_keeps_the_parallelism_it_already_had(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shards run `-n auto` on a two-core hosted runner with 7 GB, where the cores bind long
    before the memory does — measured at two workers per shard both before this budget existed and
    after it. Memory is a ceiling, never a licence to exceed the cores present, so the budget must
    cost CI nothing."""
    assert _workers(monkeypatch, cpus=2, total_mb=7 * 1024) == 2


def test_memory_is_only_ever_a_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _workers(monkeypatch, cpus=4, total_mb=64 * 1024) == 4


@pytest.mark.parametrize(
    ("tier", "cpus", "total_mb", "expected"),
    [("small", 2, 1982, 1), ("medium", 4, 3930, 2), ("large", 8, 7955, 5)],
)
def test_what_each_sandbox_tier_gets(
    monkeypatch: pytest.MonkeyPatch, tier: str, cpus: int, total_mb: int, expected: int
) -> None:
    """What this costs the fleet, at the sizes the fleet actually runs — `MemTotal` and `nproc` read
    off a live sandbox of each tier, which come in under the tier's declared memory. Every tier is
    provisioned at 1 GB per core while a worker of this suite needs closer to 1.5 GB, so every tier
    loses workers here: the point is that `small` at two of them, and `medium` at four, do not fit
    and stop answering, and one that never runs is worse than one that runs slowly."""
    assert _workers(monkeypatch, cpus=cpus, total_mb=total_mb) == expected


def test_memory_caps_a_machine_with_cores_to_spare(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _workers(monkeypatch, cpus=32, total_mb=12 * 1024) == 12 * 1024 // MEMORY_PER_WORKER_MB


def test_a_machine_too_small_for_one_worker_still_gets_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero workers is not a run. A box under the budget runs one and pays whatever it pays."""
    assert _workers(monkeypatch, cpus=8, total_mb=512) == 1


def test_an_unreadable_total_leaves_the_core_count_standing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A platform whose memory this cannot see gets the answer it had before — never a guess."""
    assert _workers(monkeypatch, cpus=6, total_mb=None) == 6


def test_the_total_is_read_from_the_running_machine() -> None:
    """The reader is not asserted against a fixture: on Linux it answers, and off it answers None,
    and both are the contract the hook is written to."""
    total = _total_memory_mb()
    assert total is None or total > 0


def test_a_cgroup_ceiling_wins_over_what_the_machine_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A container is told the host's memory. Measured inside a 1 GB container: `/proc/meminfo` says
    7936 MB while the cgroup says 1 GB, so reading only the first would size that box for five
    workers. The smaller of the two is the one this process may actually use."""
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:        8126464 kB\nMemFree:  100 kB\n")
    ceiling = tmp_path / "memory.max"
    ceiling.write_text(str(1024 * 1024 * 1024))
    monkeypatch.setattr("ufo_testsupport.plugin.Path", _rooted(meminfo, ceiling))

    assert _total_memory_mb() == 1024


def test_an_unlimited_cgroup_leaves_the_machine_total_standing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`max` is a cgroup saying it imposes nothing — a sandbox reads exactly this."""
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:        4024320 kB\n")
    ceiling = tmp_path / "memory.max"
    ceiling.write_text("max\n")
    monkeypatch.setattr("ufo_testsupport.plugin.Path", _rooted(meminfo, ceiling))

    assert _total_memory_mb() == 3930


def _rooted(meminfo: Path, ceiling: Path) -> Callable[[str], Path]:
    """Point the reader's two absolute paths at temporary files, leaving every other path alone."""
    stubs = {"/proc/meminfo": meminfo, "/sys/fs/cgroup/memory.max": ceiling}
    return lambda name: stubs.get(str(name), Path(str(name)))


def test_an_explicit_worker_count_is_left_alone(pytester: pytest.Pytester) -> None:
    """`-n auto` is the only caller this answers. A number the caller chose is theirs."""
    pytester.makepyfile(
        "\n".join(f"def test_{index}(): pass" for index in range(4))
        + "\n\ndef test_worker_count(worker_id):\n    assert worker_id in ('gw0', 'gw1', 'gw2')\n"
    )

    result = pytester.runpytest("-n", "3", "-q", "-p", "no:randomly")

    assert result.ret == pytest.ExitCode.OK


def test_the_budget_wins_the_hook_xdist_asks(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seam, not the arithmetic. `pytest_xdist_auto_num_workers` is `firstresult` and xdist
    ships its own implementation returning the core count, so what `-n auto` gets is decided by
    which of the two the dispatcher reaches first. Asking through the real dispatcher — the same
    call xdist makes — is what proves this one answers rather than merely exists."""
    monkeypatch.setattr("ufo_testsupport.plugin.os.cpu_count", lambda: 8)
    monkeypatch.setattr("ufo_testsupport.plugin._total_memory_mb", lambda: 3931)

    answered = request.config.hook.pytest_xdist_auto_num_workers(config=request.config)

    assert answered == 2
