import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from sandbox.mount_gate import (
    EXERCISE,
    WORKSPACE_DIR,
    _mount_gate_recipe,
)
from ufo.sandbox.fs_creds import SANDBOX_FS_TOKEN_SECRET_ENV
from ufo.sandbox.fs_mount import (
    SANDBOX_FS_TOKEN_STAGING_PATH,
    install_token_command,
    mount_health_check,
    prepare_token_staging_command,
)
from ufo.token_signing import verify_token

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_mount_gate_recipe_wires_the_expiring_token_proxy_and_root_mount_steps() -> None:
    conversation = uuid4()
    recipe = _mount_gate_recipe(
        bucket="bucket",
        region="us-east-1",
        proxy_url="https://proxy.test/",
        token_secret="secret",
        conversation=conversation,
        now=NOW,
    )
    claims = json.loads(verify_token(recipe.token, b"secret"))

    assert claims["kind"] == "deploy_gate"
    assert claims["conversation_id"] == str(conversation)
    assert claims["expires_at"] > int(NOW.timestamp())
    assert recipe.prepare_token_staging == prepare_token_staging_command()
    assert recipe.root_commands[0] == install_token_command()
    assert SANDBOX_FS_TOKEN_STAGING_PATH in recipe.root_commands[0]
    assert "https://proxy.test/sandbox-fs-credentials" in recipe.root_commands[2]
    assert recipe.root_commands[3] == mount_health_check(WORKSPACE_DIR)
    assert EXERCISE.startswith("set -ex")


def test_mount_gate_recipe_requires_the_deploy_token_secret() -> None:
    with pytest.raises(RuntimeError, match=SANDBOX_FS_TOKEN_SECRET_ENV):
        _mount_gate_recipe(
            bucket="bucket",
            region="us-east-1",
            proxy_url="https://proxy.test",
            token_secret=None,
            conversation=uuid4(),
            now=NOW,
        )
