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


def test_the_app_ships_the_one_feature_its_adopted_row_can_run() -> None:
    """Provisioning writes a prompt when it creates a row and never again, because a prompt is a
    member's to edit — so an adopted agent keeps the instructions it already has. A second feature
    would be a band, a control under it, and no instructions behind it in every workspace that
    already holds the reviewer, which is every workspace that has one.

    So the prompt is the review procedure and nothing else, moved whole rather than rewritten: its
    publication rule is the part a rewrite would have lost."""
    prompt = app_code.CODE_APP_PROMPT
    assert "reviewing is what you do" in prompt
    assert "That commit status is the only permitted publication of the verdict." in prompt
    page = (SKILL_DIR / "app.tsx").read_text()
    # One feature, so the page draws one queue and nothing beside it.
    assert '"Review queue"' in page


def test_a_feed_wakes_the_armed_feature_so_the_setup_offers_no_cadence() -> None:
    setup = app_code.CODE_APP_AGENT.setup
    assert setup.standing == ("source_trigger",)
    assert setup.schedule is None
    assert setup.connectors == ("github",)
    assert [credential.label for credential in setup.credentials] == ["ufo GitHub App"]


def test_the_built_page_is_the_apps_own_tsx() -> None:
    entry = MODULE_SCRIPT.search(BUILD_ENTRY.read_text())
    assert entry is not None
    assert (BUILD_ENTRY.parent / entry[1]).resolve() == (SKILL_DIR / "app.tsx").resolve()
    source = (SKILL_DIR / "app.tsx").read_text()
    assert "mountApp(" in source
    # Filled in, because the page is the shape the app rebuilds against its own repository. A
    # review is per head SHA, so the rows carry one.
    for filled in ("#2077", "a41c9e2", "Materially incorrect result or state"):
        assert filled in source
    assert "<AppConversations" in source
    # Setup is a portal screen, never a band here: the acts that wire an app — a workspace install
    # an admin makes, a model turn that authors a page — are the two a framed page cannot start.
    assert "AppSetup" not in source


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


def test_the_home_skill_edits_builds_and_deploys_the_project() -> None:
    skill = (SKILL_DIR / "SKILL.md").read_text()
    assert "Copy this skill's `app.tsx` and `index.html`" in skill
    assert "`deploy_website` with that directory and `site_name` `code-home`" in skill
    assert "do not run a build yourself" in skill
    assert "set_homepage" in skill
    assert "takes the platform kit as it stands today" in skill


def test_the_review_agent_spawns_the_profile_this_pack_registers() -> None:
    """The shipped agent and the child it drives ship together. A prompt naming a profile no
    installed extension registers is a review agent that can never spawn anything — the profile is
    the `coding` extension's, which is why the app declares it as a dependency by importing from
    it."""
    (agent,) = app_code.manifest().agents
    profile = coding.manifest().subagents[0]
    assert agent.name == app_code.CODE_APP_AGENT_NAME
    assert f"`{profile.name}` reviewers" in agent.spec.prompt


def test_the_review_agent_runs_the_member_facing_tool_set() -> None:
    """It reads the pull-request page and publishes through the workspace's GitHub connection, and
    this extension declares neither. An allowlist here would leave it holding only its own
    tools."""
    (agent,) = app_code.manifest().agents
    assert agent.tools is None


CANONICAL_PROCEDURE = (
    # Two reviewers per head, both started in one response.
    "For each head SHA, spawn exactly two `coding` reviewers in the background.",
    "Issue both spawn calls in the same response.",
    "Do not process a result until both reviewers have started.",
    "Do not spawn preparation, synthesis, or adjudication subagents.",
    "Resolve disagreements yourself.",
    "Do not create or update a plan, objective, journal, or todo for a review.",
    # One bounded evidence pass, as wide as its known operations.
    "up to eight tool calls",
    "If two or more operations are ready, a response with one tool call is invalid.",
    "Do not leave a known independent operation for a later round.",
    "Return exactly one JSON object in the finish result",
    "Put no Markdown fence or text before or after it",
    # A new head preempts, and the work it replaces is cancelled and discarded together.
    "Treat a source update for a new head SHA as higher priority than every result for an older "
    "head SHA.",
    "Call `cancel_spawn` for every still-running reviewer associated with each superseded head.",
    "Issue independent cancellation calls in the same response.",
    "Discard every result for each superseded head, including a result that arrives after "
    "cancellation.",
    "Do not publish a review or status for a superseded head.",
    "Do not publish until two valid results exist for the current head SHA.",
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


def test_the_app_carries_the_review_procedure_whole() -> None:
    """The procedure is the agent, and this app moved it rather than rewriting it. A prompt is
    written into an agent row once and never again, so a clause dropped here is a clause no
    workspace provisioned after this release ever runs, and no test downstream of the row would
    catch it — the fleet would simply review differently from the one this repository states.

    Both directions are pinned. A missing clause is a rule silently lost in the move; a superseded
    one is the older roster growing back beside the rule that replaced it, which is worse, because
    the two disagree and the model would be reading both."""
    (agent,) = app_code.manifest().agents
    prompt = agent.spec.prompt
    assert [clause for clause in CANONICAL_PROCEDURE if clause not in prompt] == []
    assert [clause for clause in SUPERSEDED_PROCEDURE if clause in prompt] == []


def test_the_app_frames_the_procedure_and_changes_nothing_in_it() -> None:
    """Two framing lines, and the procedure between them: which app is speaking, and where its own
    page is edited. Everything in between is the procedure as the repository holds it, so an edit
    meant for the app's own voice cannot reach the procedure by accident."""
    lines = (PROMPT_DIR / "agent_code.md").read_text().splitlines()
    assert lines[0] == "You are the Code app for this workspace, and reviewing is what you do."
    assert lines[1] == ""
    assert lines[2] == "You review GitHub pull requests. One conversation tracks one pull request."
    assert lines[-2] == ""
    assert lines[-1].startswith("Your homepage is the code screen:")
    assert lines[-1].endswith(f"load the skill `{app_code.HOME_SKILL}` and follow it.")


def test_the_prompt_names_the_home_skill_this_app_ships() -> None:
    """The page is changed by loading a skill, and the agent is the only thing that knows to load
    it. A prompt naming a skill the manifest does not ship is an app whose page no member can ask
    it to change — and the two are written in different files, so nothing else pairs them."""
    shipped = {path.name for path in (spec.path for spec in app_code.manifest().skills)}
    assert app_code.HOME_SKILL in shipped
    (agent,) = app_code.manifest().agents
    assert f"load the skill `{app_code.HOME_SKILL}` and follow it." in agent.spec.prompt


def test_the_review_agent_is_provisioned_for_the_two_reviewers_it_spawns() -> None:
    """Both reviewers of a head run at once in the one sandbox the conversation holds, and each
    takes its own checkout of the same repository."""
    (agent,) = app_code.manifest().agents
    assert agent.spec.sandbox_size == "large"
    assert agent.spec.reasoning == "high"


def test_the_review_agent_keeps_the_merge_base_its_diff_needs() -> None:
    """The complete diff reads `<base>...<head>`, which needs the common ancestor of the two
    commits. A shallow fetch grafts them, so git exits with `no merge base` and the reviewer never
    gets its file list."""
    (agent,) = app_code.manifest().agents
    prompt = agent.spec.prompt
    assert "git diff --name-only <base>...<head>" in prompt
    assert "fetch the two commits with `--filter=blob:none`" in prompt
    assert "Never fetch shallow" in prompt


def test_each_reviewer_owns_one_workspace_checkout() -> None:
    (agent,) = app_code.manifest().agents
    prompt = agent.spec.prompt
    assert "Checkout label `correctness`." in prompt
    assert "Checkout label `security`." in prompt
    assert "/workspace/code-review-<full head SHA>-<checkout label>" in prompt
    assert "Never put either under `/tmp` or another reviewer's checkout." in prompt


def test_a_finding_never_crosses_a_head_sha() -> None:
    """A defect is a claim about one commit, and the line it names may not exist on the next."""
    (agent,) = app_code.manifest().agents
    assert (
        "Never reuse a finding from an older head based on patch equivalence." in agent.spec.prompt
    )


def test_the_review_agent_stops_unchanged_head_revisions_without_more_tools() -> None:
    (agent,) = app_code.manifest().agents
    assert "A new head SHA is the only source change that starts review work." in agent.spec.prompt
    assert "stop in the next response without another tool call" in agent.spec.prompt
    assert "report that no action was needed" in agent.spec.prompt


def test_the_review_agent_bounds_each_child_evidence_pass() -> None:
    """One pass, as wide as the operations it already knows. The width is pinned in
    `CANONICAL_PROCEDURE`; what is pinned here is that the pass ends — a reviewer that kept
    re-reading a complete output would hold the head open and never return its result."""
    (agent,) = app_code.manifest().agents
    assert "Make one bounded evidence pass." in agent.spec.prompt
    assert "Maximize same-round tool use." in agent.spec.prompt
    assert "Do not repeat a command when its output was complete." in agent.spec.prompt
    assert "return the JSON result immediately" in agent.spec.prompt


def test_the_review_agent_requires_one_strict_json_result() -> None:
    (agent,) = app_code.manifest().agents
    prompt = agent.spec.prompt
    assert "Return exactly one JSON object in the finish result" in prompt
    assert "contains no text outside it" in prompt


def test_the_review_agent_reaches_github_through_the_slot_this_pack_declares() -> None:
    """Public internet stays blocked, so a child's `git fetch` reaches github.com only through the
    git slot's own injection rule, which the `coding` extension declares beside the profile that
    uses it. Opening the public wire instead would give the agent every host."""
    (agent,) = app_code.manifest().agents
    assert agent.spec.internet_access_allowed is False
    declared = coding.manifest().credentials
    hosts = {slot.injection.host for slot in declared if slot.injection is not None}
    assert coding.GIT_HOST in hosts


def test_the_review_agent_publishes_the_status_the_repository_requires() -> None:
    """`ufo review` is the required check on the pull request, and the head SHA is what it attaches
    to. A prompt that names either differently publishes a status nothing is waiting for. Findings
    ride a pull-request review, so each one lands on the changed line it describes rather than in a
    status description that holds one sentence."""
    (agent,) = app_code.manifest().agents
    assert "`ufo review`" in agent.spec.prompt
    assert "Never create a GitHub Check Run for it" in agent.spec.prompt
    assert "never `ufo review`" in agent.spec.prompt
    assert "/repos/{owner}/{repo}/statuses/{head_sha}" in agent.spec.prompt
    assert "/repos/{owner}/{repo}/pulls/{pull_number}/reviews" in agent.spec.prompt


def test_the_review_agent_declares_the_account_it_cannot_arrive_with() -> None:
    """A shipped agent arrives with no grant, so the provision names the one kind of authority a
    member must give it: a GitHub account, which its publication calls through. The instructions
    also name the two acts no declaration can carry — sharing the pull-request source, which is
    what lets a trigger wake it, and installing the App its children fetch commits with."""
    (agent,) = app_code.manifest().agents
    assert agent.setup.connectors == (coding.GITHUB_CONNECTOR,)
    assert "share it" in agent.setup.instructions
    assert "source trigger" in agent.setup.instructions
    assert "connect_github" in agent.setup.instructions


def test_the_review_agent_holds_the_verbs_its_own_setup_names() -> None:
    """The setup asks the agent to connect an account and apply objects in its own conversation. It
    runs the member-facing set, so it holds those verbs — an allowlist here would leave it reading
    instructions it could not follow, which `AgentProvision` refuses outright."""
    (agent,) = app_code.manifest().agents
    assert agent.tools is None


def test_the_page_says_what_the_app_is_for_in_the_provisions_own_words() -> None:
    """The setup screen draws the provision's purpose as its lede, and the built page draws its own
    `PURPOSE`. A member reads both, of one app, so two sentences that drift are two answers to what
    the app is — and nothing but this holds them together."""
    source = (SKILL_DIR / "app.tsx").read_text()
    stated = re.search(r"const PURPOSE =\s*(.*?);\n", source, re.DOTALL)
    assert stated is not None
    drawn = "".join(re.findall(r'"([^"]*)"', stated[1]))
    assert drawn == app_code.CODE_APP_AGENT.spec.purpose


BABYSIT_SKILL_FILE = SKILL_DIR.parent / "app-code-babysit" / "SKILL.md"


def test_the_babysit_skill_holds_the_procedure_and_the_prompt_only_names_it() -> None:
    """The procedure is a skill, not prompt text. A prompt is written into an agent row once and
    never again, so a workspace that already holds this app would never meet a procedure added to
    the prompt later — it would draw a feature with no instructions behind it. A skill is a file
    this extension ships, so it reaches every workspace on the next deploy.

    What the prompt carries is the one line that sends the agent there."""
    # Matched on one line: a clause that wraps in the file is the same rule, and a test that broke
    # on the wrap would send the next reader to re-flow prose rather than to fix a rule.
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


def test_babysitting_is_armed_by_a_clock_and_says_why() -> None:
    """A finished check moves no pull-request record. The `pull_requests` stream syncs
    `/repos/{owner}/{repo}/pulls`, so the source wakes on the record alone and a red check — the
    first thing babysitting exists to clear — arrives at nothing. The review's own trigger cannot
    carry this feature, and the prompt says so where a reader would otherwise try it."""
    (agent,) = app_code.manifest().agents
    assert f"`scheduled_task` named `{app_code.BABYSIT_TASK}`" in agent.spec.prompt
    assert "a check that finishes moves no pull-request record" in agent.spec.prompt
    # The review's standing order is unchanged: a feature nobody armed declares nothing.
    assert app_code.CODE_APP_AGENT.setup.standing == ("source_trigger",)
    assert app_code.CODE_APP_AGENT.setup.schedule is None


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
    # A worker that is not told the pull request is its to act on does the local work and pushes
    # nothing, which makes the whole fix step inert.
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
