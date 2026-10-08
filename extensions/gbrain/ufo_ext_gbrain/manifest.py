"""What the gbrain extension declares: the `gbrain_git` and `gbrain_folder` content-source
backends that read a GitHub repository's or a serve-local directory's markdown files as pages, the
`github_token` credential slot that opens private repositories, and the `gbrain_source` object
kind that registers one origin as a syncing source row. `serve` sources the backends into the sync
driver's backend map, so a registered origin syncs offline into memory."""

from ufo.sdk.manifest import CredentialSlot, FeedRelease, Manifest, SourceProvider
from ufo_ext_gbrain.folder import FOLDER_BACKEND, GbrainFolderSource
from ufo_ext_gbrain.git import GIT_BACKEND, GITHUB_TOKEN_SLOT, GbrainGitSource
from ufo_ext_gbrain.objects import GBRAIN_OBJECT

NAME = "gbrain"
VERSION = "0.1.0"
GITHUB_HOSTS = ("api.github.com", "codeload.github.com")


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        objects=(GBRAIN_OBJECT,),
        sources=(
            SourceProvider(
                backend=GIT_BACKEND,
                build=lambda credentials: GbrainGitSource(credentials=credentials),
            ),
            SourceProvider(
                backend=FOLDER_BACKEND,
                build=lambda _credentials: GbrainFolderSource(),
            ),
        ),
        credentials=(
            CredentialSlot(
                name=GITHUB_TOKEN_SLOT,
                description="GitHub token that opens private gbrain repositories; public "
                "repositories sync without it.",
                feed=FeedRelease(GIT_BACKEND, GITHUB_HOSTS, False),
            ),
        ),
    )
