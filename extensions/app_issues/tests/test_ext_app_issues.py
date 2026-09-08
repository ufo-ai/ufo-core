import re
from pathlib import Path

import ufo_ext_app_issues.manifest as app_issues

SKILL_DIR = Path(app_issues.__file__).parent / "skills" / "app-issues-home"
BUILD_ENTRY = (
    Path(app_issues.__file__).parents[2] / "web" / "frontend" / "apps" / "issues" / "index.html"
)
MODULE_SCRIPT = re.compile(r'<script type="module" src="([^"]+)">')


def test_app_issues_ships_one_workspace_agent_over_one_tracker() -> None:
    """Triaging an issue and writing the code that closes it is one agent over one backlog. An app
    that split them would read the same issue twice and make a member connect the same account for
    each half."""
    manifest = app_issues.manifest()
    assert manifest.name == "app_issues"
    assert [provision.name for provision in manifest.agents] == ["issues"]
    provision = manifest.agents[0]
    assert provision.spec.visibility == "workspace"
    assert provision.spec.purpose
    assert "app-issues-home" in provision.spec.prompt
    assert {path.name for path in (spec.path for spec in manifest.skills)} == {"app-issues-home"}


def test_the_two_features_run_off_two_named_tasks() -> None:
    """One task apiece, so a member sees each on the tasks screen and pauses or deletes it there.
    One task for both would arm the unasked-for feature with the asked-for one."""
    assert app_issues.TRIAGE_TASK != app_issues.IMPLEMENT_TASK
    assert app_issues.ISSUES_APP_AGENT.setup.schedule.name == app_issues.TRIAGE_TASK
    assert f"`scheduled_task` named `{app_issues.IMPLEMENT_TASK}`" in app_issues.ISSUES_APP_PROMPT
    page = (SKILL_DIR / "app.tsx").read_text()
    assert '"Triaged"' in page
    assert '"Approved to implement"' in page
    assert app_issues.IMPLEMENT_LABEL in page


def test_triage_cannot_be_woken_by_the_comment_it_posted() -> None:
    """Triage answers on the issue itself, and a comment moves the issue it is posted on. Woken by
    a changed issue the app would be woken by its own answer, and would comment on one issue for
    ever, a paid turn at a time — so nothing about a changed issue wakes it at all.

    A clock wakes it, and what it asks on waking is a fact its own work cannot flip back: an issue
    carrying a comment from this app has been triaged. The comment is the record, so the stop
    survives a compacted transcript and a restarted conversation alike."""
    setup = app_issues.ISSUES_APP_AGENT.setup
    assert "source_trigger" not in setup.standing
    prompt = app_issues.ISSUES_APP_PROMPT
    assert "take the open issues that carry no comment from you" in prompt
    assert "is triaged, and is not triaged again" in prompt


def test_an_approval_a_member_says_in_chat_is_written_onto_the_issue() -> None:
    """The page's Approve press composes an approval in words, and the label is what the sweep
    reads — so the app puts the label on. Without this the press either did nothing or made the
    app implement against its own rule."""
    page = (SKILL_DIR / "app.tsx").read_text()
    assert "for implementation." in page
    prompt = app_issues.ISSUES_APP_PROMPT
    assert "A member approving an issue in chat is asking for that label" in prompt
    assert f"put `{app_issues.IMPLEMENT_LABEL}` on the issue they name" in prompt


def test_the_page_says_what_the_app_is_for_in_the_provisions_own_words() -> None:
    """The setup screen draws the provision's purpose as its lede, and the built page draws its own
    `PURPOSE`. A member reads both, of one app, so two sentences that drift are two answers to what
    the app is — and nothing but this holds them together."""
    source = (SKILL_DIR / "app.tsx").read_text()
    stated = re.search(r"const PURPOSE =\s*(.*?);\n", source, re.DOTALL)
    assert stated is not None
    drawn = "".join(re.findall(r'"([^"]*)"', stated[1]))
    assert drawn == app_issues.ISSUES_APP_AGENT.spec.purpose
