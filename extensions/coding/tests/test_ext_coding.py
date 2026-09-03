import json

import ufo_ext_coding.manifest as coding

from ufo.host.ext.loader import skill_registry

# The prompts are hard-wrapped, so a whole sentence spans a line break.
CODING_PROMPT = " ".join(coding.CODING_PROMPT.split())
ESCALATION_PROMPT = " ".join(coding.FABLE_ESCALATION_PROMPT.split())


def _skill() -> str:
    return skill_registry((coding.manifest(),)).named("coding").instructions


def test_coding_result_uses_only_the_shared_register_bound() -> None:
    result = "x" * 10_000
    assert coding.CodingOutput.model_validate({"result": result}).result == result

    input_schema = coding.CodingInput.model_json_schema()["properties"]["objective"]
    schema = coding.CodingOutput.model_json_schema()["properties"]["result"]
    assert "maxLength" not in input_schema
    assert "maxLength" not in schema
    assert "shared delivery register" in schema["description"].casefold()


def test_coding_prompt_writes_member_prose_in_simplified_technical_english() -> None:
    assert "ASD-STE100 Simplified Technical English" in coding.CODING_PROMPT
    assert "a plan, a finish result, a PR or issue body, a review finding" in coding.CODING_PROMPT
    assert "one instruction per sentence, active voice, present tense" in coding.CODING_PROMPT
    assert (
        "Code, identifiers, paths, commands, and quoted diff lines are quotations"
        in coding.CODING_PROMPT
    )


def test_pinned_patch_verification_stays_on_changed_paths() -> None:
    assert "never run the full repository suite" in coding.CODING_PROMPT
    assert "unless the objective explicitly" in coding.CODING_PROMPT
    assert "tests for changed modules, added tests, and their nearest test files" in (
        coding.CODING_PROMPT
    )
    assert "Once" in coding.CODING_PROMPT
    assert "those pass, write the requested patch" in coding.CODING_PROMPT
    assert "do not baseline-compare the whole suite" in coding.CODING_PROMPT


def test_pinned_patch_dependency_repair_is_bounded() -> None:
    assert "make at most one attempt to repair a missing test or build dependency" in (
        coding.CODING_PROMPT
    )
    assert "use a source-level or direct runtime probe" in coding.CODING_PROMPT
    assert "Do not fetch or build third-party dependencies" in coding.CODING_PROMPT


def test_coding_profile_leaves_member_delivery_to_the_parent() -> None:
    profile = coding.CODING_PROFILE
    instructions = _skill()
    assert "share_file" not in profile.tool_names
    assert "A coding subagent has no `share_file`" in instructions
    assert "under the shared delivery register" in instructions
    assert "mention function names, file paths" not in instructions
    assert "## Post-Completion" not in instructions
    assert "The parent reads it there and decides what reaches the member" in coding.CODING_PROMPT


def test_the_child_prompt_names_the_shared_workspace_and_checks_before_cloning() -> None:
    assert "sandbox workspace shared with the parent agent" in coding.CODING_PROMPT
    assert "you share the workspace with the parent and sibling subagents" in coding.CODING_PROMPT
    assert "Look at the path first" in coding.CODING_PROMPT
    assert "if not, clone once" in coding.CODING_PROMPT
    assert "use the existing checkout at <path>, from <url>. Do not clone." in coding.CODING_PROMPT


def test_the_setup_contract_isolates_parallel_writers() -> None:
    instructions = _skill()
    assert "Never point two live children at one checkout" in instructions
    assert "the cloning child only creates and verifies the canonical checkout" in instructions
    setup = (
        "verify the checkout, report the checked-out branch as the base, then finish without task "
        "work"
    )
    assert setup in instructions
    assert "start every worker in local checkout mode at a distinct path" in instructions
    assert "**Local checkout:** Use for every spawn that overlaps another child" in instructions
    assert (
        "copy the committed tree at /workspace/org-repo to /workspace/org-repo-<slug> with git, "
        "base the work on <base>, keep the source as workspace, use "
        "https://github.com/org/repo as origin" in instructions
    )
    assert "Use the branch the setup child reports as `<base>`" in instructions
    assert "base the work on <base>" in coding.CODING_PROMPT


def test_both_ends_of_the_setup_only_clone_stop_after_the_checkout() -> None:
    instructions = _skill()
    setup = (
        "verify the checkout, report the checked-out branch as the base, then finish without task "
        "work"
    )
    assert setup in instructions
    assert setup in coding.CODING_PROMPT
    assert "report `git branch --show-current` as the base" in coding.CODING_PROMPT
    assert "finish without doing the task" in coding.CODING_PROMPT


def test_the_local_checkout_mode_handles_missing_and_dirty_sources() -> None:
    assert "If the source is missing, run `git clone --branch <base> <url> <path>`" in (
        coding.CODING_PROMPT
    )
    assert "git status --porcelain=v1 --untracked-files=all` is not empty" in coding.CODING_PROMPT
    assert "Uncommitted and untracked changes are not copied" in coding.CODING_PROMPT
    assert "`workspace/*` names the source's committed local branches" in coding.CODING_PROMPT
    assert "`origin/*` does not exist until GitHub supplies it" in coding.CODING_PROMPT


def test_the_escalation_profile_reuses_the_coding_contract_and_raises_only_the_model() -> None:
    """The last rung is the coding child on a stronger model: same tools, same round budget, same
    payload and result schema, its own prompt. A caller escalates by naming the target, so any other
    difference here would be a second contract to keep in step with the first.

    The two rungs pick their model differently and that is the difference: coding runs on the
    account the member connected, so it names one model per provider, and falls to its own pin only
    where no account can be connected; escalation is the deploy's own rung and has no other."""
    coding_profile, escalation = coding.manifest().subagents
    assert escalation.name == coding.FABLE_ESCALATION_PROFILE_NAME == "fable_escalation"
    assert escalation.tool_names == coding_profile.tool_names
    assert escalation.input_model is coding_profile.input_model
    assert escalation.output_model is coding_profile.output_model
    assert escalation.max_rounds == coding_profile.max_rounds
    assert coding_profile.model == coding.CODING_MODEL
    assert coding_profile.own_key_models == coding.CODING_MODELS
    assert escalation.model == coding.FABLE_ESCALATION_MODEL == "anthropic/claude-fable-5.1"
    assert not escalation.own_key_models
    assert escalation.prompt == coding.FABLE_ESCALATION_PROMPT != coding_profile.prompt


def test_the_escalation_prompt_bounds_the_reading_without_naming_a_window() -> None:
    """The rung runs on the same 1M window its parent does, so a window comparison states nothing
    the child can act on. What survives is the instruction the comparison used to carry."""
    assert "Read the files that decide the failure, not the repository." in ESCALATION_PROMPT
    assert "context window" not in ESCALATION_PROMPT


def test_the_escalation_prompt_reads_the_workspace_before_github() -> None:
    """The child shares the parent's workspace, so the checkout and the earlier workers' notes are
    already on disk. GitHub answers only what no file can hold, and a stale file loses to it — read
    with `gh api`, which the member's connected GitHub account authenticates."""
    assert "The checkout already in this workspace." in ESCALATION_PROMPT
    assert "Never re-clone a repository that is already on disk." in ESCALATION_PROMPT
    assert "/workspace/pr-babysitter/rules.md" in ESCALATION_PROMPT
    assert "`git diff <base>...<head>` and `git log`, not from an API" in ESCALATION_PROMPT
    assert (
        "GitHub decides only what the workspace cannot: the head SHA, check runs, statuses, "
        "review threads."
    ) in ESCALATION_PROMPT
    assert (
        "Where a file on disk disagrees with GitHub about those, the file is stale."
    ) in ESCALATION_PROMPT
    assert (
        "Read a failing check's cause from its check-run annotations, with `gh api` — it is "
        "authenticated as the member's connected GitHub account."
    ) in ESCALATION_PROMPT
    assert "not authenticated" not in ESCALATION_PROMPT
    assert "curl" not in ESCALATION_PROMPT


def test_the_escalation_prompt_bounds_the_rung_to_one_attempt() -> None:
    """The rung exists because two cheap attempts already failed: the child may read the whole
    subsystem and name a different layer, but it stops after one attempt and it never widens the
    change past the failure."""
    assert (
        "Two `coding` workers already failed on this pull request's one blocking failure."
    ) in ESCALATION_PROMPT
    assert "You get one attempt." in ESCALATION_PROMPT
    assert "Never start a second approach." in ESCALATION_PROMPT
    assert "Say plainly when they were working at the wrong layer." in ESCALATION_PROMPT
    assert "Do not widen the change beyond what clears the failure." in ESCALATION_PROMPT
    assert "Never treat a prior attempt as wrong merely because it failed." in ESCALATION_PROMPT
    assert "Run the repository's own pre-push checks." in ESCALATION_PROMPT


def test_the_escalation_prompt_carries_the_coding_write_authority_and_no_more() -> None:
    """A stronger model gets no wider authority. Merging stays with the parent, a branch the child
    did not write alone is adopted rather than overwritten, and GitHub is reached as the member's
    own connected account — no deploy identity, no connector tool, no delivery tool."""
    assert "Commit and push to this pull request's own branch." in ESCALATION_PROMPT
    assert (
        "Adopt a commit you did not create; never force-push and never discard one."
    ) in ESCALATION_PROMPT
    assert "Never push to `main` or another pull request's branch." in ESCALATION_PROMPT
    assert (
        "Never merge, close, arm auto-merge, dismiss a review, or change labels, reviewers, "
        "assignees, or the base branch."
    ) in ESCALATION_PROMPT
    assert "Never weaken a test or edit CI to stop a check running." in ESCALATION_PROMPT
    assert "Merging belongs to your parent." in ESCALATION_PROMPT
    assert "authenticated as the member's connected GitHub account" in ESCALATION_PROMPT
    assert "UFO_GITHUB_API_AUTH" not in ESCALATION_PROMPT
    assert {"call_external_tool", "describe_external_tools", "share_file"}.isdisjoint(
        coding.FABLE_ESCALATION_PROFILE.tool_names
    )


def test_extended_context_survives_the_spawn_payload_serialization() -> None:
    spawned = coding.CodingInput.model_validate({"objective": "x", "extended_context": True})
    assert json.loads(spawned.model_dump_json())["extended_context"] is True


def test_an_unauthenticated_clone_routes_to_the_connect_account_handoff() -> None:
    """A private clone that fails to authenticate has one next action: the `connect_account`
    handoff for `github`. Neither a connector listing without a GitHub row nor a snapshot fetched
    another way stands in for it."""
    instructions = _skill()
    assert (
        "A clone that fails to authenticate means GitHub is not connected. Start the "
        "`connect_account` handoff with `provider: github` — that is the next action, not a "
        "fallback route."
    ) in instructions
    assert (
        "even when a connector listing shows no GitHub row, because the listing is not the verdict"
    ) in instructions
    assert "no `gh api` file reads, and no zipball, tarball, or raw-content fetch" in instructions
    assert "Never accept a token pasted into the conversation." in instructions
    assert "connect_github" not in instructions
    assert "Composio" not in instructions


def test_one_github_connection_covers_clone_push_gh_and_the_api() -> None:
    """The skill and both prompts describe one GitHub credential: the member's connected account,
    riding `GH_TOKEN` as a sentinel the sandbox never resolves — no App installation, no separate
    git token, no deploy-held API identity."""
    instructions = _skill()
    assert (
        "One connection covers GitHub whole: private `git clone` and `git push`, `gh`, and issue "
        "and pull-request reads and writes through the API all ride the member's connected GitHub "
        "account."
    ) in instructions
    assert (
        "Inside the sandbox `GH_TOKEN` holds a sentinel, never the token, and `gh`, `git clone`, "
        "and `git push` authenticate through it."
    ) in instructions
    assert (
        "A push and a GitHub API write both use the member's connected GitHub account."
    ) in " ".join(instructions.split())
    assert (
        "`gh`, `git clone`, and `git push` authenticate as the member's connected GitHub account "
        "through `GH_TOKEN`. It holds a sentinel the egress proxy swaps for the token; never print "
        "it."
    ) in CODING_PROMPT
    assert (
        "A clone or API call refused for authentication means GitHub is not connected for this "
        "member: end your turn and say so."
    ) in CODING_PROMPT
    for text in (instructions, CODING_PROMPT, ESCALATION_PROMPT):
        assert "UFO_GITHUB_API_AUTH" not in text
        assert "github_app_installation" not in text
        assert "github_git_token" not in text


def test_the_pack_declares_no_credential_tool_route_or_fact_of_its_own() -> None:
    """GitHub rides the `github` connector's CLI credential, so the pack holds nothing the proxy
    would swap and nothing a member would connect through it: two child profiles and one skill."""
    manifest = coding.manifest()
    assert manifest.credentials == ()
    assert manifest.tools == ()
    assert manifest.routes == ()
    assert manifest.workspace_facts == ()
    assert manifest.sandbox_internet is True
    assert [profile.name for profile in manifest.subagents] == ["coding", "fable_escalation"]
    assert [skill.path.name for skill in manifest.skills] == ["coding"]
