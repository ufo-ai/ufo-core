"""The turn record contract is rendered from core's models and checked in where the portal reads
its types and replays its fixture, so a model change is a diff that runs the browser tests."""

from ufo_testsupport.contract import (
    CONTRACT_PATH,
    FOLD_FIXTURE_PATH,
    RECORD_FIXTURE_PATH,
    rendered_contract,
    rendered_fold,
    rendered_record,
)


def test_contract_types_are_fresh() -> None:
    assert CONTRACT_PATH.read_text() == rendered_contract(), (
        "stale record contract; regenerate: uv run python -m ufo_testsupport.contract"
    )


def test_record_fixture_is_fresh() -> None:
    assert RECORD_FIXTURE_PATH.read_text() == rendered_record(), (
        "stale record fixture; regenerate: uv run python -m ufo_testsupport.contract"
    )


def test_fold_fixture_is_fresh() -> None:
    assert FOLD_FIXTURE_PATH.read_text() == rendered_fold(), (
        "stale fold fixture; regenerate: uv run python -m ufo_testsupport.contract"
    )
