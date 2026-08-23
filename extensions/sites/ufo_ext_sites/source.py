"""Site source moving between the blob store and a sandbox — the deploy's upload and the kind
read's materialization, one bounded transfer under either shape of store: an S3 store exchanges
presigned URLs curled from inside the container, a filesystem dev store streams through this
process. The manifest itself is `store.SourceManifest`; this module only moves the bytes it
names."""

import shlex

from ufo.sdk.sandbox import WORKSPACE_DIR, workspace_path
from ufo.sdk.tools import ToolContext
from ufo_ext_sites.store import HostedSite, SourceManifest

SOURCE_PUT_TTL_SECONDS = 900
SOURCE_GET_TTL_SECONDS = 900
SOURCE_TRANSFER_BATCH = 50
SOURCE_TRANSFER_TIMEOUT_BASE_SECONDS = 120
SOURCE_TRANSFER_BYTES_PER_SECOND = 1024 * 1024
STAMP_READ_TIMEOUT_SECONDS = 15
SITE_SOURCE_DIR = "sites"
UPLOAD_SCRIPT = (
    'while [ "$#" -ge 2 ]; do curl -sS --fail-with-body -T "$1" --url "$2" || exit 1; shift 2; done'
)
DOWNLOAD_SCRIPT = (
    'while [ "$#" -ge 2 ]; do '
    'curl -sS --fail-with-body --create-dirs -o "$1" --url "$2" || exit 1; shift 2; done'
)
CLAIM_TREE_PROG = """
import os
import sys
from containment import ContainmentError, contained_dir, contained_file

try:
    if os.path.lexists(sys.argv[2]):
        root = contained_dir(sys.argv[2], sys.argv[1])
        for base, dirs, names in os.walk(root):
            dirs[:] = [name for name in dirs if not os.path.islink(os.path.join(base, name))]
            for name in names:
                with contained_file(os.path.join(base, name), root) as held:
                    held.unlink()
    for path in sys.argv[3:]:
        with contained_file(path, sys.argv[1], create_parent=True) as target:
            target.replace_bytes(b"", 0o644)
except ContainmentError as error:
    raise SystemExit(str(error))
"""
CLAIM_TIMEOUT_SECONDS = 120


async def transfer(
    ctx: ToolContext, script: str, pairs: list[tuple[str, str]], total_bytes: int
) -> None:
    timeout = SOURCE_TRANSFER_TIMEOUT_BASE_SECONDS + total_bytes // SOURCE_TRANSFER_BYTES_PER_SECOND
    for start in range(0, len(pairs), SOURCE_TRANSFER_BATCH):
        batch = pairs[start : start + SOURCE_TRANSFER_BATCH]
        moved = await ctx.sandbox.sh(
            script, *(part for pair in batch for part in pair), timeout_s=timeout
        )
        if moved.exit_code != 0:
            raise RuntimeError(moved.stderr.strip() or moved.stdout.strip() or "transfer failed")


async def materialize_source(
    ctx: ToolContext, site: HostedSite, object_name: str
) -> tuple[str, list[str]]:
    """Write the site's stored source into this conversation's sandbox and answer the directory it
    landed in with the paths written, relative to it.

    This is how an edit reaches the page the member's link opens rather than the shipped skill or
    a stale working copy: the source of record lives under the manifest's own keys, so any
    conversation's read starts from the last deploy. The destination is the object's own directory
    under `sites/`, overwriting — the tree is bounded by the deploy's own caps, so the whole of it
    lands and a redeploy of the directory carries every file, never a truncation of the page.

    A generation stamp beside the directory makes the read idempotent: a site object is read for
    reasons far smaller than an edit — its link, its visibility — and re-pulling the whole tree on
    each would spend up to the deploy caps per read. A stamp matching the row's deploy generation
    answers without a transfer, which also leaves an agent's in-progress edits alone: the stamp can
    only match while the store still holds exactly what the last pull delivered.

    Before either store's writes, the destination directory is emptied and every manifest path
    claimed through the containment guard — the emptying because a file a newer deploy dropped
    must not survive a re-pull and ride the next redeploy back onto the site, the claim because
    `curl -o` follows a symlink and truncates what it finds, and the names sit in a directory the
    agent writes. Directories are made by an `O_NOFOLLOW` descent and each name becomes a fresh
    empty regular file."""
    if site.source_manifest is None:
        raise RuntimeError(f"site {site.name!r} has no stored source to materialize")
    manifest = SourceManifest.model_validate_json(site.source_manifest)
    dest = workspace_path(f"{SITE_SOURCE_DIR}/{object_name}")
    paths = sorted(manifest.files)
    stamp = workspace_path(f"{SITE_SOURCE_DIR}/.{object_name}.generation")
    held = await ctx.sandbox.bash(
        f"cat {shlex.quote(stamp)} 2>/dev/null || true", timeout_s=STAMP_READ_TIMEOUT_SECONDS
    )
    if held.stdout.strip() == str(site.deploy_generation):
        return dest, paths
    claimed = await ctx.sandbox.python(
        CLAIM_TREE_PROG,
        WORKSPACE_DIR,
        dest,
        *(f"{dest}/{path}" for path in paths),
        timeout_s=CLAIM_TIMEOUT_SECONDS,
    )
    if claimed.exit_code != 0:
        raise RuntimeError(
            claimed.stderr.strip() or f"claiming the source tree under {dest} failed"
        )
    try:
        downloads = [
            (
                f"{dest}/{path}",
                await ctx.blob.presigned_get(f"{manifest.root}{path}", SOURCE_GET_TTL_SECONDS),
            )
            for path in paths
        ]
    except TypeError:
        for path in paths:
            await ctx.sandbox.write_file(
                f"{dest}/{path}", await ctx.blob.get(f"{manifest.root}{path}")
            )
    else:
        total = sum(entry.size for entry in manifest.files.values())
        await transfer(ctx, DOWNLOAD_SCRIPT, downloads, total)
    await ctx.sandbox.write_file(stamp, str(site.deploy_generation).encode())
    return dest, paths
