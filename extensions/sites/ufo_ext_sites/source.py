"""Site source moving between the blob store and a sandbox — the deploy's upload and the kind
read's materialization, one bounded transfer under either shape of store: an S3 store exchanges
presigned URLs curled from inside the container, a filesystem dev store streams through this
process. The manifest itself is `store.SourceManifest`; this module only moves the bytes it
names.

An app page's project is assembled here rather than read whole from one place, because its three
parts have three owners: the page's `app.tsx` and the `index.html` that names it are the app
extension's, carried as its home skill's bundled files — that html is the project's page, the input
a build reads, never a document served to a browser; `vite.config.ts` and the kit under `page/` are
this extension's own package data, because this materialization is their only reader; and a fork's
edited `src/` is the member's, under its deploy's manifest keys. Only the middle pair is fixed at
the deploy — which is what makes an app page forked a year ago build against today's components
rather than the ones its first build froze."""

import shlex
import tarfile
from io import BytesIO
from pathlib import Path

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
PAGE_DIR = Path(__file__).parent / "page"
KIT_DIR = PAGE_DIR / "kit"
KIT_MOUNT = "sdk"
KIT_ARCHIVE = "sdk.tar.gz"
PROJECT_SOURCE = "app.tsx"
PROJECT_CONFIG = "vite.config.ts"
PROJECT_DIST = "dist"
"""A page project's entry, the config it is built with, and where that build writes the page. The
entry's presence is what tells a deploy the directory it was handed is source rather than a site: no
browser runs TSX, so a directory whose page names one is a project either way. The config is the
deploy's own — written beside the entry at deploy time, never carried in the project — so a page
builds against components as current as the pod that deployed it."""
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
UNPACK_KIT_PROG = """
import os
import sys
import tarfile
from containment import ContainmentError, contained_file

try:
    with tarfile.open(sys.argv[2]) as archive:
        for member in archive.getmembers():
            if not member.isfile():
                raise SystemExit(f"the kit archive holds {member.name}, which is not a file")
            held = archive.extractfile(member)
            with contained_file(
                f"{sys.argv[3]}/{member.name}", sys.argv[1], create_parent=True
            ) as target:
                target.replace_bytes(held.read(), 0o644)
    os.unlink(sys.argv[2])
except ContainmentError as error:
    raise SystemExit(str(error))
"""
"""Unpack the kit member by member through the containment guard rather than with `extractall` or
`tar`: every path is re-derived and checked under the workspace root, a member that is not a regular
file stops the read, and no name in the archive can reach outside the destination or ride a symlink
the agent left in it. `python3` is in the sandbox image by contract; `tar` is not.

The archive is unlinked here rather than by a command after it, so the transfer leaves nothing
behind in the directory a redeploy carries and the whole unpack is one round trip."""
UNPACK_TIMEOUT_SECONDS = 300


def _page_kit_archive() -> bytes:
    """The deploy's SDK as one gzipped tar, read from this package once at import.

    148 files and 6.8 MB on disk become a 2.3 MB archive in about 0.16 s, so a read spends one
    sandbox write and one unpack instead of 148 round trips. Built at import because a site object
    is read for reasons far smaller than an edit — its link, its visibility — and re-archiving per
    read would pay that on every one of them."""
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(KIT_DIR.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=f"{KIT_MOUNT}/{path.relative_to(KIT_DIR).as_posix()}")
    return buffer.getvalue()


PAGE_KIT_ARCHIVE = _page_kit_archive()
PROJECT_CONFIG_BYTES = (PAGE_DIR / "vite.config.ts").read_bytes()


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

    This is how an edit reaches the page the member's link opens rather than a stale working copy:
    the source of record lives under the manifest's own keys, so any conversation's read starts
    from the last deploy. The destination is the object's own directory under `sites/`, holding the
    whole deployed tree, so the directory the read names is the directory a redeploy carries and an
    edit reaches the site whole rather than truncating it. An app page's tree is both the page and
    its source at once — `index.html` and hashed `assets/` are what its build wrote, `src/` is what
    it built from — so a later edit starts from the `src/` beside the page it produced.

    A generation stamp beside the directory makes the read idempotent: a site object is read for
    reasons far smaller than an edit — its link, its visibility — and re-pulling the whole tree on
    each would spend up to the deploy caps per read. A stamp matching the row's deploy generation
    answers without a transfer, which also leaves an agent's in-progress edits alone: the stamp can
    only match while the store still holds exactly what the last pull delivered.

    Before either store's writes, the destination directory is emptied and every selected path
    claimed through the containment guard — the emptying because a file a newer deploy dropped, or
    a page an earlier build wrote here, must not survive a re-pull and ride the next redeploy back
    onto the site; the claim because `curl -o` follows a symlink and truncates what it finds, and
    the names sit in a directory the agent writes. Directories are made by an `O_NOFOLLOW` descent
    and each name becomes a fresh empty regular file. The kit's own names are checked the same way
    as they are unpacked, which is why the archive lands after the emptying rather than before."""
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
        total = sum(manifest.files[path].size for path in paths)
        await transfer(ctx, DOWNLOAD_SCRIPT, downloads, total)
    await ctx.sandbox.write_file(stamp, str(site.deploy_generation).encode())
    return dest, paths


async def unpack_page_kit(ctx: ToolContext, dest: str, runtime_root: str | None = None) -> None:
    """Write the deploy's kit under `<dest>/sdk/`, the sibling an app page project's `./sdk/kit.js`
    alias resolves through, so a page builds against components as current as the pod that deployed
    it rather than the ones its first build froze. One write and one unpack, which also unlinks the
    archive: a stray 2.3 MB file in the directory the build carries would ride onto the site."""
    archive = f"{dest}/{KIT_ARCHIVE}"
    if runtime_root is None:
        await ctx.sandbox.write_file(archive, PAGE_KIT_ARCHIVE)
    else:
        await ctx.sandbox.write_runtime_path(archive, PAGE_KIT_ARCHIVE)
    unpacked = await ctx.sandbox.python(
        UNPACK_KIT_PROG,
        runtime_root or WORKSPACE_DIR,
        archive,
        dest,
        timeout_s=UNPACK_TIMEOUT_SECONDS,
    )
    if unpacked.exit_code != 0:
        raise RuntimeError(unpacked.stderr.strip() or f"unpacking the kit under {dest} failed")
