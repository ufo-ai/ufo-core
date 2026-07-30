"""The `db` fixture guards a process global: `init_db` refuses a second call, so a setup failure
that skipped disposal would poison every later test in the process with 'db already initialized'
— one flaky failure amplified into a red shard. This proves the engine is disposed however the
fixture ends, by failing one test's reset and running another after it."""

import pytest

pytest_plugins = ("pytester",)


def test_a_failed_reset_leaves_the_next_test_a_clean_engine(pytester: pytest.Pytester) -> None:
    pytester.makeini(
        """
        [pytest]
        asyncio_mode = auto
        """
    )
    pytester.makepyfile(
        """
        import pytest
        import ufo_testsupport.plugin as plugin

        @pytest.fixture(autouse=True)
        def _poison_first_reset(request, monkeypatch):
            if not request.node.name.startswith("test_first"):
                return
            real = plugin.reset_workspace_data

            async def failing(connection):
                raise RuntimeError("reset failed")

            monkeypatch.setattr(plugin, "reset_workspace_data", failing)

        async def test_first(db):
            pass

        async def test_second(db):
            pass
        """
    )
    outcome = pytester.runpytest_subprocess("-q")
    outcome.assert_outcomes(passed=2, errors=2)
