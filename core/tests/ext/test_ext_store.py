import importlib
from importlib.metadata import EntryPoint
from pathlib import Path
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
import sqlalchemy as sa
import ufo_ext_sample.manifest as sample
from ufo_ext_sample.jobs import JOB_KEY, JOB_NAME
from ufo_ext_sample.tools import NOTE_TABLE

from ufo.bundle import BUNDLE_CONSTRAINTS_NAME, BUNDLE_WHEELS_DIR, Bundle, wheel_name
from ufo.db import workspace_tx
from ufo.host.ext.loader import (
    LOCKFILE_PATH_ENV,
    ExtensionPin,
    Lockfile,
    discovered,
    extension_digest,
    load_manifests,
    write_lockfile,
)
from ufo.host.ext.store import Catalog, CatalogEntry, ExtensionStore, ufo_version
from ufo.runtime.ext.context import ScopedStore
from ufo.runtime.jobs import JobRunner, bindings_from
from ufo.runtime.workspace import ws
from ufo.schema import tables

LOCKED = "sqlalchemy==2.0.51\n"
CONFIG_TOML = """\
[database]
url = "sqlite+aiosqlite:///ufo.db"

[blob]
backend = "filesystem"
root = "./blobs"
"""


def _catalog(disabled: bool = False) -> Catalog:
    return Catalog(
        extensions=(CatalogEntry(name=sample.NAME, version=sample.VERSION, disabled=disabled),)
    )


def _pinned_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    lock = tmp_path / "ufo.lock"
    monkeypatch.setenv(LOCKFILE_PATH_ENV, str(lock))
    return lock


def _sample_wheel(tmp_path: Path) -> Path:
    wheel = tmp_path / wheel_name()
    root = Path(sample.__file__).parent
    with ZipFile(wheel, "w") as archive:
        for path in root.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                archive.write(path, f"ufo_ext_sample/{path.relative_to(root).as_posix()}")
    return wheel


def _sample_client(tmp_path: Path) -> Path:
    client = tmp_path / "ufo"
    client.write_bytes(b"sandbox-client")
    return client


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _seed_note(workspace_id: UUID) -> None:
    """Give the workspace a row in the sample's own `sample_ext_note` table — the table its tick
    job's candidate selector reads."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(NOTE_TABLE).values(workspace_id=workspace_id, note="seed")
        )


def test_an_empty_lockfile_deactivates_every_discovered_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _pinned_lock(tmp_path, monkeypatch)
    write_lockfile(lock, Lockfile(ufo_version="0.1.0"))
    assert load_manifests() == ()


def test_install_pins_the_digest_and_activates_the_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _pinned_lock(tmp_path, monkeypatch)
    write_lockfile(lock, Lockfile(ufo_version="0.1.0"))
    pin = ExtensionStore(catalog=_catalog(), lockfile=lock).install(sample.NAME)
    assert pin.name == sample.NAME
    assert pin.version == sample.VERSION
    assert pin.digest.startswith("sha256:")
    assert [manifest.name for manifest in load_manifests()] == [sample.NAME]


def test_remove_reverses_the_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock = _pinned_lock(tmp_path, monkeypatch)
    write_lockfile(lock, Lockfile(ufo_version="0.1.0"))
    store = ExtensionStore(catalog=_catalog(), lockfile=lock)
    store.install(sample.NAME)
    store.remove(sample.NAME)
    assert load_manifests() == ()


def test_install_refuses_a_bundle_only_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _pinned_lock(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="bundle-only"):
        ExtensionStore(catalog=_catalog(disabled=True), lockfile=lock).install(sample.NAME)


def test_install_refuses_a_name_absent_from_the_catalog(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not in the store catalog"):
        ExtensionStore(catalog=Catalog(), lockfile=tmp_path / "ufo.lock").install(sample.NAME)


def test_install_refuses_a_name_not_installed_in_the_environment(tmp_path: Path) -> None:
    catalog = Catalog(extensions=(CatalogEntry(name="ghost", version="1.0.0"),))
    with pytest.raises(RuntimeError, match="not installed in this environment"):
        ExtensionStore(catalog=catalog, lockfile=tmp_path / "ufo.lock").install("ghost")


def test_remove_refuses_an_uninstalled_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _pinned_lock(tmp_path, monkeypatch)
    write_lockfile(lock, Lockfile(ufo_version="0.1.0"))
    with pytest.raises(ValueError, match="not installed"):
        ExtensionStore(catalog=_catalog(), lockfile=lock).remove(sample.NAME)


def test_search_marks_installed_and_bundle_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _pinned_lock(tmp_path, monkeypatch)
    write_lockfile(lock, Lockfile(ufo_version="0.1.0"))
    store = ExtensionStore(catalog=_catalog(), lockfile=lock)
    (before,) = store.search("")
    assert before.installed is False and before.disabled is False
    store.install(sample.NAME)
    (after,) = store.search("sample")
    assert after.installed is True
    (bundle_only,) = ExtensionStore(catalog=_catalog(disabled=True), lockfile=lock).search("")
    assert bundle_only.disabled is True


def test_a_drifted_digest_fails_boot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock = _pinned_lock(tmp_path, monkeypatch)
    write_lockfile(
        lock,
        Lockfile(
            ufo_version="0.1.0",
            extensions=(
                ExtensionPin(name=sample.NAME, version=sample.VERSION, digest="sha256:deadbeef"),
            ),
        ),
    )
    with pytest.raises(RuntimeError, match="does not match pinned"):
        load_manifests()


def test_a_pinned_but_missing_extension_fails_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _pinned_lock(tmp_path, monkeypatch)
    write_lockfile(
        lock,
        Lockfile(
            ufo_version="0.1.0",
            extensions=(ExtensionPin(name="ghost", version="1.0.0", digest="sha256:x"),),
        ),
    )
    with pytest.raises(RuntimeError, match="not installed"):
        load_manifests()


def test_digest_covers_a_tampered_non_entry_file_in_a_multi_file_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "tamperpkg"
    package.mkdir()
    (package / "__init__.py").write_text("def manifest():\n    return None\n")
    other = package / "other.py"
    other.write_text("VALUE = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    entry = EntryPoint(name="tamper", value="tamperpkg:manifest", group="ufo.extension")
    before = extension_digest(entry)
    other.write_text("VALUE = 2\n")
    after = extension_digest(entry)
    assert before != after


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_an_installed_extension_fires_through_the_loader(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _pinned_lock(tmp_path, monkeypatch)
    write_lockfile(lock, Lockfile(ufo_version="0.1.0"))
    ExtensionStore(catalog=_catalog(), lockfile=lock).install(sample.NAME)
    workspace_id = await _workspace()
    await _seed_note(workspace_id)
    manifests = load_manifests()
    runner = JobRunner(bindings=bindings_from(manifests, ()), manifests=manifests)
    with ws(workspace_id):
        for workspace_id in await runner.candidates(f"{sample.NAME}:{JOB_NAME}"):
            await runner.fire(f"{sample.NAME}:{JOB_NAME}", workspace_id)
        scoped = ScopedStore(extension=sample.NAME)
        assert (await scoped.get(JOB_KEY) or {})["ran"] is True


def test_bundle_pins_a_bundle_only_extension_and_writes_a_build_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pinned_lock(tmp_path, monkeypatch)
    write_lockfile(tmp_path / "ufo.lock", Lockfile(ufo_version="0.1.0"))
    config_path = tmp_path / "ufo.toml"
    config_path.write_text(CONFIG_TOML)
    out = tmp_path / "out"
    result = Bundle(
        config_path=config_path,
        catalog=_catalog(disabled=True),
        out=out,
        wheels=(_sample_wheel(tmp_path),),
        client_binary=_sample_client(tmp_path),
        constraints=LOCKED,
    ).build()
    assert [pin.name for pin in result.pins] == [sample.NAME]
    assert result.config.read_text() == CONFIG_TOML
    locked = Lockfile.model_validate_json(result.lockfile.read_text())
    assert {pin.name for pin in locked.extensions} == {sample.NAME}
    dockerfile = result.dockerfile.read_text()
    assert dockerfile.startswith("FROM python:3.12-slim-trixie\n")
    assert "apt-get install -y --no-install-recommends libpcre2-8-0" in dockerfile
    assert (
        "dpkg --compare-versions \"$(dpkg-query -W -f='${Version}' libpcre2-8-0)\" "
        "ge 10.46-1~deb13u3" in dockerfile
    )
    assert "rm -rf /var/lib/apt/lists/*" in dockerfile
    assert (out / BUNDLE_CONSTRAINTS_NAME).read_text() == LOCKED
    assert (
        f"COPY ufo-{ufo_version()}-py3-none-any.whl {BUNDLE_CONSTRAINTS_NAME} {BUNDLE_WHEELS_DIR}/"
        in dockerfile
    )
    assert (
        f"RUN pip install --no-cache-dir -c {BUNDLE_WHEELS_DIR}/{BUNDLE_CONSTRAINTS_NAME} "
        f"{BUNDLE_WHEELS_DIR}/ufo-" in dockerfile
    )
    assert "ufo-ext-" not in dockerfile
    assert 'ENTRYPOINT ["ufoctl"]' in dockerfile
    assert dockerfile.rstrip().endswith('CMD ["serve"]')


def test_bundle_carries_the_sandbox_client_named_by_the_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pinned_lock(tmp_path, monkeypatch)
    write_lockfile(tmp_path / "ufo.lock", Lockfile(ufo_version="0.1.0"))
    config_path = tmp_path / "ufo.toml"
    config_path.write_text(CONFIG_TOML)
    result = Bundle(
        config_path=config_path,
        catalog=_catalog(disabled=True),
        out=tmp_path / "out",
        wheels=(_sample_wheel(tmp_path),),
        client_binary=_sample_client(tmp_path),
        constraints=LOCKED,
    ).build()
    assert result.client_binary.read_bytes() == b"sandbox-client"
    dockerfile = result.dockerfile.read_text()
    assert "COPY --chmod=0555 ufo-sandbox-client /usr/local/bin/ufo-sandbox-client" in dockerfile
    assert "ENV UFO_CLIENT_BINARY=/usr/local/bin/ufo-sandbox-client" in dockerfile


def test_bundle_pins_the_wheel_instead_of_the_installed_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pinned_lock(tmp_path, monkeypatch)
    write_lockfile(tmp_path / "ufo.lock", Lockfile(ufo_version="0.1.0"))
    config_path = tmp_path / "ufo.toml"
    config_path.write_text(CONFIG_TOML)
    wheel = _sample_wheel(tmp_path)
    with ZipFile(wheel, "a") as archive:
        archive.writestr("ufo_ext_sample/probe.txt", b"shipped only\n")
    result = Bundle(
        config_path=config_path,
        catalog=_catalog(disabled=True),
        out=tmp_path / "out",
        wheels=(wheel,),
        client_binary=_sample_client(tmp_path),
        constraints=LOCKED,
    ).build()
    entry = discovered()[sample.NAME][1]
    assert result.pins[0].digest != extension_digest(entry)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_bundle_boots_its_pinned_extension_on_a_clean_lockfile(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pinned_lock(tmp_path, monkeypatch)
    write_lockfile(tmp_path / "ufo.lock", Lockfile(ufo_version="0.1.0"))
    config_path = tmp_path / "ufo.toml"
    config_path.write_text(CONFIG_TOML)
    bundle = Bundle(
        config_path=config_path,
        catalog=_catalog(disabled=True),
        out=tmp_path / "out",
        wheels=(_sample_wheel(tmp_path),),
        client_binary=_sample_client(tmp_path),
        constraints=LOCKED,
    )
    result = bundle.build()
    monkeypatch.setenv(LOCKFILE_PATH_ENV, str(result.lockfile))
    assert [manifest.name for manifest in load_manifests()] == [sample.NAME]
    workspace_id = await _workspace()
    await _seed_note(workspace_id)
    manifests = load_manifests()
    runner = JobRunner(bindings=bindings_from(manifests, ()), manifests=manifests)
    with ws(workspace_id):
        for workspace_id in await runner.candidates(f"{sample.NAME}:{JOB_NAME}"):
            await runner.fire(f"{sample.NAME}:{JOB_NAME}", workspace_id)
        scoped = ScopedStore(extension=sample.NAME)
        assert (await scoped.get(JOB_KEY) or {})["ran"] is True
