from evals.suites.member_add_notify import (
    CASES,
    _cleanup_partner_grant,
    _cleanup_transcript_access,
    _seed_archived_app,
    _seed_plain_colleague,
)

CLEANED = {
    "authored-role-change-is-not-an-add": _seed_plain_colleague,
    "authored-web-grant": _cleanup_partner_grant,
    "authored-transcript-acknowledgement": _cleanup_transcript_access,
    "authored-app-restore": _seed_archived_app,
}


def test_each_seeding_case_that_a_run_changes_pairs_its_cleanup() -> None:
    by_name = {case.name: case for case in CASES}
    for name, cleanup in CLEANED.items():
        assert by_name[name].cleanup is cleanup, name
    for case in CASES:
        if case.name not in CLEANED:
            assert case.cleanup is None, case.name
