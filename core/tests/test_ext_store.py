import importlib
from importlib.metadata import EntryPoint
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_sample as sample

from ufo.bundle import Bundle
from ufo.db import workspace_tx
from ufo.ext.context import ScopedStore
from ufo.ext.loader import (
    LOCKFILE_PATH_ENV,
    ExtensionPin,
    Lockfile,
    extension_digest,
    load_manifests,
    write_lockfile,
)
from ufo.ext.store import Catalog, CatalogEntry, ExtensionStore, ufo_version
from ufo.jobs import JobRunner, bindings_from
from ufo.schema import tables
from ufo.workspace import ws

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


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


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


async def test_an_installed_extension_fires_through_the_loader(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _pinned_lock(tmp_path, monkeypatch)
    write_lockfile(lock, Lockfile(ufo_version="0.1.0"))
    ExtensionStore(catalog=_catalog(), lockfile=lock).install(sample.NAME)
    workspace_id = await _workspace()
    runner = JobRunner(bindings=bindings_from(load_manifests(), ()))
    await runner.fire(f"{sample.NAME}:{sample.JOB_NAME}")
    with ws(workspace_id):
        scoped = ScopedStore(extension=sample.NAME)
        assert await scoped.get(sample.JOB_KEY) == {"ran": True}


def test_bundle_pins_a_bundle_only_extension_and_writes_a_build_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pinned_lock(tmp_path, monkeypatch)
    write_lockfile(tmp_path / "ufo.lock", Lockfile(ufo_version="0.1.0"))
    config_path = tmp_path / "ufo.toml"
    config_path.write_text(CONFIG_TOML)
    out = tmp_path / "out"
    result = Bundle(config_path=config_path, catalog=_catalog(disabled=True), out=out).build()
    assert [pin.name for pin in result.pins] == [sample.NAME]
    assert result.config.read_text() == CONFIG_TOML
    locked = Lockfile.model_validate_json(result.lockfile.read_text())
    assert {pin.name for pin in locked.extensions} == {sample.NAME}
    dockerfile = result.dockerfile.read_text()
    assert dockerfile.startswith("FROM python:3.12-slim")
    assert f"COPY ufo-{ufo_version()}-py3-none-any.whl" in dockerfile
    assert "RUN pip install --no-cache-dir /tmp/ufo-" in dockerfile
    assert "ufo-ext-" not in dockerfile
    assert 'ENTRYPOINT ["ufoctl"]' in dockerfile
    assert dockerfile.rstrip().endswith('CMD ["serve"]')


async def test_a_bundle_boots_its_pinned_extension_on_a_clean_lockfile(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pinned_lock(tmp_path, monkeypatch)
    write_lockfile(tmp_path / "ufo.lock", Lockfile(ufo_version="0.1.0"))
    config_path = tmp_path / "ufo.toml"
    config_path.write_text(CONFIG_TOML)
    bundle = Bundle(config_path=config_path, catalog=_catalog(disabled=True), out=tmp_path / "out")
    result = bundle.build()
    monkeypatch.setenv(LOCKFILE_PATH_ENV, str(result.lockfile))
    assert [manifest.name for manifest in load_manifests()] == [sample.NAME]
    workspace_id = await _workspace()
    runner = JobRunner(bindings=bindings_from(load_manifests(), ()))
    await runner.fire(f"{sample.NAME}:{sample.JOB_NAME}")
    with ws(workspace_id):
        scoped = ScopedStore(extension=sample.NAME)
        assert await scoped.get(sample.JOB_KEY) == {"ran": True}
