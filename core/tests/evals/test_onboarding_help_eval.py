"""The onboarding_help graders are pure decisions over one turn's tool calls, and every case must
name a reference file the hosted corpus actually ships: a renamed or misspelled reference would fail
every case forever, and a corpus the pack stopped carrying would make the whole suite unscoreable.
The negative-loading control is asserted from the same trajectory shape as the positive one, so a
grader that stopped seeing loads would pass those cases for the wrong reason."""

from json import dumps, loads
from re import findall
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from ufo_ext_slack.surface import (
    IDENTITY_BLOB_KEY,
    SLACK_BOT_TOKEN_SLOT,
    SLACK_SIGNING_SECRET_SLOT,
    SURFACE_SLACK,
    URL_VERIFIED_BLOB_KEY,
    SlackIdentity,
    bot_token_fingerprint,
    signing_secret_fingerprint,
    slack_installation_id,
)
from ufo_ext_slack.tools import SlackConnectInput, slack_connect_handler

from evals.harness.capability import CapabilityOutput, ToolInvocation, grading_statement
from evals.suites.onboarding_help import (
    CASES,
    CORPUS_SKILL,
    ONBOARDING_HELP_PACKS,
    REFERENCES_DIR,
    SLACK_EVAL_BOT_TOKEN,
    SLACK_EVAL_BOT_USER_ID,
    SLACK_EVAL_CURRENT_SECRET,
    SLACK_EVAL_IDENTITY,
    SLACK_EVAL_STALE_FINGERPRINT,
    SLACK_EVAL_TEAM_ID,
    SLACK_SETUP_SKILL,
    catalog_scorer,
    corpus_scorer,
    own_work_scorer,
    seed_slack_rotated_secret,
    slack_rotated_secret_scorer,
    slack_setup_scorer,
)
from ufo.access.credentials import CredentialStore
from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.ext.loader import load_manifests, skill_registry
from ufo.loop.engine import REQUEST_CREDENTIALS_TOOL
from ufo.schema import tables
from ufo.sdk.context import (
    CredentialAccess,
    ExtensionContext,
    ScopedStore,
    SurfaceInstallationAccess,
)
from ufo.tools.context import ToolContext
from ufo.turns.activity import SKILL_LOAD_TOOL
from ufo.workspace import init_workspace_credentials, ws

GETTING_STARTED = "getting-started.md"
BILLING = "billing-and-seats.md"
RESTRAINT_CASES = frozenset({"internal-probe", "uncovered-compliance"})
"""The cases whose point is what the answer withholds. They stay at one sample for the same reason
the own-work controls do."""
OVERVIEW_CASES = frozenset({"what-can-you-do", "show-me-what-you-can-do"})
"""The two phrasings a customer opens with. The description is the only thing that decides whether
either reaches the corpus, and both get the same answer, so both are pinned to it."""
UNROUTED = frozenset({"internal-only.md"})
"""The one reference no case grades a read of. Its rule is stated in `SKILL.md` itself, so an agent
that refuses a probe without opening the file is behaving correctly — requiring the read would fail
the right answer. `internal-probe` grades that refusal instead."""


def _load(skill: str = CORPUS_SKILL, is_error: bool = False, result: str = "") -> ToolInvocation:
    return ToolInvocation(
        name=SKILL_LOAD_TOOL,
        input={"name": skill},
        result=result or ("no such skill" if is_error else "mounted"),
        has_result=True,
        is_error=is_error,
    )


def _read(reference: str) -> ToolInvocation:
    return ToolInvocation(
        name="read",
        input={"path": f"{REFERENCES_DIR}/{reference}"},
        result="# Getting Started",
        has_result=True,
    )


def _output(*calls: ToolInvocation, response: str = "Here is your next step.") -> CapabilityOutput:
    return CapabilityOutput(response=response, calls=calls)


def _credential_request(slot: str, is_error: bool = False) -> ToolInvocation:
    return ToolInvocation(
        name=REQUEST_CREDENTIALS_TOOL,
        input={"prompts": [{"slot": slot, "prompt": "Enter it privately."}]},
        result="[private handoff redacted]",
        has_result=True,
        is_error=is_error,
    )


async def _slack_workspace() -> tuple[UUID, UUID]:
    workspace_id = uuid4()
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="be brief",
                model="claude-opus-4-8",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id


async def test_reading_the_matching_reference_passes() -> None:
    verdict = await corpus_scorer(GETTING_STARTED)(_output(_load(), _read(GETTING_STARTED)))

    assert verdict.passed
    assert GETTING_STARTED in verdict.reason


async def test_answering_without_reading_the_reference_fails() -> None:
    """The corpus's SKILL.md carries no product facts, so a load with no reference read is an
    answer from the model's own guesses about the product."""
    verdict = await corpus_scorer(GETTING_STARTED)(_output(_load()))

    assert not verdict.passed
    assert "never read" in verdict.reason


async def test_reading_a_different_reference_fails() -> None:
    verdict = await corpus_scorer(GETTING_STARTED)(_output(_load(), _read("billing-and-seats.md")))

    assert not verdict.passed


async def test_either_of_two_named_references_satisfies_the_read() -> None:
    """The seat case's rubric spans two files, so its grader accepts either. Passing on the second
    name is the half that a single-reference grader got wrong."""
    grader = corpus_scorer(BILLING, "troubleshooting.md")

    assert (await grader(_output(_load(), _read(BILLING)))).passed
    assert (await grader(_output(_load(), _read("troubleshooting.md")))).passed
    assert not (await grader(_output(_load(), _read(GETTING_STARTED)))).passed


async def test_the_catalogue_must_be_consulted_before_speaking_to_availability() -> None:
    """`catalog_scorer` is half the deterministic gate of the connectors case: a judge cannot tell a
    checked negative from an assumed one, so the trajectory carries it."""
    checked = ToolInvocation(
        name="list_external_tools", input={"query": "snowflake"}, result="[]", has_result=True
    )
    failed = ToolInvocation(
        name="list_external_tools",
        input={"query": "snowflake"},
        result="upstream refused",
        has_result=True,
        is_error=True,
    )

    assert (await catalog_scorer()(_output(_load(), checked))).passed
    assert not (await catalog_scorer()(_output(_load()))).passed
    assert not (await catalog_scorer()(_output(_load(), failed))).passed


async def test_the_overview_closes_by_loading_the_install_skill() -> None:
    assert (await slack_setup_scorer()(_output(_load(), _load(SLACK_SETUP_SKILL)))).passed
    assert not (await slack_setup_scorer()(_output(_load(), _read("capabilities.md")))).passed


async def test_a_failed_install_skill_load_does_not_count_as_closing() -> None:
    verdict = await slack_setup_scorer()(_output(_load(), _load(SLACK_SETUP_SKILL, is_error=True)))

    assert not verdict.passed
    assert "never mounted" in verdict.reason
    assert "no such skill" in verdict.reason


async def test_only_a_skill_load_counts_not_any_call_naming_the_skill() -> None:
    named = ToolInvocation(
        name="share_file",
        input={"name": SLACK_SETUP_SKILL},
        result="mounted",
        has_result=True,
    )
    verdict = await slack_setup_scorer()(_output(_load(), named))

    assert not verdict.passed
    assert "never loaded" in verdict.reason


async def test_the_passing_verdict_names_the_skill_it_credited() -> None:
    verdict = await slack_setup_scorer()(_output(_load(), _load(SLACK_SETUP_SKILL)))

    assert verdict.passed
    assert SLACK_SETUP_SKILL in verdict.reason


async def test_the_failure_reason_names_the_first_failed_attempt() -> None:
    verdict = await slack_setup_scorer()(
        _output(
            _load(),
            _load(SLACK_SETUP_SKILL, is_error=True, result="mount raced"),
            _load(SLACK_SETUP_SKILL, is_error=True, result="second reason"),
        )
    )

    assert not verdict.passed
    assert "mount raced" in verdict.reason


async def test_a_load_that_succeeded_before_a_later_failure_still_counts() -> None:
    verdict = await slack_setup_scorer()(
        _output(_load(), _load(SLACK_SETUP_SKILL), _load(SLACK_SETUP_SKILL, is_error=True))
    )

    assert verdict.passed


async def test_a_retried_install_skill_load_is_scored_on_the_attempt_that_succeeded() -> None:
    verdict = await slack_setup_scorer()(
        _output(_load(), _load(SLACK_SETUP_SKILL, is_error=True), _load(SLACK_SETUP_SKILL))
    )

    assert verdict.passed


async def test_rotated_secret_recovery_loads_the_skill_and_requests_the_signing_secret() -> None:
    verdict = await slack_rotated_secret_scorer()(
        _output(
            _load(SLACK_SETUP_SKILL),
            _credential_request(SLACK_SIGNING_SECRET_SLOT),
        )
    )

    assert verdict.passed


async def test_rotated_secret_recovery_credits_an_attempt_even_when_no_member_can_fill_it() -> None:
    verdict = await slack_rotated_secret_scorer()(
        _output(
            _load(SLACK_SETUP_SKILL),
            _credential_request(SLACK_SIGNING_SECRET_SLOT, is_error=True),
        )
    )

    assert verdict.passed


async def test_rotated_secret_recovery_rejects_a_wrong_private_request() -> None:
    wrong = await slack_rotated_secret_scorer()(
        _output(_load(SLACK_SETUP_SKILL), _credential_request("slack_bot_token"))
    )

    assert not wrong.passed


async def test_rotated_secret_seed_lands_pending_slack_state(db: None, tmp_path) -> None:
    workspace_id, agent_id = await _slack_workspace()
    store = CredentialStore(Fernet(Fernet.generate_key()))
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    init_workspace_credentials(store)
    try:
        with ws(workspace_id):
            await seed_slack_rotated_secret(workspace_id, agent_id, blob)
            await seed_slack_rotated_secret(workspace_id, agent_id, blob)
            assert await store.get(workspace_id, SLACK_BOT_TOKEN_SLOT) == SLACK_EVAL_BOT_TOKEN
            assert (
                await store.get(workspace_id, SLACK_SIGNING_SECRET_SLOT)
                == SLACK_EVAL_CURRENT_SECRET
            )
    finally:
        init_workspace_credentials(None)

    with ws(workspace_id):
        identity = SlackIdentity.model_validate_json(await blob.get(IDENTITY_BLOB_KEY))
        marker = loads(await blob.get(URL_VERIFIED_BLOB_KEY))

    assert identity.bot_token_fingerprint == bot_token_fingerprint(SLACK_EVAL_BOT_TOKEN)
    assert marker["fingerprint"] == SLACK_EVAL_STALE_FINGERPRINT
    assert marker["fingerprint"] != signing_secret_fingerprint(SLACK_EVAL_CURRENT_SECRET)


async def test_rotated_secret_seed_makes_slack_connect_pending(db: None, tmp_path) -> None:
    workspace_id, agent_id = await _slack_workspace()
    store = CredentialStore(Fernet(Fernet.generate_key()))
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    init_workspace_credentials(store)
    try:
        with ws(workspace_id):
            await seed_slack_rotated_secret(workspace_id, agent_id, blob)
            ext = ExtensionContext(
                store=ScopedStore(extension="slack"),
                credentials=CredentialAccess(
                    frozenset((SLACK_BOT_TOKEN_SLOT, SLACK_SIGNING_SECRET_SLOT))
                ),
                installations=SurfaceInstallationAccess(frozenset((SURFACE_SLACK,))),
            )
            ctx = cast(
                "ToolContext",
                SimpleNamespace(
                    blob=blob,
                    turn=SimpleNamespace(workspace_id=workspace_id),
                    ext=ext,
                    public_base_url=None,
                ),
            )
            result = await slack_connect_handler(
                ctx,
                SlackConnectInput(
                    method="manifest", user_description="checking the Slack connection"
                ),
            )
            await seed_slack_rotated_secret(workspace_id, agent_id, blob)
    finally:
        init_workspace_credentials(None)

    assert loads(result.content[0].text)["state"] == "pending"


async def test_rotated_secret_seed_refuses_every_foreign_slack_state(db: None, tmp_path) -> None:
    store = CredentialStore(Fernet(Fernet.generate_key()))
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))

    async def slack_state(workspace_id: UUID):
        async with workspace_tx() as connection:
            credentials = (
                await connection.execute(
                    sa.select(tables.credential.c.slot, tables.credential.c.ciphertext)
                    .where(tables.credential.c.workspace_id == workspace_id)
                    .order_by(tables.credential.c.slot)
                )
            ).all()
            installations = (
                await connection.execute(
                    sa.select(
                        tables.surface_installation.c.installation_id,
                        tables.surface_installation.c.agent_id,
                    ).where(
                        tables.surface_installation.c.workspace_id == workspace_id,
                        tables.surface_installation.c.surface == SURFACE_SLACK,
                    )
                )
            ).all()
        identity_key = IDENTITY_BLOB_KEY
        verified_key = URL_VERIFIED_BLOB_KEY
        identity = await blob.get(identity_key) if await blob.exists(identity_key) else None
        verified = await blob.get(verified_key) if await blob.exists(verified_key) else None
        return credentials, installations, identity, verified

    init_workspace_credentials(store)
    try:
        for mutation in (
            "slot",
            "installation",
            "identity_missing",
            "verified_missing",
            "bot_token",
            "signing_secret",
            "identity",
            "fingerprint",
        ):
            workspace_id, agent_id = await _slack_workspace()
            with ws(workspace_id):
                await seed_slack_rotated_secret(workspace_id, agent_id, blob)
                match mutation:
                    case "slot":
                        async with workspace_tx() as connection:
                            await connection.execute(
                                sa.delete(tables.credential).where(
                                    tables.credential.c.workspace_id == workspace_id,
                                    tables.credential.c.slot == SLACK_SIGNING_SECRET_SLOT,
                                )
                            )
                    case "installation":
                        async with workspace_tx() as connection:
                            await connection.execute(
                                sa.insert(tables.surface_installation).values(
                                    routes_ingress=True,
                                    workspace_id=workspace_id,
                                    surface=SURFACE_SLACK,
                                    installation_id=slack_installation_id(
                                        f"{SLACK_EVAL_TEAM_ID}OTHER"
                                    ),
                                    agent_id=agent_id,
                                    created_at=sa.func.now(),
                                    updated_at=sa.func.now(),
                                )
                            )
                    case "identity_missing":
                        await blob.delete(IDENTITY_BLOB_KEY)
                    case "verified_missing":
                        await blob.delete(URL_VERIFIED_BLOB_KEY)
                    case "bot_token":
                        await store.put(workspace_id, SLACK_BOT_TOKEN_SLOT, "xoxb-existing")
                    case "signing_secret":
                        await store.put(workspace_id, SLACK_SIGNING_SECRET_SLOT, "existing-secret")
                    case "identity":
                        identity = SLACK_EVAL_IDENTITY.model_copy(
                            update={"bot_user_id": f"{SLACK_EVAL_BOT_USER_ID}OTHER"}
                        )
                        await blob.put(
                            IDENTITY_BLOB_KEY,
                            identity.model_dump_json().encode(),
                        )
                    case "fingerprint":
                        await blob.put(
                            URL_VERIFIED_BLOB_KEY,
                            dumps({"fingerprint": "existing", "at": 1.0}).encode(),
                        )
                before = await slack_state(workspace_id)
                with pytest.raises(RuntimeError, match="disposable workspace without Slack state"):
                    await seed_slack_rotated_secret(workspace_id, agent_id, blob)
                assert await slack_state(workspace_id) == before
    finally:
        init_workspace_credentials(None)


async def test_a_companion_skill_alongside_the_corpus_passes() -> None:
    """A Slack question legitimately loads the install skill too — the corpus is what must be
    there, not what must be alone."""
    verdict = await corpus_scorer("slack-install.md")(
        _output(_load("slack-app-setup"), _load(), _read("slack-install.md"))
    )

    assert verdict.passed


async def test_loading_only_another_skill_fails_and_names_it() -> None:
    verdict = await corpus_scorer(GETTING_STARTED)(_output(_load("slack-app-setup")))

    assert not verdict.passed
    assert "slack-app-setup" in verdict.reason


async def test_a_retried_load_is_scored_on_the_attempt_that_succeeded() -> None:
    """Mounted files are written over the network in the sandbox this suite runs against, so a first
    load can fail and the retry is correct behavior — the verdict follows the successful attempt."""
    verdict = await corpus_scorer(GETTING_STARTED)(
        _output(_load(is_error=True), _load(), _read(GETTING_STARTED))
    )

    assert verdict.passed


async def test_loading_the_corpus_is_enough_when_no_reference_is_named() -> None:
    """The two guardrail cases grade a refusal, not a read: `internal-probe` and
    `uncovered-compliance` pass on the load alone, so this branch is their entire deterministic
    half."""
    verdict = await corpus_scorer()(_output(_load()))

    assert verdict.passed
    assert CORPUS_SKILL in verdict.reason


async def test_a_failed_corpus_load_fails() -> None:
    verdict = await corpus_scorer()(_output(_load(is_error=True)))

    assert not verdict.passed
    assert "failed" in verdict.reason


async def test_own_work_passes_only_while_the_corpus_stays_unloaded() -> None:
    assert (await own_work_scorer()(_output(_load("office-xlsx")))).passed
    assert not (await own_work_scorer()(_output(_load(), _read(GETTING_STARTED)))).passed


def test_the_cases_are_wired_to_the_graders_their_rubrics_need() -> None:
    """Pinning the mechanism is not pinning the wiring: reverting the seat case to one reference, or
    dropping the catalogue half of the connectors case, would leave every other test green."""
    seat = grading_statement(
        next(case for case in CASES if case.name == "teammate-has-no-seat").grader
    )
    connectors = grading_statement(
        next(case for case in CASES if case.name == "what-can-you-connect-to").grader
    )
    rotated = grading_statement(
        next(case for case in CASES if case.name == "slack-signing-secret-rotated").grader
    )
    rotated_case = next(case for case in CASES if case.name == "slack-signing-secret-rotated")

    assert set(_references_of(seat)) == {BILLING, "troubleshooting.md"}
    assert "list_external_tools" in connectors
    assert "capabilities.md" in _references_of(connectors)
    assert REQUEST_CREDENTIALS_TOOL in rotated
    assert SLACK_SIGNING_SECRET_SLOT in rotated
    assert rotated_case.seed is seed_slack_rotated_secret
    assert CASES[-1] is rotated_case


def test_sampling_follows_which_way_the_case_points() -> None:
    """A case that grades an act is re-run, because routing is about nine rounds in ten and one
    sample makes a clean suite a coin toss. A case that grades restraint is not, whichever way its
    grader points: "any sample passes" would let the one round that stayed quiet excuse the two that
    disclosed."""
    for case in CASES:
        restraint = case.name in RESTRAINT_CASES or "never loads" in grading_statement(case.grader)
        expected = 1 if restraint else 3
        assert case.samples == expected, f"{case.name} has samples={case.samples}"


def test_the_description_carries_the_overview_phrasing_verbatim() -> None:
    corpus = skill_registry(load_manifests(ONBOARDING_HELP_PACKS[0])).by_name[CORPUS_SKILL]
    description = corpus.description.casefold()

    for name in OVERVIEW_CASES:
        sent = next(case for case in CASES if case.name == name).message.casefold().rstrip("?")
        assert sent in description, f"{name!r} sends {sent!r}, which the description does not carry"


def test_the_description_opens_as_a_routing_trigger() -> None:
    corpus = skill_registry(load_manifests(ONBOARDING_HELP_PACKS[0])).by_name[CORPUS_SKILL]

    assert corpus.description.startswith("Load when")


def test_the_overview_cases_grade_the_slack_close() -> None:
    for name in OVERVIEW_CASES:
        statement = grading_statement(next(case for case in CASES if case.name == name).grader)

        assert SLACK_SETUP_SKILL in statement, f"{name!r} does not grade the install close"
        assert "capabilities.md" in _references_of(statement)


def test_the_pack_mounts_the_install_skill_the_close_names() -> None:
    mounted = skill_registry(load_manifests(ONBOARDING_HELP_PACKS[0])).by_name

    assert SLACK_SETUP_SKILL in mounted, sorted(mounted)


def test_every_case_names_a_reference_the_corpus_ships() -> None:
    corpus = skill_registry(load_manifests(ONBOARDING_HELP_PACKS[0])).by_name[CORPUS_SKILL]
    shipped = {
        path.removeprefix("references/")
        for path in corpus.mounted_files()
        if path.startswith("references/")
    }
    graded = {
        reference for case in CASES for reference in _references_of(grading_statement(case.grader))
    }

    assert graded, "the suite grades no reference file — the routing table is unproven"
    assert graded <= shipped, f"cases grade references the corpus does not ship: {graded - shipped}"
    assert graded == shipped - UNROUTED, (
        "every row of the routing table needs a case that reaches its file: "
        f"{shipped - UNROUTED - graded} ungraded"
    )


def _references_of(statement: str) -> tuple[str, ...]:
    """Every reference the statement names, not just the last — a case that accepts either of two
    files ("how do I add my team" is seating as much as joining) states both, and taking one would
    read the coverage set short and pass while a routing row went ungraded."""
    return tuple(findall(r"references/(\S+\.md)", statement))


def test_a_multi_reference_statement_contributes_every_file_it_names() -> None:
    both = grading_statement(corpus_scorer(GETTING_STARTED, "billing-and-seats.md"))

    assert _references_of(both) == (GETTING_STARTED, "billing-and-seats.md")
    assert _references_of(grading_statement(corpus_scorer())) == ()
