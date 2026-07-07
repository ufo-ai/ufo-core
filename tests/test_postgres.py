import pytest

from selfhost_k8s.contract import PostgresModel
from selfhost_k8s.postgres import (
    PG_ROLE_SEED_ENV,
    ensure_tenant_postgres,
    tenant_dsn,
    tenant_identifier,
    tenant_password,
)


def test_identifier_maps_dns_label_to_safe_postgres_name() -> None:
    assert tenant_identifier("acme-corp") == "selfhost_acme_corp"


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
    assert dsn.startswith("postgresql+asyncpg://selfhost_acme:")
    assert dsn.endswith("@pg.svc:5432/selfhost_acme")


async def test_rls_model_is_not_provisioned_in_this_cut() -> None:
    with pytest.raises(NotImplementedError):
        await ensure_tenant_postgres(PostgresModel.RLS, "dsn", "acme", "pg.svc:5432")
