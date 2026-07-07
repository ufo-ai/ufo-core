import pytest

from ufo_control.postgres import (
    PG_ROLE_SEED_ENV,
    tenant_dbos_database,
    tenant_dsn,
    tenant_identifier,
    tenant_password,
    tenant_role,
)


def test_identifier_maps_dns_label_to_safe_postgres_name() -> None:
    assert tenant_identifier("acme-corp") == "ufo_acme_corp"


def test_rls_role_and_dbos_database_share_the_safe_suffix() -> None:
    assert tenant_role("acme-corp") == "ufo_t_acme_corp"
    assert tenant_dbos_database("acme-corp") == "ufo_dbos_acme_corp"


def test_password_is_deterministic_per_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PG_ROLE_SEED_ENV, "seed")
    assert tenant_password("acme") == tenant_password("acme")
    assert tenant_password("acme") != tenant_password("other")


def test_password_requires_a_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PG_ROLE_SEED_ENV, raising=False)
    with pytest.raises(RuntimeError):
        tenant_password("acme")


def test_dsn_points_the_role_at_its_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PG_ROLE_SEED_ENV, "seed")
    dsn = tenant_dsn("acme", "pg.svc:5432")
    assert dsn.startswith("postgresql+asyncpg://ufo_acme:")
    assert dsn.endswith("@pg.svc:5432/ufo_acme")
