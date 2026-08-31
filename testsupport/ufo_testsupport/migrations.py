"""The migrated-SQLite template every test database is built from, cached across pytest processes.

`apply_migrations` seals the file it migrates — WAL mode, nothing in a sidecar — so a byte copy of
it carries the whole database, which is what the `db` fixture already does per test. Caching that
one file per machine is what this adds: `alembic upgrade heads` over core's chain plus every
extension branch costs ~2 s, and it is paid by every pytest process and by every per-test fixture
that migrates a database of its own. The key is a digest of every migration file that would run, so
a new or edited migration misses the cache; `UFO_TEST_SQLITE_TEMPLATE_CACHE_OFF` bypasses it."""

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from sqlalchemy.engine import make_url

from ufo.db import MIGRATIONS_DIR, apply_migrations
from ufo.host.ext.loader import migration_locations

SQLITE_TEMPLATE_DIR = Path(tempfile.gettempdir()) / "ufo-sqlite-templates"
TEMPLATE_CACHE_OFF_ENV = "UFO_TEST_SQLITE_TEMPLATE_CACHE_OFF"
SQLITE_SCHEME = "sqlite"


def apply_cached_migrations(url: str, pack: str | None = None) -> None:
    """Bring `url`'s database to `heads`, byte-copying a cached migrated file when one matches this
    tree's migrations. Postgres has no copyable template, so it runs the migrations itself.

    A database that is already there is migrated rather than copied over: `apply_migrations` over
    one at heads is a no-op, where the copy would replace it — along with anything a `-wal` beside
    it still holds — so the cache never turns an idempotent call into a destructive one."""
    if not url.startswith(SQLITE_SCHEME) or os.environ.get(TEMPLATE_CACHE_OFF_ENV):
        apply_migrations(url, pack)
        return
    database = make_url(url).database
    if database is None:
        raise RuntimeError(f"sqlite url names no file: {url}")
    destination = Path(database)
    if destination.exists():
        apply_migrations(url, pack)
        return
    template = SQLITE_TEMPLATE_DIR / f"{_migration_digest(pack)}.db"
    if template.exists():
        shutil.copy(template, destination)
        return
    apply_migrations(url, pack)
    _publish(destination, template)


def _migration_digest(pack: str | None) -> str:
    digest = hashlib.sha256()
    locations = (str(MIGRATIONS_DIR / "versions"), *migration_locations(pack))
    for location in locations:
        for path in sorted(Path(location).glob("*.py")):
            digest.update(path.name.encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def _publish(migrated: Path, template: Path) -> None:
    """Two xdist workers reach a cold cache together, so the copy is staged beside the template and
    renamed onto it — a reader either sees no template or sees a whole one, never a partial file."""
    SQLITE_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    staged = template.with_suffix(f".{os.getpid()}")
    staged.write_bytes(migrated.read_bytes())
    staged.replace(template)
