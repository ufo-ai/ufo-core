from datetime import UTC, datetime
from uuid import uuid4

import pytest

import evals.__main__ as eval_main
from evals.harness.viewer import RunRecorder
from ufo.config import BlobConfig, Config, DatabaseConfig


async def test_the_eval_run_destroys_its_dbos_client(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destroyed = False

    class Client:
        def destroy(self) -> None:
            nonlocal destroyed
            destroyed = True

    async def resolved(*_args: object) -> tuple[object, ...]:
        return uuid4(), uuid4(), "prompt", "model", "off", "small"

    def reject_registry(*_args: object) -> None:
        raise RuntimeError("stop after client creation")

    monkeypatch.setattr(eval_main, "load_manifests", lambda _pack: ())
    monkeypatch.setattr(eval_main, "resolve_workspace_and_agent", resolved)
    monkeypatch.setattr(eval_main, "replay_safe_client", lambda _url: Client())
    monkeypatch.setattr(eval_main, "model_registry", reject_registry)
    config = Config(
        database=DatabaseConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'ufo.db'}"),
        blob=BlobConfig(backend="filesystem", root=tmp_path / "blobs"),
    )

    with pytest.raises(RuntimeError, match="stop after client creation"):
        await eval_main._run(
            config,
            (),
            "ufo",
            RunRecorder(
                root=tmp_path,
                id=uuid4(),
                created_at=datetime.now(UTC),
                label="lifecycle",
                agent="ufo",
                ufo_version="test",
                revision="test",
            ),
        )

    assert destroyed
