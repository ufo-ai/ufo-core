from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from ufo_ext_slack.manifest import manifest
from ufo_ext_slack.surface import (
    IDENTITY_BLOB_KEY,
    SLACK_BOT_TOKEN_SLOT,
    SlackIdentity,
    bot_token_fingerprint,
)

from ufo.access.credentials import CredentialStore
from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.serve import _source_identity_resolvers
from ufo.workspace import ws


class _Credentials:
    def __init__(self, token: str) -> None:
        self.token = token

    async def get(self, _workspace_id: UUID, slot: str) -> str:
        assert slot == SLACK_BOT_TOKEN_SLOT
        return self.token


async def test_slack_source_identity_tracks_reinstall_without_caching(tmp_path: Path) -> None:
    workspace_id = uuid4()
    old_token = "xoxb-old"
    new_token = "xoxb-new"
    credentials = _Credentials(old_token)
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    with ws(workspace_id):
        await blob.put(
            IDENTITY_BLOB_KEY,
            SlackIdentity(
                bot_token_fingerprint=bot_token_fingerprint(old_token),
                team_id="T01234567",
                bot_user_id="U_OLD",
            )
            .model_dump_json()
            .encode(),
        )
    resolve = _source_identity_resolvers(
        (manifest(),),
        cast(CredentialStore, credentials),
        blob,
    )["slack"]

    assert await resolve(workspace_id) == "U_OLD"
    credentials.token = new_token
    assert await resolve(workspace_id) is None
    with ws(workspace_id):
        await blob.put(
            IDENTITY_BLOB_KEY,
            SlackIdentity(
                bot_token_fingerprint=bot_token_fingerprint(new_token),
                team_id="T01234567",
                bot_user_id="U_NEW",
            )
            .model_dump_json()
            .encode(),
        )
    assert await resolve(workspace_id) == "U_NEW"
