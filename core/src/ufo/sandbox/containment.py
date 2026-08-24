"""The one containment guard for a path built from agent, model, or connector input.

Filename validation is not path validation: validating a *name* and then resolving it for a
read, copy, or write leaves the resolution following symlinks, which is how a container-controlled
agent plants a link in its own writable directory and reads a host file (CVE-2026-56692). The guard
is four ordered checks, in this order, and every ingress runs all four:

1. lexical — the name must be a usable relative name under the root (no empty/`.`/`..` target);
2. canonical — `resolve()` the parent, walking up to the nearest existing ancestor so a
   not-yet-created target still gets checked, and assert the result is inside the root;
3. per-component descent — re-open each component of the canonical parent from an fd on the root
   with `O_NOFOLLOW`, so every later operation names the target relative to a *pinned* parent fd
   that no path swap can redirect;
4. the target's own `lstat`, without following a final symlink — a planted link is refused instead
   of read, and a write goes to a staged name created `O_CREAT|O_EXCL|O_NOFOLLOW`.

A symlinked ancestor is check 2's to judge, never check 3's: the components the descent walks are
the canonical parent's, so an ancestor pointing out of the root is already a `LocationEscape` and
one pointing inside it has already been collapsed to the real directory the fds then pin. That is
what splits the policy on links under the root: an ancestor link is traversed, under a resolution
already proved contained and then pinned, while a link at the *target* is refused — its own
resolution is the one the descent cannot pin, and what it points at can be swapped after any check.

The root itself is `lstat`ed too. A symlinked root is the case a containment check alone misses:
`mkdir -p` follows the link, the target then really is inside the resolved root, and every
containment assertion downstream passes. A root an operator configured rather than an agent can
reach is `configured_root`'s, which follows the link the deploy chose.

Check 1 is also published on its own — `contained_relative`, `contained_leaf`, `contained_pattern` —
for an ingress that names a path this process cannot stat: one inside a container the carrier writes
to, or a file key persisted now and written on a later turn. Those callers get the lexical tier here
rather than each writing their own `".." in parts`, and the write they hand off to still runs checks
2 to 4 where the filesystem actually is.

This module lives two lives from one file: the serve process imports it as
`ufo.sandbox.containment`, and an in-sandbox program imports it as the sibling module `containment`,
whose source `SANDBOX_MODULE_BOOTSTRAP` carries into the program. So it stays standard-library only
and imports nothing from `ufo` — a dependency here would have to be installed inside every sandbox.
"""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from io import BufferedReader
from pathlib import Path, PurePosixPath
from uuid import uuid4

STAGED_PREFIX = ".ufo-staged-"
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
UNUSABLE_NAMES = frozenset({"", ".", ".."})


class ContainmentError(OSError):
    """A path built from untrusted input does not name a file inside its root.

    An `OSError`, because a refusal here reaches a caller through the same seams a filesystem's
    own refusals do — `Carrier.write` documents `OSError` and the container carriers raise the
    errno's own class — and one refusal arriving as two unrelated exception types by carrier is what
    makes an inbound-file loop catch neither."""


class RelativeEscape(ContainmentError):
    """The path or pattern is not a usable relative name under the root."""


class LocationEscape(ContainmentError):
    """The path resolves outside the root."""


class NonDirectoryAncestor(ContainmentError):
    """The root, or a component on the way to the target, is a symlink or not a directory."""


class PathNotFound(ContainmentError):
    """A component of the path does not exist."""


class NotRegularFile(ContainmentError):
    """The target exists but is not a regular file."""


def contained_root(root: str | os.PathLike[str]) -> Path:
    """The canonical form of a root, refusing a symlinked one.

    The `lstat` here is the check that a containment assertion cannot stand in for: with a symlinked
    root every path built under it resolves inside the link's target and passes containment, while
    the bytes land wherever the link points."""
    path = Path(root)
    try:
        entry = path.lstat()
    except OSError as error:
        raise PathNotFound(f"{path} not found") from error
    if not stat.S_ISDIR(entry.st_mode):
        raise NonDirectoryAncestor(f"{path} is not a directory")
    return path.resolve()


def configured_root(root: str | os.PathLike[str], setting: str) -> Path:
    """The canonical form of a root an operator configured, which may itself be a symlink.

    `contained_root`'s refusal of a symlinked root answers a threat the agent poses: it can
    replace a root it reaches and redirect every path built under it. A root that arrives as deploy
    config is a different one — `/var/lib/ufo/blobs -> /mnt/data/blobs` is an ordinary compose or
    k8s layout, and refusing it takes the store or every workspace down — so the link it chose is
    followed here, once, and the canonical result is what the descent then runs against, leaving
    every path *under* the root as contained as before. A root that names something other than a
    directory is still refused, naming `setting` so an operator knows which key to fix."""
    path = Path(root)
    try:
        entry = path.stat()
    except OSError as error:
        raise PathNotFound(f"{setting} names {path}, which does not exist") from error
    if not stat.S_ISDIR(entry.st_mode):
        raise NonDirectoryAncestor(f"{setting} names {path}, which is not a directory")
    return path.resolve()


@dataclass(frozen=True)
class ContainedFile:
    """A validated target, addressed only through `parent_fd`.

    `parent_fd` is an fd on the canonical parent directory reached by the symlink-free descent, so
    every operation below names the file by `name` relative to it. A directory renamed or replaced
    by a symlink after the descent takes the fd's inode with it, which is what keeps the check and
    the operation talking about the same file. `path` is the canonical location, for messages and
    for the readers that must hand a path to a subprocess."""

    root: Path
    path: Path
    name: str
    parent_fd: int

    def lstat(self) -> os.stat_result | None:
        """The target's own stat, never following a final symlink. `None` when nothing holds the
        name; `NotRegularFile` when something other than a regular file does."""
        try:
            entry = os.stat(self.name, dir_fd=self.parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if stat.S_ISDIR(entry.st_mode):
            raise NotRegularFile(f"{self.path} is a directory")
        if not stat.S_ISREG(entry.st_mode):
            raise NotRegularFile(f"{self.path} is not a regular file")
        return entry

    def mode(self, default: int) -> int:
        """The permission bits an overwrite of this target carries across, `default` when nothing
        holds the name.

        A symlink holding it answers `default` rather than raising: `replace_bytes` renames a staged
        inode onto the name instead of writing through it, so a link there cannot steer the bytes,
        while refusing would let one planted link deny a delivery — an inbound attachment, an
        offloaded result — for good. A directory is still refused: it is not a file to write, and no
        rename can replace it."""
        try:
            entry = os.stat(self.name, dir_fd=self.parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return default
        if stat.S_ISDIR(entry.st_mode):
            raise NotRegularFile(f"{self.path} is a directory")
        return entry.st_mode & 0o777 if stat.S_ISREG(entry.st_mode) else default

    def open_bytes(self) -> BufferedReader:
        """The target open for reading through an `O_NOFOLLOW` fd whose `fstat` says regular file —
        what a consumer that must not hold the file whole (a copy-out, a digest over a
        multi-gigabyte artifact) streams from. The returned fd is the file itself, so it keeps
        naming the file the descent proved after the pinned parent is closed."""
        descriptor = self._open_regular()
        try:
            return os.fdopen(descriptor, "rb")
        except BaseException:
            os.close(descriptor)
            raise

    def read_bytes(self, limit: int) -> bytes:
        """Up to `limit` bytes, read through an `O_NOFOLLOW` fd whose `fstat` says regular file."""
        with self.open_bytes() as handle:
            return handle.read(limit)

    def read_text(self, limit: int) -> str:
        return self.read_bytes(limit).decode("utf-8", errors="replace")

    def chmod(self, mode: int) -> None:
        os.chmod(self.name, mode & 0o777, dir_fd=self.parent_fd, follow_symlinks=False)

    def unlink(self) -> None:
        try:
            os.unlink(self.name, dir_fd=self.parent_fd)
        except FileNotFoundError:
            pass

    def replace_with(self, source: ContainedFile) -> None:
        """Rename `source` onto this target, both named relative to their pinned parents."""
        os.replace(source.name, self.name, src_dir_fd=source.parent_fd, dst_dir_fd=self.parent_fd)

    def replace_text(self, text: str, mode: int) -> None:
        self.replace_bytes(text.encode(), mode)

    def replace_bytes(self, data: bytes, mode: int) -> None:
        """Write `data` to a staged sibling created `O_CREAT|O_EXCL|O_NOFOLLOW` and rename it onto
        the target. Exclusive create is what makes the staging step refuse a name a symlink already
        holds, rather than truncating through it; the rename installs the bytes whole, so a reader
        of the name sees one write or the one before it."""
        staged = f"{STAGED_PREFIX}{uuid4().hex}"
        descriptor = os.open(
            staged,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=self.parent_fd,
        )
        try:
            os.fchmod(descriptor, mode & 0o777)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(data)
            os.replace(staged, self.name, src_dir_fd=self.parent_fd, dst_dir_fd=self.parent_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(staged, dir_fd=self.parent_fd)
            except FileNotFoundError:
                pass

    def _open_regular(self) -> int:
        try:
            descriptor = os.open(
                self.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=self.parent_fd,
            )
        except FileNotFoundError as error:
            raise PathNotFound(f"{self.path} not found") from error
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise NotRegularFile(f"{self.path} is not a regular file") from error
            raise
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise NotRegularFile(f"{self.path} is not a regular file")
        return descriptor


@contextmanager
def contained_file(
    path: str | os.PathLike[str],
    root: str | os.PathLike[str],
    *,
    create_parent: bool = False,
) -> Iterator[ContainedFile]:
    """The write and read entry point: all four checks, then a handle pinned to the target's parent.

    `create_parent` makes each missing directory on the way as the descent reaches it, so a write to
    a path whose directories do not exist yet is checked component by component as it is built,
    never resolved once and trusted."""
    canonical_root = contained_root(root)
    target = rooted(path, canonical_root)
    if target.name in UNUSABLE_NAMES:
        raise RelativeEscape(f"{target} is not a file path")
    parent = target.parent.resolve()
    if not _inside(parent, canonical_root):
        raise LocationEscape(f"{target} escapes {canonical_root}")
    descriptor = _open_root(canonical_root)
    try:
        for part in parent.relative_to(canonical_root).parts:
            if create_parent:
                try:
                    os.mkdir(part, dir_fd=descriptor)
                except FileExistsError:
                    pass
            descriptor = _descend(descriptor, part, target)
        yield ContainedFile(
            root=canonical_root,
            path=parent / target.name,
            name=target.name,
            parent_fd=descriptor,
        )
    finally:
        os.close(descriptor)


def contained_dir(
    path: str | os.PathLike[str], root: str | os.PathLike[str], *, create: bool = False
) -> Path:
    """The canonical path of a directory under `root`, reached without following a symlink at any
    component — the enumeration entry point, where the result is a directory to walk rather than a
    file to open, so the descent's fds are released once each component has been proved.

    `create` makes each missing component as the descent reaches it, from the parent's own fd, so a
    directory a caller provisions is built through the checks instead of by a `mkdir` that would
    follow a link planted at the name."""
    canonical_root = contained_root(root)
    target = rooted(path, canonical_root)
    resolved = target.resolve()
    if not _inside(resolved, canonical_root):
        raise LocationEscape(f"{target} escapes {canonical_root}")
    descriptor = _open_root(canonical_root)
    try:
        for part in resolved.relative_to(canonical_root).parts:
            if create:
                try:
                    os.mkdir(part, dir_fd=descriptor)
                except FileExistsError:
                    pass
            descriptor = _descend(descriptor, part, target)
    finally:
        os.close(descriptor)
    return resolved


def contained_regular(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> Path:
    """The canonical path of an existing regular file under `root`, for a reader that has to hand a
    path to something else — a subprocess, a library that only takes a filename. Every ancestor is
    proved symlink-free and the target itself is `lstat`ed, so the name cannot be a planted link; a
    reader that can work from an fd should use `contained_file` instead and keep the parent pinned
    for the read itself."""
    with contained_file(path, root) as target:
        if target.lstat() is None:
            raise PathNotFound(f"{target.path} not found")
        return target.path


def contained_pattern(pattern: str, root: Path) -> str:
    """A glob pattern rewritten to one relative to `root`, refusing any that leaves it.

    Scoping the directory an enumeration starts from does not scope the pattern: an absolute pattern
    re-roots the walk at the filesystem anchor and the scoped root is never consulted. So an
    absolute pattern is honoured only when it names a location inside the root, as the root-relative
    pattern it is there, and `..` is refused outright."""
    candidate = PurePosixPath(pattern)
    if ".." in candidate.parts:
        raise RelativeEscape(f"pattern {pattern!r} leaves {root}")
    if not candidate.is_absolute():
        return pattern
    if not candidate.is_relative_to(root):
        raise LocationEscape(f"pattern {pattern!r} escapes {root}")
    relative = str(candidate.relative_to(root))
    if relative == ".":
        raise RelativeEscape(f"pattern {pattern!r} matches a directory root")
    return relative


def contained_glob(
    pattern: str, path: str | os.PathLike[str] | None, root: Path
) -> tuple[Path, str]:
    """The directory an enumeration walks and the pattern to run there, both confined to `root`.

    An absolute pattern names its own location, so it walks from the root — never from the
    filesystem anchor `Path.glob` would take it to — and a caller's start directory does not apply
    to it. A relative one walks from `path`, or from the root when the caller named none. Deciding
    that here is what keeps the shape of a pattern from being a question every enumeration answers
    itself."""
    absolute = PurePosixPath(pattern).is_absolute()
    start = root if absolute or path is None else path
    return contained_dir(start, root), contained_pattern(pattern, root)


def contained_relative(path: str, root: str) -> str:
    """The absolute path `path` names under `root`, resolved lexically — check 1 alone, for a caller
    whose write lands on a filesystem this process cannot reach.

    A lexical resolution is not a canonical one: `a/../b` is `b` here even where `a` is a
    symlink, so this proves only that the path *intends* to stay under the root. That intent is
    exactly what a sub-root needs asserted — a mount path or a persisted file key is contained at
    its own root, not merely at the workspace above it — and the write itself still runs the
    descent (a workspace path gets it in the carrier). The root itself is not a file path, so a
    path resolving onto it is refused rather than returned."""
    candidate = PurePosixPath(path if path.startswith("/") else f"{root}/{path}")
    parts: list[str] = []
    for part in candidate.parts:
        if part == "..":
            if len(parts) <= 1:
                raise LocationEscape(f"path {path!r} escapes {root}")
            parts.pop()
        elif part not in {"", "."}:
            parts.append(part)
    resolved = PurePosixPath(*parts)
    base = PurePosixPath(root)
    if resolved == base:
        raise RelativeEscape(f"path {path!r} names {root} itself, not a file in it")
    if base not in resolved.parents:
        raise LocationEscape(f"path {path!r} escapes {root}")
    return str(resolved)


def contained_leaf(raw: str, fallback: str) -> str:
    """One usable filename component from a name chosen elsewhere — a provider's file name, a
    surface attachment, a browser's content-disposition. Directory components are dropped rather
    than refused, in both separators (a Windows client sends backslashes), and a name that leaves
    nothing usable behind falls back, so an inbound file still lands under a name of ours instead of
    failing the delivery it rode in on. Dropping the components is what makes this a leaf; it is not
    containment, so the caller still joins it under a root and writes through the guard."""
    leaf = PurePosixPath(raw.replace("\\", "/")).name
    return fallback if leaf in UNUSABLE_NAMES else leaf


def is_contained_regular(path: Path, root: Path) -> bool:
    """Whether an enumerated path is a regular file inside `root` that no symlink was crossed to
    reach — the filter an enumeration applies to its own results, where a per-component descent per
    hit would cost more than the walk that produced it. A read of a hit still goes through
    `contained_file`: this answers what to list, not what to open."""
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return False
        canonical = path.resolve(strict=True)
    except OSError:
        return False
    return canonical == path and _inside(canonical, root)


def rooted(path: str | os.PathLike[str], root: Path) -> Path:
    """A relative path read against `root`, never against the process's cwd — where every entry
    point below starts, and the one thing a caller that must ask something about a path *before*
    choosing which entry point to send it through needs to get right. A caller that roots it
    elsewhere asks its question about one path and then guards another."""
    target = Path(path)
    return target if target.is_absolute() else root / target


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _open_root(root: Path) -> int:
    try:
        return os.open(root, DIR_FLAGS)
    except OSError as error:
        raise NonDirectoryAncestor(f"{root} is not an openable directory") from error


def _descend(descriptor: int, part: str, target: Path) -> int:
    """One component deeper, refusing to follow a link, and closing the fd left behind."""
    try:
        child = os.open(part, DIR_FLAGS, dir_fd=descriptor)
    except FileNotFoundError as error:
        raise PathNotFound(f"{target} not found") from error
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise NonDirectoryAncestor(
                f"{target} passes through {part}, which is not a directory"
            ) from error
        raise ContainmentError(str(error)) from error
    os.close(descriptor)
    return child
