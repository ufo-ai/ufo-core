import re
from pathlib import Path

import ufo_ext_app_code.manifest as app_code
import ufo_ext_coding.manifest as coding

SKILL_DIR = Path(app_code.__file__).parent / "skills" / "app-code-home"
PROMPT_DIR = Path(app_code.__file__).parent / "prompts"
BUILD_ENTRY = (
    Path(app_code.__file__).parents[2] / "web" / "frontend" / "apps" / "code" / "index.html"
)
MODULE_SCRIPT = re.compile(r'<script type="module" src="([^"]+)">')


def test_the_agent_is_not_named_for_the_profile_its_reviewers_are() -> None:
    """A spawn target naming both a subagent profile and a workspace agent is refused as ambiguous,
    before the permission check. The reviewers this app spawns are the `coding` profile, so an agent
    called `coding` would stop every one of them at the spawn call. The app is `code`, named for the
    work it operates over like every other default app, and its slug is `code` too — the slug comes
    from the extension's name, and the two now agree."""
    (provision,) = app_code.manifest().agents
    profiles = {profile.name for profile in coding.manifest().subagents}
    assert provision.name not in profiles
    assert "coding" in profiles


def test_app_code_ships_the_one_agent_and_coding_ships_none() -> None:
    """The reviewer is not a new agent — it is the one `coding` shipped, with a page. Both
    extensions shipping a provision would give a workspace two reviewers over one set of pull
    requests, each holding half the grants."""
    manifest = app_code.manifest()
    assert manifest.name == "app_code"
    assert [provision.name for provision in manifest.agents] == ["code"]
    assert coding.manifest().agents == ()
    provision = manifest.agents[0]
    assert provision.spec.visibility == "workspace"
    assert provision.spec.purpose
    assert {path.name for path in (spec.path for spec in manifest.skills)} == {
        "app-code-home",
        "app-code-babysit",
    }


def test_the_code_agent_and_its_coding_profile_use_separate_models() -> None:
    (agent,) = app_code.manifest().agents
    profile = coding.manifest().subagents[0]
    assert (agent.spec.model, agent.spec.reasoning) == ("z-ai/glm-5.3", "high")
    assert profile.model == "claude-opus-5"
    assert profile.own_key_models["anthropic"] == "claude-opus-5"
    assert profile.needs_own_model_key


def test_the_page_draws_only_the_findings_the_procedure_produces() -> None:
    """A reviewer names one impact from a fixed list, and a claim it can only state as could or
    might is rejected before it is ever a finding. So the page has no lower tier to draw: one that
    carried a `Plausible` badge would be drawing a verdict no reviewer can return, and telling a
    member the app hedges when the procedure is what refuses to."""
    page = (SKILL_DIR / "app.tsx").read_text()
    procedure = (PROMPT_DIR / "agent_code.md").read_text()
    drawn = re.findall(r'^  \| "([^"]+)"$', page, re.MULTILINE)
    assert drawn, "the page names no impact"
    for impact in drawn:
        assert impact[0].lower() + impact[1:] in procedure
    assert "Plausible" not in page
    assert "Confirmed" not in page


def test_the_review_agent_runs_the_member_facing_tool_set() -> None:
    """It reads the pull-request page and publishes through the workspace's GitHub connection, and
    this extension declares neither. An allowlist here would leave it holding only its own
    tools."""
    (agent,) = app_code.manifest().agents
    assert agent.tools is None


CANONICAL_PROCEDURE = (
    "For each review key, spawn exactly two `coding` reviewers in the background.",
    "Issue up to eight ready spawn calls in one response",
    "Start all reviewers for the delivered batch before you process a result.",
    "Do not spawn preparation, synthesis, or adjudication subagents.",
    "Resolve disagreements yourself.",
    "Do not create or update a plan, objective, journal, or todo for a review.",
    'Each spawn payload is `{"objective": "<complete objective>"}`; never use `task`.',
    "do not load `spawn-catalog` or another skill",
    "After all named page reads, issue the ready spawn calls immediately.",
    "Earlier conversation messages can contain reviewer objectives from old prompt revisions.",
    "Build both spawn objectives only from the current `Review objective for each subagent` block",
    "issue all independent calls whose inputs are known, with a maximum of eight",
    "If two calls are ready, one call is invalid.",
    "the next response must issue four parallel calls",
    "Later, issue every ready instruction read, code read, diff read, and search as separate "
    "parallel calls.",
    "If a bounded read reports remaining offsets, read up to eight known offsets together next.",
    "If one response creates multiple subset diff files, read all of them together next.",
    "Return exactly one JSON object through `finish`, with no other text",
    "Treat a source update for a new head SHA as higher priority than every result for an older "
    "review key with the same repository URL and pull-request number.",
    "Call `cancel_spawn` for every still-running reviewer associated with each superseded review "
    "key for that pull request.",
    "Issue independent cancellation calls in the same response.",
    "Discard every result for each superseded review key for that pull request, including a "
    "result that arrives after cancellation.",
    "Do not publish a review or status for a superseded review key.",
    "Do not publish until two valid results exist for the current review key.",
)
"""Every clause of the review procedure this app carries, as the release that bounded review
concurrency wrote them."""

SUPERSEDED_PROCEDURE = (
    "3 to 7",
    "Each head SHA gets two passes",
    "every file in scope belongs to exactly one reviewer",
    "Reviews are additive",
    "Never cancel a subagent",
)
"""The roster the procedure replaced: passes of three to seven reviewers that split the changed
files between them, published additively, never cancelled. Each clause contradicts one above —
seven reviewers against two, a file split against every reviewer reading every file, an additive
publication against a superseded head publishing nothing."""


def test_the_app_frames_the_procedure_and_changes_nothing_in_it() -> None:
    """Three opening lines, then the procedure and the line that locates the app's own page."""
    lines = (PROMPT_DIR / "agent_code.md").read_text().splitlines()
    assert lines[0] == "You are the Code app for this workspace, and reviewing is what you do."
    assert lines[1] == ""
    assert lines[2] == (
        "You review GitHub pull requests. One conversation tracks the pull-request stream."
    )
    assert lines[3] == (
        "Its source trigger uses `delivery: current` and `streams: [pull_requests]`."
    )
    assert "do no more work for that page and do not publish a status" in lines[5]
    assert "read its complete contents and use each line's `page` value in file order" in lines[5]
    assert lines[-2] == ""
    assert lines[-1].startswith("Your homepage is the code screen:")
    assert lines[-1].endswith(f"load the skill `{app_code.HOME_SKILL}` and follow it.")


def test_the_review_agent_keeps_the_merge_base_its_diff_needs() -> None:
    """The complete diff reads `<base>...<head>`, which needs the common ancestor of the two
    commits. A shallow fetch grafts them, so git exits with `no merge base` and the reviewer never
    gets its file list."""
    (agent,) = app_code.manifest().agents
    prompt = agent.spec.prompt
    assert "git diff --name-only <base>...<head>" in prompt
    assert "Fetch base and head with `--filter=blob:none` and no `--depth`." in prompt


def test_each_reviewer_owns_one_workspace_checkout() -> None:
    (agent,) = app_code.manifest().agents
    prompt = agent.spec.prompt
    assert "Checkout label `correctness`." in prompt
    assert "Checkout label `security`." in prompt
    assert (
        "/workspace/code-review-<repository owner>-<repository name>-<pull-request number>-<full "
        "head SHA>-<checkout label>" in prompt
    )
    assert "Never use `/tmp` or a peer's path." in prompt


def test_a_finding_never_crosses_a_head_sha() -> None:
    """A defect is a claim about one commit, and the line it names may not exist on the next."""
    (agent,) = app_code.manifest().agents
    assert (
        "Never reuse a finding from an older head based on patch equivalence." in agent.spec.prompt
    )


def test_the_review_agent_stops_unchanged_review_keys_without_more_tools() -> None:
    (agent,) = app_code.manifest().agents
    assert "make no more tool calls for that page" in agent.spec.prompt
    assert "Only a new head SHA for the same repository URL and pull-request number" in (
        agent.spec.prompt
    )
    assert "report that no action was needed" in agent.spec.prompt


def test_the_review_agent_keeps_current_batch_pull_requests_separate() -> None:
    (agent,) = app_code.manifest().agents
    prompt = agent.spec.prompt

    assert "obtain every changed page ref in the received order" in prompt
    assert "Pass every obtained ref unchanged to `object_get`" in prompt
    assert "`delivery: current` and `streams: [pull_requests]`" in prompt
    assert "Continue until you have handled every changed page." in prompt
    assert "repository URL, pull-request number, and head SHA" in prompt
    assert "A different repository URL or pull-request number is different work" in prompt
    assert "Never combine results from different review keys." in prompt
    assert "Do not cancel or discard work for another repository URL" in prompt


def test_the_setup_applies_the_current_pull_request_trigger() -> None:
    instructions = app_code.CODE_APP_SETUP_INSTRUCTIONS

    assert app_code.CODE_APP_DELIVERY == "current"
    assert app_code.CODE_APP_STREAMS == ("pull_requests",)
    assert "`delivery: current`" in instructions
    assert "`streams: ['pull_requests']`" in instructions


def test_the_review_agent_bounds_each_child_evidence_pass() -> None:
    """One pass, as wide as the operations it already knows. The width is pinned in
    `CANONICAL_PROCEDURE`; what is pinned here is that the pass ends — a reviewer that kept
    re-reading a complete output would hold the head open and never return its result."""
    (agent,) = app_code.manifest().agents
    assert "the next response must issue four parallel calls" in agent.spec.prompt
    assert "Do not combine independent operations in one shell command." in agent.spec.prompt
    assert "repeat a complete call" in agent.spec.prompt
    assert "Return the result immediately after full coverage." in agent.spec.prompt


def test_the_review_agent_reads_agents_files_without_following_claude_symlinks() -> None:
    (agent,) = app_code.manifest().agents
    assert "Never read `CLAUDE.md`; it can be a symlink to `AGENTS.md`." in agent.spec.prompt


BABYSIT_SKILL_FILE = SKILL_DIR.parent / "app-code-babysit" / "SKILL.md"


def test_the_babysit_skill_holds_the_procedure_and_the_prompt_only_names_it() -> None:
    """The procedure is a skill, not prompt text. A prompt is written into an agent row once and
    never again, so a workspace that already holds this app would never meet a procedure added to
    the prompt later — it would draw a feature with no instructions behind it. A skill is a file
    this extension ships, so it reaches every workspace on the next deploy.

    What the prompt carries is the one line that sends the agent there."""
    skill = " ".join(BABYSIT_SKILL_FILE.read_text().split())
    for clause in (
        "Merging is yours, and never a worker's.",
        "the head SHA you verified is the head SHA you merge",
        "never bypass protection",
        "One escalation per pull request, failure and head SHA.",
        "A pull request waiting on a decision stays waiting.",
        "Never report a merge, a push, a comment, or a green check you have not read back",
    ):
        assert clause in skill, clause
    (agent,) = app_code.manifest().agents
    assert app_code.BABYSIT_SKILL in agent.spec.prompt
    assert "Merging is yours" not in agent.spec.prompt


def test_the_babysit_skill_names_no_workspace_of_its_own() -> None:
    """The repository, the authors and the people to name are the member's answers, held in a file
    the sweep writes. A shipped skill that named one workspace's would hand every other workspace
    somebody else's repository and somebody else's colleagues.

    This reads the class, not a list of the four names the procedure was ported from — a denylist
    passes the moment the next workspace's names are the ones that leaked."""
    skill = BABYSIT_SKILL_FILE.read_text()
    prose = re.sub(r"`[^`]*`", "", skill)
    assert re.findall(r"@[A-Za-z0-9][-\w]*", prose) == []
    owner_repo = re.findall(r"\b[a-z0-9][-\w]*/[a-z0-9][-\w]{2,}\b", prose)
    assert [held for held in owner_repo if not held.startswith(("repos/", "workspace/"))] == []
    assert "settings.md" in skill


def test_the_worker_is_given_the_rules_it_is_bound_by() -> None:
    """A worker reads its objective and nothing else. The skill is loaded by the parent, so a rule
    left in the skill binds nobody — and the escalation rung reads
    `/workspace/pr-babysitter/rules.md`, which something has to write."""
    skill = BABYSIT_SKILL_FILE.read_text()
    escalation = (
        Path(coding.__file__).parent / "prompts" / "subagent_fable_escalation.md"
    ).read_text()
    path = "/workspace/pr-babysitter/rules.md"
    assert path in escalation
    assert path in skill
    assert "the rules themselves in its objective" in skill
    assert "does not load this skill, and it must not" in skill
    assert "authorized to act on" in skill


def test_the_merge_gate_holds_on_nothing_this_app_cannot_clear() -> None:
    """`ufo review` publishes each finding as an inline comment, which opens a review thread. The
    repository resolves none of them — nothing here calls `resolveReviewThread`, a worker may only
    reply, and GitHub's own rule for `main` sets `required_review_thread_resolution` false — so a
    gate counting those threads never comes true. Every pull request the review ever flagged would
    sit unmerged until the 24-hour age-out drops it, with nobody named.

    The verdict is the status the gate already reads, and it is published per head SHA, so the head
    being merged answers every thread the review left on an older one."""
    skill = " ".join(BABYSIT_SKILL_FILE.read_text().split())
    merging = skill.split("## Merging", 1)[1].split("## ", 1)[0]
    assert "no unresolved thread a person left" in merging
    assert re.search(r"thread this app's own review left is not\b", merging)
    assert "`ufo review` status on the head you merge is its answer" in merging
    assert "Resolve nothing" in merging
