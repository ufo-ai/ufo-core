from pathlib import Path

SKILL = Path(__file__).parents[2] / ".claude" / "skills" / "babysit-prs" / "SKILL.md"


def test_babysit_prs_skill_keeps_review_stage_updates_terse() -> None:
    skill = SKILL.read_text()

    assert (
        skill.index("never rerun an unchanged mechanism")
        < skill.index("You speak only where this file names a statement you owe")
        < skill.index("## What you may and may not do")
    )
    assert "a blocker or a design decision that is the user's\nto make" in skill
    assert "Everything else\nis silent" in skill
    assert "a routine push, waiting itself, a check you are watching, a finding you fixed" in skill
    assert "`Two valid issues. Fixing.`" in skill
    assert "A reply is at most two sentences" in skill
    assert "Reply on the thread, never as a new PR-level comment" in skill


def test_babysit_prs_skill_still_owes_every_report_it_names() -> None:
    skill = SKILL.read_text()

    for owed in (
        "why you believe a cause is external before you rerun",
        "a push onto an approved head",
        "a completed\nwait this file tells you to report",
        "the merge-ready state or truthful red at the end",
    ):
        assert owed in skill

    assert "Say why you believe it's external when you rerun" in skill
    assert "then report and act" in skill
    assert "surface that" in skill


def test_babysit_prs_skill_touches_only_this_session_s_pull_requests() -> None:
    skill = SKILL.read_text()

    assert skill.index("Touch only the pull requests named in the invocation") < skill.index(
        "root `README.md`"
    )
    assert "only the PRs this session\nopened or pushed to" in skill
    assert "never widen to `gh pr list --author @me`" in skill
    assert "never commit, reply, or resolve a thread on a PR outside that set" in skill
    assert "sweep the PRs this session opened or pushed to" in skill


def test_babysit_prs_skill_spends_one_push_per_review_round() -> None:
    skill = SKILL.read_text()

    assert "## What earns a commit" in skill
    assert "**the proof of behavior the diff leaves unpinned**" in skill
    assert "**any finding anchored\nto a written rule**" in skill
    assert "is a defect whatever its category, naming and prose included" in skill
    assert "a preference is never worth one" in skill
    assert "A prose finding therefore has exactly two ends and no third" in skill
    assert (
        "a written rule backs it, so it is a defect and\nits sentence goes in **this** round's push"
        in skill
    )
    assert (
        "nothing backs it, so the reply says the sentence stands and\nthe thread resolves" in skill
    )
    assert "Never a deletion promised for a later push" in skill
    assert "a thread resolves only once the pushed\nhead carries the fix" in skill
    assert "it goes by **deletion** wherever the code reads without it" in skill
    assert "keeping only a public API's docstring, in the fewest words that are\ntrue" in skill

    assert "## One push per round, and it fixes the class" in skill
    assert "collect every finding at this head → address all of them → sweep" in skill
    assert "→ **one** push" in skill
    assert "never push while a finding from the same head is **unaddressed**" in skill
    assert "never hold the push for their round's\ncommitted fixes" in skill
    assert "`.claude/skills/review-pull-request/SKILL.md`" in skill
    assert "the surface your own fix just created" in skill
    assert "is the sweep you skipped, not a nitpick" in skill
    assert "That line is an example, not the inventory" in skill
    assert "Fix the cause that let it exist and every sibling it already reached" in skill
    assert "an instance fix comes back as the next round's finding" in skill
    assert "a branch, parameter, state, event, window, column" in skill
    assert "Its test lands in the same push" in skill


def test_babysit_prs_skill_spends_an_approval_only_on_an_advisory() -> None:
    skill = SKILL.read_text()

    assert "## An approval is not spent on an advisory" in skill
    assert "no advisory earns a push of its own" in skill
    assert "throws the approval away" in skill
    assert "A push the rest of this file requires is still owed at an approved head" in skill
    assert "a red required check, a `dirty`\nmerge state, a rule-backed finding" in skill
    assert "The approval body's advisory list is answered the same way" in skill
    assert "the second end above, every entry: one reply saying the sentence stands" in skill
    assert "No entry waits on a\nlater push" in skill
    assert "A thread you\nwere told to leave open — a design gap you surfaced — stays open" in skill

    rows = {
        row.split("|")[1].strip(): row.split("|")[2].strip()
        for row in skill.splitlines()
        if row.startswith("| ")
    }
    advisory_only = rows["`APPROVED` head whose only open findings are advisory"]
    owed = rows["`APPROVED` head with a red check, a `dirty` merge state, or a rule-backed finding"]
    prose_only = rows["A round raises only wording or docstrings with no written rule behind them"]

    assert advisory_only == (
        "Reply and resolve; no push. A push here would trade a green gate for another full round."
    )
    assert owed == "Fix and push — the approval was never a bar to that."
    assert prose_only == (
        "Reply that the sentence stands, and resolve. "
        "Nothing is owed, so nothing is promised for a later push."
    )


def test_babysit_prs_skill_never_merges() -> None:
    skill = SKILL.read_text()

    assert "Never merges." in skill
    assert "You may **not** merge a PR or enable auto-merge" in skill
    assert "do not merge and do not arm auto-merge yourself" in skill
