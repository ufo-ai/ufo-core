import importlib.util
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).parents[2]
WORKFLOW = _ROOT / ".github" / "workflows" / "claude-code-review.yml"
SKILL = _ROOT / ".claude" / "skills" / "review-pull-request" / "SKILL.md"
AUTHOR_SKILL = _ROOT / ".claude" / "skills" / "babysit-prs" / "SKILL.md"
SCRIPT = _ROOT / ".github" / "scripts" / "prior_findings.py"
LEDGER_SCRIPT = _ROOT / ".github" / "scripts" / "review_ledger.py"

_SPEC = importlib.util.spec_from_file_location("prior_findings", SCRIPT)
prior = importlib.util.module_from_spec(_SPEC)
sys.modules["prior_findings"] = prior
_SPEC.loader.exec_module(prior)

_LEDGER_SPEC = importlib.util.spec_from_file_location("review_ledger", LEDGER_SCRIPT)
ledger = importlib.util.module_from_spec(_LEDGER_SPEC)
sys.modules["review_ledger"] = ledger
_LEDGER_SPEC.loader.exec_module(ledger)


def test_claude_code_review_uses_its_own_skill_with_permissive_bash() -> None:
    workflow = WORKFLOW.read_text()
    allowed_tools = re.search(r'--allowedTools "([^"]+)"', workflow)
    allowed_bots = re.search(r"allowed_bots: ([^\n]+)", workflow)

    assert allowed_tools is not None
    assert allowed_bots is not None
    assert set(allowed_bots.group(1).split(",")) == {
        "github-actions",
        "claude",
        "flyingobject-ai-ufo",
    }
    assert {"Skill", "Bash"} <= set(allowed_tools.group(1).split(","))
    assert "--model claude-opus-5" in workflow
    assert "Use the review-pull-request skill" in workflow
    assert "plugin_marketplaces" not in workflow
    assert "plugins:" not in workflow
    assert "fetch-depth: 0" in workflow


def test_claude_code_review_skill_requires_a_current_head_verdict() -> None:
    skill = SKILL.read_text()

    assert skill.index("root `README.md`") < skill.index("Read the pull request")
    assert "`AGENTS.md` when present; otherwise read `CLAUDE.md`" in skill
    assert "## Gotchas" in skill
    assert "pipe, redirect, or compound Bash command" in skill
    assert "Re-read `headRefOid` immediately before writing" in skill
    assert "--request-changes" in skill
    assert "--approve" in skill
    assert "only because Claude authored the pull request" in skill
    assert "<!-- claude-review-verdict head=<full head SHA> verdict=APPROVED -->" in skill
    assert "<!-- claude-review-verdict head=<full head SHA> verdict=CHANGES_REQUESTED -->" in skill
    assert "Never use the marker for another review failure" in skill


def test_claude_code_review_skill_keeps_prose_findings_out_of_the_gate() -> None:
    skill = SKILL.read_text()

    assert "### Blocking and advisory findings" in skill
    assert "Sort every kept finding into one of three buckets" in skill
    assert "Blocking, because it changes what the code does" in skill
    assert "Blocking, because the diff breaks a rule the repository writes down" in skill
    assert "design findings live here" in skill
    assert "cannot anchor to a written rule is a preference" in skill
    assert "A structural or design finding fits neither blocking bucket" in skill
    assert "prose inaccuracy that does not change behavior" in skill
    assert "do not let it hold the verdict" in skill
    assert "When every remaining finding is advisory" in skill
    assert "the author answers them in a reply, without a new head" in skill
    assert "For each validated issue, blocking or advisory" in skill
    assert "- Blocking findings of either kind: `gh pr review <number> --request-changes" in skill
    assert "- No blocking findings: `gh pr review <number> --approve --body" in skill
    assert "must be fixed before merge" not in skill
    assert skill.index("Validate every returned finding") < skill.index(
        "### Blocking and advisory findings"
    )


def test_claude_code_review_skill_keeps_published_text_terse() -> None:
    prose = " ".join(SKILL.read_text().split())

    assert "one paragraph of at most 60 words" in prose
    assert "State only the failure, its consequence, and the required change" in prose
    assert "Do not narrate the investigation, restate the diff, or include test commands" in prose
    assert "The verdict body is one sentence: the round, then the finding ids by bucket" in prose
    assert "Never repeat inline evidence in the verdict" in prose
    assert "never restate continuity in prose" in prose
    assert '"unresolved from rounds 1, 2 and 3" is not a verdict\'s work' in prose
    assert "Round 5. Blocking: F3, F7. New: F12. Advisory: F9." in prose


def test_claude_code_review_skill_verifies_prior_rounds_instead_of_re_deriving() -> None:
    prose = " ".join(SKILL.read_text().split())

    assert "python3 .github/scripts/prior_findings.py" in prose
    assert "`0` is a first review; `N` makes this round `N + 1`; `null`" in prose
    assert "never as a first review" in prose
    assert "Step 6 reports a null `rounds`" in prose
    assert "Read the `verdicts` bodies before deriving anything" in prose
    assert "a round that skips them argues with itself" in prose
    assert "Tempted to read prior findings with `gh pr view` or a filtered `gh api`" in prose
    assert "Each agent finishes a changed file within its own lens" in prose
    assert "Closure across all three" in prose
    assert "On a follow-up round — `rounds` is anything but `0`" in prose
    assert "verify each earlier finding is actually fixed at this head" in prose
    assert "`anchor_sha`, the head the newest earlier round reviewed" in prose
    assert "`git cat-file -e <anchor_sha>^{commit}`" in prose
    assert "`git fetch origin <anchor_sha>`" in prose
    assert "`git diff <anchor_sha>..<head sha>`" in prose
    assert prose.index("`git fetch origin <anchor_sha>`") < prose.index(
        "`git diff <anchor_sha>..<head sha>`"
    )
    assert "A null `anchor_sha` leaves no range" in prose
    assert "A follow-up round carries a null `anchor_sha`" in prose
    assert "No prior finding named a head" not in prose
    assert "never on code no commit since `anchor_sha` has touched" in prose
    assert (
        "The reply decides how much work verifying costs, never whether the finding holds" in prose
    )
    assert "only the code at this head settles it" in prose
    assert prose.index("prior_findings.py") < prose.index("## Review")


def test_claude_code_review_skill_spends_one_round_per_subject() -> None:
    prose = " ".join(SKILL.read_text().split())

    assert "### Write each finding to be the last one on its subject" in prose
    assert "what one finding leaves unsaid the next round bills for" in prose
    assert "**Name the class, not the instance.**" in prose
    assert "the finding names them all in the one comment" in prose
    assert "**Name what the fix must preserve.**" in prose
    assert "breaks what the finding did not mention is the finding's defect" in prose
    assert "**Close new code the round it appears.**" in prose
    assert "one finding spread over three" in prose
    assert "the round count below is its limit" in prose
    assert prose.index("### Write each finding to be the last one on its subject") < prose.index(
        "On a follow-up round"
    )

    for gotcha in (
        "The code names several cases and the diff answers one",
        "A finding asks for a deletion, a replacement, or a move",
        "A follow-up commit introduced a mechanism that did not exist before",
        "A new finding lands on a file no commit in the `anchor_sha` range touched",
    ):
        assert gotcha in prose


def test_claude_code_review_skill_drops_findings_that_buy_a_round() -> None:
    prose = " ".join(SKILL.read_text().split())

    assert "Two further drops on a follow-up round" in prose
    assert "drop a new finding on a file no commit in `<anchor_sha>..<head sha>` touched" in prose
    assert "raising it now buys a round for code this pull request is done with" in prose
    assert "The range bounds new work only" in prose
    assert (
        "an earlier finding this head does not resolve is verified wherever its file sits, and its "
        "row keeps holding the gate without a second comment" in prose
    )
    assert "its row holds the gate with no second comment" in prose
    assert "Drop a finding that restores what an earlier round's finding removed" in prose
    assert "reverses a disposition an earlier round settled" in prose
    assert "stating which earlier finding was wrong and why" in prose
    assert (
        prose.index("Validate every returned finding")
        < prose.index("Two further drops on a follow-up round")
        < prose.index("### Blocking and advisory findings")
    )


def test_every_round_count_the_script_emits_has_an_instruction_in_the_skill() -> None:
    prose = " ".join(SKILL.read_text().split())
    claude = prior.comment(
        {
            "id": 1,
            "user": {"login": "claude[bot]"},
            "path": "a.py",
            "body": "",
            "created_at": "2026-01-01T00:00:00Z",
        }
    )
    unanchored = prior.prior_round((claude,), (), ())

    assert prior.prior_round((), (), ()).rounds == 0
    assert "`0` is a first review" in prose

    assert unanchored.rounds == 1
    assert "`N` makes this round `N + 1`" in prose

    assert prior.UNDETERMINED.rounds is None
    assert "`null` means the fetch failed" in prose
    assert "Step 6 reports a null `rounds`" in prose

    assert unanchored.anchor_sha is None
    assert "A null `anchor_sha` leaves no range" in prose
    assert "A follow-up round carries a null `anchor_sha`" in prose

    assert "a marker-only verdict carries no body and so no count" in " ".join(
        AUTHOR_SKILL.read_text().split()
    )


def test_every_verdict_the_script_emits_is_read_by_the_skill() -> None:
    prose = " ".join(SKILL.read_text().split())
    body = "Round 3. Four blocking findings."
    emitted = prior.prior_round(
        (),
        (
            prior.review(
                {
                    "user": {"login": "claude[bot]"},
                    "state": "CHANGES_REQUESTED",
                    "body": body,
                    "submitted_at": "2026-01-01T00:00:00Z",
                }
            ),
        ),
        (),
    ).verdicts

    assert emitted == (body,)
    assert "every earlier round's verdict summary under `verdicts`" in prose
    assert "they say what each earlier round closed, carried forward, and settled" in prose


def test_a_marker_round_is_counted_and_carries_no_summary() -> None:
    prose = " ".join(SKILL.read_text().split())
    marker = f"<!-- claude-review-verdict head={'a' * 40} verdict=APPROVED -->"
    result = prior.prior_round(
        (),
        (),
        (
            prior.issue_comment(
                {
                    "user": {"login": "claude[bot]"},
                    "body": marker,
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ),
        ),
    )

    assert (result.rounds, result.verdicts) == (1, ())
    assert "or `Round unknown.` where `rounds` came back `null`" in prose
    assert (
        "the one verdict that carries no body, so a round published that way carries no count"
        in prose
    )


def test_claude_code_review_skill_asks_prose_to_be_deleted_not_reworded() -> None:
    prose = " ".join(SKILL.read_text().split())

    assert "A prose finding asks for the sentence's **deletion**, never its correction." in prose
    assert "Name the words to cut" in prose
    assert "name the assertion that must carry it instead" in prose
    assert (
        "comes back as a different sentence, which is a new claim you have to read again" in prose
    )
    assert "So one sentence gets one prose finding." in prose
    assert "ask for the deletion once and no further" in prose
    assert "drop it and leave it to the author" in prose
    assert "About to publish a prose finding on a sentence an earlier round already raised" in prose
    assert "Never negotiate a wording." in prose
    assert prose.index("Advisory: a comment, docstring, or prose inaccuracy") < prose.index(
        "A prose finding asks for the sentence's **deletion**"
    )


def test_claude_code_review_skill_names_the_split_when_the_round_count_stops_falling() -> None:
    prose = " ".join(SKILL.read_text().split())

    assert "### When the round count stops falling" in prose
    assert "`rounds` is the one number that says whether this review is converging" in prose
    assert "it never terminates on a diff that grows a mechanism every round" in prose
    assert (
        "each fix then arrives as its own first review and the blocking count holds flat" in prose
    )
    assert "When `rounds` is 3 or more and the recent ranges each introduced a mechanism" in prose
    assert 'quote `CLAUDE.md`\'s "Split into independently reviewable units"' in prose
    assert "That is a blocking finding of the second kind" in prose
    assert "Nothing here licenses a softer review of what is in front of you" in prose
    assert "the count changes what the verdict asks for, never how the diff is read" in prose
    assert "`rounds` is 3 or more and each recent range brought a new mechanism" in prose

    assert "Open the verdict body with `Round <rounds + 1>.`" in prose
    assert "or `Round unknown.` where `rounds` came back `null`" in prose
    assert "makes a cycling review visible to the author and to the human" in prose
    assert prose.index("### When the round count stops falling") < prose.index(
        "### Blocking and advisory findings"
    )
    assert prose.index("Open the verdict body with `Round <rounds + 1>.`") < prose.index(
        "For each validated issue, blocking or advisory"
    )


def test_claude_code_review_skill_keeps_one_ledger_it_edits_in_place() -> None:
    prose = " ".join(SKILL.read_text().split())

    assert "python3 .github/scripts/review_ledger.py" in prose
    assert "the `F<n>` id it keeps for life" in prose
    assert "`next_id` for the first finding you publish this round" in prose
    assert "`comment_id` for the one ledger comment to edit" in prose
    assert "A null `rows` means the fetch failed" in prose
    assert "A null `next_id` publishes the finding without an id prefix or marker" in prose
    assert "The next readable run derives its id from thread creation order" in prose
    assert "a projection of the evidence step 6 reads, never a second source" in prose
    assert "a deleted ledger comment loses a rendering and no state" in prose
    assert "opening with `<id> \u2014 ` and ending with `<!-- claude-finding id=<id> -->`" in prose
    assert "taking ids from `next_id` upward in publication order" in prose
    assert (
        "The visible prefix is what a human cites and the marker is what the script reads" in prose
    )
    assert (
        "`gh api --method PATCH repos/<owner>/<repo>/issues/comments/<comment_id> -F body=@<file>`"
        in prose
    )
    assert "`gh pr comment <number> --body-file <file>`" in prose
    assert "The pull request carries exactly one ledger, at one URL" in prose
    assert "so never post a second" in prose
    assert "Publish the script's `body` unedited" in prose
    assert "the ledger carries a row for every finding this round published" in prose
    assert prose.index("review_ledger.py") < prose.index("## Review")
    assert prose.index("opening with `<id> \u2014 `") < prose.index("## Publish the verdict")


def test_claude_code_review_skill_renders_the_ledger_after_the_round_it_reports() -> None:
    prose = " ".join(SKILL.read_text().split())

    assert "The ledger is published last, once the evidence it renders exists" in prose
    assert (
        "With every inline finding created and every `verified fixed` reply written, run step 7's "
        "script again and save its `body` under `$RUNNER_TEMP`" in prose
    )
    assert "which the `body` from step 7 could not know" in prose
    assert "With the second run's `comment_id` set, edit that comment in place" in prose
    assert "About to render the ledger before this round's comments and replies exist" in prose
    assert prose.index("For each validated issue, blocking or advisory") < prose.index(
        "The ledger is published last"
    )
    assert prose.index("`F<n> — verified fixed: <head sha>`, and on nothing else") < prose.index(
        "The ledger is published last"
    )


def test_claude_code_review_skill_publishes_one_finding_once() -> None:
    prose = " ".join(SKILL.read_text().split())
    author = " ".join(AUTHOR_SKILL.read_text().split())

    assert "A finding is published once, on one thread, for the life of the pull request" in prose
    assert (
        "A `todo` row the head does not resolve is carried forward by that row and by its id in "
        "this round's verdict, never by a second inline comment" in prose
    )
    assert "One id is one row" in prose
    assert "a re-publication renders the same finding twice" not in prose
    assert "Publish an inline comment only for a finding no round has published" in prose
    assert "A `todo` row is verified at this head and stays `todo`" in prose
    assert (
        "About to publish an inline comment for a `todo` row this head still does not resolve"
        in prose
    )
    assert "Re-publish a finding the head does not resolve" not in prose
    assert "A prior finding's thread is open but the newest summary does not name its id" in author


def test_claude_code_review_skill_moves_a_ledger_row_only_on_evidence() -> None:
    prose = " ".join(SKILL.read_text().split())

    assert "Step 7's `state` says which findings are still yours to verify" in prose
    assert "A `todo` row is verified at this head" in prose
    assert "A `done` row was verified once and is not verified again" in prose
    assert "A defect that returns after a `done` is a new id" in prose
    assert "No row moves backwards" in prose
    assert "one reply on its own thread reading `F<n> — verified fixed: <head sha>`" in prose
    assert "arrives as `todo` and is bought again" in prose

    for gotcha in (
        "Step 7 reports null `rows`",
        "A finding an earlier round published carries no `<!-- claude-finding id=<id> -->`",
        "A row you verified is still `todo` next round",
    ):
        assert gotcha in prose

    assert "Publish under that id" not in prose
    assert "Use that derived id in the verdict and any `verified fixed` reply" in prose


def test_every_ledger_state_the_script_emits_has_an_instruction_in_both_skills() -> None:
    reviewer = " ".join(SKILL.read_text().split())
    author = " ".join(AUTHOR_SKILL.read_text().split())
    states = {ledger.TODO, ledger.DONE}

    assert states == {"todo", "done"}
    for state in states:
        assert f"`{state}`" in reviewer
        assert f"`{state}`" in author

    assert ledger.UNDETERMINED.rows is None
    assert "A null `rows` means the fetch failed" in reviewer
    assert "Step 7 reports null `rows`" in reviewer

    assert ledger.LEDGER_MARKER == "<!-- claude-review-ledger -->"
    assert ledger.LEDGER_MARKER in author
