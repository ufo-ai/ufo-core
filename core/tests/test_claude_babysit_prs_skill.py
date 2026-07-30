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
    prose = " ".join(SKILL.read_text().split())

    assert "## What earns a commit" in prose
    assert "**the proof of behavior the diff leaves unpinned**" in prose
    assert "**any finding anchored to a written rule**" in prose
    assert "is a defect whatever its category, naming and prose included" in prose
    assert "a preference is never worth one" in prose
    assert "A prose finding therefore has exactly two ends and no third" in prose
    assert "both are decided in **this** round" in prose
    assert "**The sentence goes**, by deletion, in this round's push" in prose
    assert "whenever a written rule backs the finding, whatever verdict this head carries" in prose
    assert "whenever the sentence is false at a head no `APPROVED` verdict has reached" in prose
    assert (
        "**Or the sentence stays** and one reply says why, and only where no written rule backs it"
        in prose
    )
    assert "it is true, or this head is already approved" in prose
    assert "the cut is worth less than the approval it would spend" in prose
    assert "Never a deletion promised for a later push" in prose
    assert "a sentence left standing is left standing for a stated reason, not owed" in prose
    assert "it goes by **deletion** wherever the code reads without it" in prose
    assert "keeping only a public API's docstring, in the fewest words that are true" in prose

    assert "## One push per round, and it fixes the class" in prose
    assert "collect every finding at this head → address all of them → sweep" in prose
    assert "→ **one** push" in prose
    assert "never push while a finding from the same head is **unaddressed**" in prose
    assert "never hold the push for their round's committed fixes" in prose
    assert "`.claude/skills/review-pull-request/SKILL.md`" in prose
    assert "the surface your own fix just created" in prose
    assert "is the sweep you skipped, not a nitpick" in prose
    assert "That line is an example, not the inventory" in prose
    assert "Fix the cause that let it exist and every sibling it already reached" in prose
    assert "an instance fix comes back as the next round's finding" in prose
    assert "a branch, parameter, state, event, window, column" in prose
    assert "Its test lands in the same push" in prose


def test_babysit_prs_skill_spends_an_approval_only_on_an_advisory() -> None:
    skill = SKILL.read_text()
    prose = " ".join(skill.split())

    assert "## An approval is not spent on an advisory" in prose
    assert "no advisory earns a push of its own" in prose
    assert "throws the approval away" in prose
    assert "A push the rest of this file requires is still owed at an approved head" in prose
    assert "a red required check, a `dirty` merge state, a rule-backed finding" in prose
    assert "The approval body's advisory list is answered the same way" in prose
    assert "every entry: one reply, then resolve" in prose
    assert "That is the second end above" in prose
    assert "an approved head is the ground that end already names" in prose
    assert "No entry waits on a later push" in prose
    assert "A thread you were told to leave open — a design gap you surfaced — stays open" in prose

    rows = {
        row.split("|")[1].strip(): row.split("|")[2].strip()
        for row in skill.splitlines()
        if row.startswith("| ")
    }
    advisory_only = rows["`APPROVED` head whose only open findings are advisory"]
    owed = rows["`APPROVED` head with a red check, a `dirty` merge state, or a rule-backed finding"]
    prose_only = rows[
        "A round raises only wording or docstrings with no written rule behind them, "
        "and the sentences are true"
    ]

    assert advisory_only == (
        "Reply and resolve; no push. A push here would trade a green gate for another full round."
    )
    assert owed == "Fix and push — the approval was never a bar to that."
    assert prose_only == (
        "Reply that the sentence stands, and resolve. "
        "Nothing is owed, so nothing is promised for a later push."
    )
    assert rows["The sentence the finding names is false at this head"] == (
        "Cut it in this round's push. Where no written rule backs it and this head is approved, "
        "reply that it is false and resolve instead — the cut is not worth the approval. Never "
        "reword it: a second wording is a second claim, and the class only closes on the cut."
    )
    assert prose.index("**Or the sentence stays** and one reply says why") < prose.index(
        "That is the second end above"
    )


def test_babysit_prs_skill_cuts_a_false_sentence_rather_than_rewording_it() -> None:
    prose = " ".join(SKILL.read_text().split())

    assert "A false sentence is not a wording problem" in prose
    assert '"advisory" is not a licence to negotiate it' in prose
    assert "The code is the documentation" in prose
    assert (
        "so it goes unless it is the one case above, an unbacked sentence at an approved head"
        in prose
    )
    assert "Count the wordings you have shipped of one sentence" in prose
    assert "cut it and let the code say it" in prose
    assert prose.index("A prose finding therefore has exactly two ends") < prose.index(
        "A false sentence is not a wording problem"
    )


def test_babysit_prs_skill_surfaces_a_new_mechanism_instead_of_pushing_it() -> None:
    prose = " ".join(SKILL.read_text().split())

    assert "## A fix that needs a new mechanism is a scope call, not a push" in prose
    assert "a field on a shared type, a stamp on a write path, a gate, a validator" in prose
    assert "Nothing has reviewed it, so it lands as a first review layered on a diff" in prose
    assert "your fix earns a fix" in prose
    assert "the blocking count flat the whole way" in prose
    assert "inside the shape the diff already has is this round's push" in prose
    assert "A mechanism is a unit of its own: **surface it**" in prose
    assert (
        "Whether it stacks as its own pull request or this one grows is the human's call" in prose
    )
    assert prose.index("## One push per round, and it fixes the class") < prose.index(
        "## A fix that needs a new mechanism is a scope call, not a push"
    )


def test_babysit_prs_skill_reads_the_round_count_and_backs_its_replies() -> None:
    skill = SKILL.read_text()
    prose = " ".join(skill.split())

    assert "A summary opens with its round number, where the round had a summary to write" in prose
    assert "a marker-only verdict carries no body and so no count" in prose
    assert "a count that climbs while the blocking count holds flat" in prose
    assert "another push is not the answer to it" in prose

    assert "A reply claiming a fix names a check you **ran at the pushed head**" in prose
    assert "the mutation you reverted to watch it fail" in prose
    assert "Never `Fixed` from the edit you believe you made" in prose
    assert "Unrun means unfixed" in prose

    rows = {
        row.split("|")[1].strip(): row.split("|")[2].strip()
        for row in skill.splitlines()
        if row.startswith("| ")
    }
    assert rows["You are about to reply `Fixed`"] == (
        "Name the check you ran at this pushed head and its result. "
        "A reply the code does not back costs the whole round."
    )
    assert rows[
        "Closing the finding needs a field, stamp, gate, validator, or call site the diff "
        "does not have"
    ] == (
        "That is a unit, not a push. Surface the scope call; pushing it layers a first review "
        "onto a diff already under review."
    )
    assert rows["The verdict's round number climbs while the blocking count holds flat"] == (
        "Your pushes are buying rounds. Find which cause it is — a mechanism per push, an "
        "instance fix, an unbacked reply — and surface it instead of pushing again."
    )


def test_babysit_prs_skill_never_merges() -> None:
    skill = SKILL.read_text()

    assert "Never merges." in skill
    assert "You may **not** merge a PR or enable auto-merge" in skill
    assert "do not merge and do not arm auto-merge yourself" in skill
