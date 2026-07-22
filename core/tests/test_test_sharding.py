import pytest

pytest_plugins = ("pytester",)


def test_shards_partition_the_collection(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("\n".join(f"def test_{index}(): pass" for index in range(20)))

    first = pytester.runpytest("--shard", "1/2", "-q").parseoutcomes()
    second = pytester.runpytest("--shard", "2/2", "-q").parseoutcomes()

    assert first["passed"] + second["passed"] == 20
    assert first["deselected"] == second["passed"]
    assert second["deselected"] == first["passed"]


def test_shard_rejects_an_invalid_partition(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("def test_sample(): pass")

    result = pytester.runpytest("--shard", "0/2", "-q")

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*--shard must be INDEX/COUNT with COUNT >= 2*"])
