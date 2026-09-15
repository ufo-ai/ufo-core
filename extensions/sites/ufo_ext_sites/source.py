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

from ufo.sdk.sandbox import workspace_path
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
PROJECT_DESIGN = "application-design.svg"
PROJECT_CONFIG = "vite.config.ts"
PROJECT_PREVIEW = "preview.html"
PROJECT_DIST = "dist"
"""A page project's entry, the design it is held to, the config it is built with, the frame the
audit drives it in, and where that build writes the page. The entry's presence is what tells a
deploy the directory it was handed is source rather than a site: no browser runs TSX, so a directory
whose page names one is a project either way. The config and the preview frame are the deploy's own
— written beside the entry at deploy time, never carried in the project — so a page builds against
components as current as the pod that deployed it, and a page an app extension ships is audited in
the same portal frame a generated one is."""
PROJECT_FILE_ABSENT = 17
PROJECT_FILE_READ = f"""import sys

try:
    with open(sys.argv[1], encoding="utf-8", errors="replace") as handle:
        text = handle.read(int(sys.argv[2]))
except FileNotFoundError:
    raise SystemExit({PROJECT_FILE_ABSENT})
except OSError as error:
    raise SystemExit(str(error))
sys.stdout.write(text)"""
UPLOAD_SCRIPT = (
    'while [ "$#" -ge 2 ]; do curl -sS --fail-with-body -T "$1" --url "$2" || exit 1; shift 2; done'
)
DOWNLOAD_SCRIPT = (
    'while [ "$#" -ge 2 ]; do '
    'curl -sS --fail-with-body --create-dirs -o "$1" --url "$2" || exit 1; shift 2; done'
)
CLEAR_TIMEOUT_SECONDS = 120
UNPACK_KIT_PROG = """
import os
import sys
import tarfile

with tarfile.open(sys.argv[1]) as archive:
    for member in archive.getmembers():
        target = os.path.join(sys.argv[2], member.name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with archive.extractfile(member) as held, open(target, "wb") as handle:
            handle.write(held.read())
os.unlink(sys.argv[1])
"""
"""`python3` is in the sandbox image by contract; `tar` is not."""
UNPACK_TIMEOUT_SECONDS = 300


def _page_kit_archive() -> bytes:
    """The deploy's SDK as one gzipped tar, read from this package once at import.

    154 files and 8.0 MB on disk become a 2.6 MB archive in about 0.2 s, so a read spends one
    sandbox write and one unpack instead of 154 round trips. Built at import because a site object
    is read for reasons far smaller than an edit — its link, its visibility — and re-archiving per
    read would pay that on every one of them."""
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(KIT_DIR.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=f"{KIT_MOUNT}/{path.relative_to(KIT_DIR).as_posix()}")
    return buffer.getvalue()


PAGE_KIT_ARCHIVE = _page_kit_archive()
PROJECT_CONFIG_BYTES = (PAGE_DIR / PROJECT_CONFIG).read_bytes()
PROJECT_PREVIEW_BYTES = (PAGE_DIR / PROJECT_PREVIEW).read_bytes()


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

    The destination is removed before either store's writes: a file a newer deploy dropped, or a
    page an earlier build wrote here, must not survive a re-pull and ride the next redeploy back
    onto the site."""
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
    cleared = await ctx.sandbox.bash(f"rm -rf {shlex.quote(dest)}", timeout_s=CLEAR_TIMEOUT_SECONDS)
    if cleared.exit_code != 0:
        raise RuntimeError(cleared.stderr.strip() or f"removing {dest} failed")
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


async def unpack_page_kit(ctx: ToolContext, dest: str) -> None:
    """Write the deploy's kit under `<dest>/sdk/`, the sibling an app page project's `./sdk/kit.js`
    alias resolves through, so a page builds against components as current as the pod that deployed
    it rather than the ones its first build froze. One write and one unpack, which also unlinks the
    archive: a stray 2.3 MB file in the directory the build carries would ride onto the site."""
    archive = f"{dest}/{KIT_ARCHIVE}"
    await ctx.sandbox.write_file(archive, PAGE_KIT_ARCHIVE)
    unpacked = await ctx.sandbox.python(
        UNPACK_KIT_PROG, archive, dest, timeout_s=UNPACK_TIMEOUT_SECONDS
    )
    if unpacked.exit_code != 0:
        raise RuntimeError(unpacked.stderr.strip() or f"unpacking the kit under {dest} failed")
