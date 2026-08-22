import json
import re
import shlex
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from ufo_ext_repl.manifest import JS_REPL_TOOL, XLSX_REPL_TOOL
from ufo_ext_research.tools import FETCH_URL_TOOL, SEARCH_VERTICAL_TOOL, SEARCH_WEB_TOOL
from ufo_ext_sites import manifest as sites_manifest
from ufo_ext_sites import share_card
from ufo_ext_sites.delegation import BuildWebsiteInput, _build_website
from ufo_ext_sites.objects import (
    CONVERSATION_DIGEST_HEX,
    site_name_from_object,
    site_object_name,
)
from ufo_ext_sites.share_card import (
    BROWSER_COMMANDS,
    CARD_HEIGHT,
    CARD_SHOT_DRAWN,
    CARD_TIMEOUT_SECONDS,
    CARD_WIDTH,
    FONT_ASSET,
    FRAME_SECONDS,
    LOAD_WALL_SECONDS,
    LOGO_ASSET,
    PANEL_WIDTH,
    SETTLE_WALL_SECONDS,
    SHOT_DEADLINE_SECONDS,
    SHOT_TOKEN,
    SHOT_WIDTH,
    STORED_SHOT_DRAWN,
    UNATTENDED_FLAGS,
    card_page,
    shot_command,
)
from ufo_ext_sites.store import SITE_NAME_MAX, InvalidSiteName, site_name
from ufo_ext_sites.subagent import WEBSITE_BUILDING_PROFILE, WebsiteBuildingResult
from ufo_ext_sites.tools import (
    LOG_CLEAR_PROG,
    LOG_TAIL_TIMEOUT_SECONDS,
    PREVIEW_HEIGHT,
    PREVIEW_WIDTH,
    READINESS_TIMEOUT_SECONDS,
    SITES_TOOL_NAMES,
    StartServerInput,
    WebsiteInput,
    start_server,
    website,
)

from ufo.blob import FilesystemBlobStore
from ufo.ext.loader import skill_registry
from ufo.loop.subagents import subagent_system_prompt
from ufo.object_name import OBJECT_NAME_MAX_LENGTH
from ufo.sandbox.session import (
    SANDBOX_MODULE_BOOTSTRAP,
    SANDBOX_PYTHON_FLAG,
    ExecResult,
)
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.skills.runtime import mount_skill
from ufo.tools.builtins import BUILTIN_TOOLS
from ufo.tools.context import SpawnResult, ToolContext

TOOL_NARRATION = "building the site"
HOUSE_STYLE = "ufo-style"
HOUSE_STYLE_TOKENS = "references/tokens.css"
PLAYWRIGHT_GUIDANCE = "shared/12-playwright-interactive.md"
JS_CELL = re.compile(r"```javascript\n(.*?)```", re.S)


def _playwright_guidance() -> str:
    registry = skill_registry((sites_manifest.manifest(),))
    return dict(registry.named("website-building").files)[PLAYWRIGHT_GUIDANCE].decode()


@dataclass
class FakeSandbox:
    """Scripts the sandbox for the sites handlers: records every bash command and returns a scripted
    result by matching a substring, defaulting to success — so the serve/build flows are exercised
    without a real container."""

    scripted: dict[str, ExecResult] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    budgets: dict[str, int | None] = field(default_factory=dict)
    programs: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    claim: ExecResult = field(default_factory=lambda: ExecResult(stdout="", stderr="", exit_code=0))

    async def bash(self, command: str, timeout_s: int | None = None) -> ExecResult:
        self.commands.append(command)
        self.budgets[command] = timeout_s
        for needle, result in self.scripted.items():
            if needle in command:
                return result
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def python(self, program: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        self.programs.append((program, args))
        return self.claim


async def _unavailable_spawn(profile: str, payload: dict, background: bool = False) -> SpawnResult:
    raise AssertionError("sites tools must not spawn")


@dataclass
class _StubMemory:
    async def recall(self, query: str, subjects: frozenset[str], limit: int) -> tuple:
        return ()

    async def commit(self, write: object) -> None:
        return None


def _context(sandbox: FakeSandbox, tmp_path: Path) -> ToolContext:
    turn = Turn(
        id=uuid4(),
        workspace_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=0,
        status="running",
        inbound="hi",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
    )
    return ToolContext(
        sandbox=sandbox,
        blob=FilesystemBlobStore(root=tmp_path),
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
    )


def test_manifest_declares_the_tools_the_profile_and_the_section() -> None:
    manifest = sites_manifest.manifest()
    assert {tool.name for tool in manifest.tools} == {
        "website",
        "start_server",
        "deploy_website",
        "publish_website",
        "set_homepage",
        "build_website",
    }
    deploy = next(tool for tool in manifest.tools if tool.name == "deploy_website")
    assert deploy.description.startswith("Serve a website folder and host it")
    assert "visibility" in deploy.input_model.model_json_schema()["properties"]
    (kind,) = manifest.objects
    assert kind.name == "site"
    (surface,) = manifest.surfaces
    assert surface.name == "sites"
    build = next(tool for tool in manifest.tools if tool.name == "build_website")
    assert build.side_effecting is True
    assert {"objective", "preload_skills", "extended_context"} <= set(
        build.input_model.model_json_schema()["properties"]
    )
    (profile,) = manifest.subagents
    assert profile.name == "website_building"
    assert profile.input_model.model_validate({"objective": "x" * 10_000}).objective == "x" * 10_000
    assert "maxLength" not in profile.input_model.model_json_schema()["properties"]["objective"]
    assert "maxLength" not in profile.output_model.model_json_schema()["properties"]["result"]
    assert "deploy_website" in profile.tool_names
    assert "publish_website" not in profile.tool_names
    assert "this conversation's sandbox" in build.description
    (section,) = manifest.prompt_sections
    assert section.name == "sites" and "<sites>" in section.body


def test_website_profile_receives_the_complete_per_turn_skill_index() -> None:
    profile = sites_manifest.manifest().subagents[0]
    prompt = subagent_system_prompt(
        profile, skills=(("extension-skill", "A turn-specific website workflow."),)
    )
    assert "{{skill_index}}" not in prompt
    assert "<available_skills>" in prompt
    assert "- extension-skill: A turn-specific website workflow." in prompt


async def test_build_website_forwards_the_optional_knobs_into_the_spawn(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def _capture(
        profile: str,
        payload: dict,
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        captured["profile"] = profile
        captured["payload"] = payload
        captured["dedup_key"] = dedup_key
        return SpawnResult(
            turn_id=uuid4(),
            conversation_id=uuid4(),
            output=WebsiteBuildingResult(result="built"),
        )

    ctx = replace(
        _context(FakeSandbox(), tmp_path),
        spawn=_capture,
        idempotency_key="turn-1/build_website/call-2",
    )
    result = await _build_website(
        ctx,
        BuildWebsiteInput(
            user_description=TOOL_NARRATION,
            objective="build a landing page",
            preload_skills=("website-building",),
            extended_context=True,
        ),
    )
    assert captured["profile"] == "profile:website_building"
    assert captured["payload"] == {
        "objective": "build a landing page",
        "preload_skills": ("website-building",),
        "extended_context": True,
    }
    assert captured["dedup_key"] == "turn-1/build_website/call-2"
    assert json.loads(result.content[0].text)["result"] == "built"


async def test_build_website_omits_the_unset_knobs(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def _capture(
        profile: str,
        payload: dict,
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        captured["payload"] = payload
        return SpawnResult(
            turn_id=uuid4(),
            conversation_id=uuid4(),
            output=WebsiteBuildingResult(result="built"),
        )

    ctx = replace(_context(FakeSandbox(), tmp_path), spawn=_capture)
    await _build_website(
        ctx, BuildWebsiteInput(user_description=TOOL_NARRATION, objective="minimal")
    )
    assert captured["payload"] == {"objective": "minimal"}


def test_website_building_indexes_the_parent_and_hides_the_nested_child() -> None:
    registry = skill_registry((sites_manifest.manifest(),))
    index = dict(registry.index())
    assert "website-building" in index
    assert "website-building/webapp" not in index


def test_website_building_parent_keeps_its_own_subdirs_but_not_the_child_subtree() -> None:
    registry = skill_registry((sites_manifest.manifest(),))
    parent_files = {path for path, _ in registry.named("website-building").files}
    assert any(path.startswith("game/") for path in parent_files)
    assert any(path.startswith("shared/") for path in parent_files)
    assert any(path.startswith("informational/") for path in parent_files)
    assert not any(path.startswith("webapp/") for path in parent_files)


async def test_the_webapp_child_declares_its_parent_and_mounts_it_nested() -> None:
    registry = skill_registry((sites_manifest.manifest(),))

    child = registry.named("website-building/webapp")
    assert child.name == "website-building/webapp"
    assert child.parent == "website-building"

    assert [ref.card.name for ref in registry.closure("website-building/webapp")] == [
        "website-building/webapp",
        "website-building",
        HOUSE_STYLE,
    ]

    written: dict[str, bytes] = {}

    class _Sandbox:
        async def write_file(self, path: str, content: bytes) -> None:
            written[path] = content

    for entry in await registry.materialize(registry.closure("website-building/webapp")):
        await mount_skill(_Sandbox(), entry.skill)

    assert "/workspace/.skills/website-building/SKILL.md" in written
    assert "/workspace/.skills/website-building/webapp/SKILL.md" in written
    assert "/workspace/.skills/website-building/shared/01-design-tokens.md" in written
    assert not any(path.startswith("/workspace/.skills/website-building-") for path in written)


async def test_website_building_pulls_the_house_style_and_scopes_it_to_our_own_pages() -> None:
    """A page of ours is built in the house tokens with no second load, so the tokens mount with the
    skill and the art-direction ladder states the exception before its first rung. A site with a
    subject of its own, or a member who named a style, still overrides it — without that the ladder
    below would be dead wording and every site would come out looking like the portal."""
    registry = skill_registry((sites_manifest.manifest(),))
    assert [ref.card.name for ref in registry.closure("website-building")] == [
        "website-building",
        HOUSE_STYLE,
    ]

    written: dict[str, bytes] = {}

    class _Sandbox:
        async def write_file(self, path: str, content: bytes) -> None:
            written[path] = content

    for entry in await registry.materialize(registry.closure("website-building")):
        await mount_skill(_Sandbox(), entry.skill)
    assert f"/workspace/.skills/{HOUSE_STYLE}/{HOUSE_STYLE_TOKENS}" in written

    instructions = registry.named("website-building").instructions
    assert HOUSE_STYLE in instructions
    assert "Member-supplied design direction wins" in instructions


def test_ufo_application_homepages_bind_only_after_browser_proof_and_deploy() -> None:
    instructions = (
        skill_registry((sites_manifest.manifest(),)).named("website-building").instructions
    )
    application = instructions.partition("## ufo application homepage")[2]

    assert application
    assert "shared/12-playwright-interactive.md" in application
    assert "set_homepage" in application
    assert application.index("browser QA") < application.index("deploy_website")
    assert application.index("deploy_website") < application.index("set_homepage")
    assert "visible state" in application


def test_the_website_building_profile_names_only_meaningful_tools() -> None:
    """It builds, serves, validates and hosts — and delivers nothing itself. `share_file` exists to
    hand the member a copy, and the child has no member to hand one to: the workspace it shares with
    the parent is the handoff, read back with `glob` and `read`."""
    names = set(WEBSITE_BUILDING_PROFILE.tool_names)
    # The queue projects the profile by filtering the live tool set on these names, so a name that
    # resolves to nothing is dropped in silence — a typo would leave the child short of a tool and
    # every test green. Whatever this profile names has to exist somewhere that ships.
    available = (
        set(SITES_TOOL_NAMES)
        | {tool.name for tool in BUILTIN_TOOLS}
        | {JS_REPL_TOOL, XLSX_REPL_TOOL}
        | {SEARCH_WEB_TOOL, SEARCH_VERTICAL_TOOL, FETCH_URL_TOOL}
    )
    assert names <= available
    # Named, not merely resolvable: the containment above passes just as well with a tool dropped,
    # and the two REPLs are what the child drives a page and a workbook with.
    assert {JS_REPL_TOOL, XLSX_REPL_TOOL} <= names
    assert {"website", "start_server", "write", "js_repl", "deploy_website"} <= names
    assert "share_file" not in names
    assert WEBSITE_BUILDING_PROFILE.input_model.model_validate(
        {"user_description": TOOL_NARRATION, "objective": "build a landing page"}
    ).objective
    assert WEBSITE_BUILDING_PROFILE.max_rounds == 100


def test_the_website_building_profile_uses_the_shared_finish_contract() -> None:
    prompt = subagent_system_prompt(WEBSITE_BUILDING_PROFILE)
    assert "A delivery crosses an agent boundary" in prompt
    assert "call `finish` directly" not in WEBSITE_BUILDING_PROFILE.prompt
    assert "End the turn by calling the `finish` tool" in prompt


def test_the_playwright_guidance_keeps_the_browser_outside_the_cell() -> None:
    """`js_repl` runs one `node` process per call and returns only when that process exits, so a
    browser held open for the next cell keeps the process on the event loop until the budget expires
    and the run is killed. The browser has to outlive the cell as a process of its own: started once
    through `bash` behind a debug port, connected to per cell."""
    guidance = _playwright_guidance()
    assert "--remote-debugging-port" in guidance
    assert "background: true" in guidance
    assert "connectOverCDP" in guidance
    assert "chromium.launch(" not in guidance
    lowered = guidance.lower()
    assert "keep the handles alive" not in lowered
    assert "handles alive across" not in lowered
    assert "bootstrap" not in lowered, "the bootstrap cell is the timeout this guidance replaced"


def test_every_playwright_example_cell_can_exit() -> None:
    """Each example is a cell the model runs verbatim, so a connect with no matching close is a
    documented deadline loss. The close sits in `finally`, so a check that throws still exits."""
    cells = [cell for cell in JS_CELL.findall(_playwright_guidance()) if "connectOverCDP" in cell]
    assert len(cells) >= 3
    for cell in cells:
        assert "browser.close()" in cell
        assert "} finally {" in cell


def test_the_playwright_signoff_gates_a_visual_claim_on_a_screenshot() -> None:
    """A turn whose every browser call failed still reported "Verified at desktop and phone widths".
    Signoff names the evidence a visual claim rests on, and names what the reply must say when that
    evidence does not exist."""
    signoff = _playwright_guidance().split("### Signoff", 1)[1].split("\n## ", 1)[0]
    assert "At least one screenshot came back successfully" in signoff
    assert "exit_code: 0" in signoff
    assert "make no visual claim" in signoff
    assert "visual verification was skipped" in signoff


def test_the_website_building_prompt_gates_a_visual_claim_on_a_screenshot() -> None:
    """The child's result is what the parent repeats to the member, so the same gate has to hold in
    the profile prompt: the skill file only binds a child that read it."""
    prompt = WEBSITE_BUILDING_PROFILE.prompt
    assert "connects to it over CDP" in prompt
    assert "exit_code: 0" in prompt
    assert "visual verification was skipped" in prompt


async def test_website_builds_and_lists_the_output(tmp_path: Path) -> None:
    sandbox = FakeSandbox(
        scripted={"ls -1A": ExecResult(stdout="index.html\nstyle.css\n", stderr="", exit_code=0)}
    )
    ctx = _context(sandbox, tmp_path)
    result = await website(
        ctx,
        WebsiteInput(
            user_description=TOOL_NARRATION,
            run_command="npm run build",
            project_path="/workspace/site",
        ),
    )
    payload = json.loads(result.content[0].text)
    assert payload["project_path"] == "/workspace/site"
    assert payload["files"] == ["index.html", "style.css"]
    assert any("npm run build" in command for command in sandbox.commands)


async def test_website_build_failure_fails_loud(tmp_path: Path) -> None:
    sandbox = FakeSandbox(
        scripted={"npm run build": ExecResult(stdout="", stderr="build broke", exit_code=1)}
    )
    ctx = _context(sandbox, tmp_path)
    with pytest.raises(RuntimeError, match="build broke"):
        await website(
            ctx, WebsiteInput(user_description=TOOL_NARRATION, run_command="npm run build")
        )


async def test_start_server_reports_a_serve_failure_from_the_log(tmp_path: Path) -> None:
    sandbox = FakeSandbox(
        scripted={
            "nohup": ExecResult(stdout="", stderr="", exit_code=1),
            "tail -n 20": ExecResult(stdout="Traceback: port in use", stderr="", exit_code=0),
        }
    )
    ctx = _context(sandbox, tmp_path)
    with pytest.raises(RuntimeError, match="port in use"):
        await start_server(
            ctx,
            StartServerInput(
                user_description=TOOL_NARRATION, command="python3 app.py", project_path="/workspace"
            ),
        )


async def test_the_failure_log_tail_states_its_own_budget(tmp_path: Path) -> None:
    """The tail runs after a start that already ended, and reads twenty lines out of one file: it
    must not sit on the sandbox's 120s default, which is a budget for work rather than for a failure
    report."""
    sandbox = FakeSandbox(
        scripted={
            "nohup": ExecResult(stdout="", stderr="", exit_code=1),
            "tail -n 20": ExecResult(stdout="Traceback: port in use", stderr="", exit_code=0),
        }
    )
    ctx = _context(sandbox, tmp_path)
    with pytest.raises(RuntimeError, match="port in use"):
        await start_server(
            ctx,
            StartServerInput(
                user_description=TOOL_NARRATION, command="python3 app.py", project_path="/workspace"
            ),
        )
    tail = next(command for command in sandbox.commands if command.startswith("tail -n 20"))
    assert sandbox.budgets[tail] == LOG_TAIL_TIMEOUT_SECONDS


async def test_a_start_the_sandbox_killed_names_the_deadline_when_the_log_is_empty(
    tmp_path: Path,
) -> None:
    """The carrier's kill leaves its own `timed out` and nothing else, so a server that never bound
    its port and wrote no log would be reported as an empty error. Name the deadline that ended it
    and say the log holds nothing."""
    sandbox = FakeSandbox(
        scripted={
            "nohup": ExecResult(
                stdout="",
                stderr="",
                exit_code=124,
                timed_out_after_s=READINESS_TIMEOUT_SECONDS + 5,
            )
        }
    )
    ctx = _context(sandbox, tmp_path)
    with pytest.raises(RuntimeError, match=f"after {READINESS_TIMEOUT_SECONDS + 5}s"):
        await start_server(
            ctx,
            StartServerInput(
                user_description=TOOL_NARRATION, command="python3 app.py", project_path="/workspace"
            ),
        )


async def test_a_model_named_path_is_scoped_to_the_workspace(tmp_path: Path) -> None:
    """These tools quote their path arguments into `cd` and `>` rather than opening them, and under
    the local carrier those run on the host. So every path the model names is resolved under
    /workspace first: a traversal is refused before a command is built, and nothing runs."""
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        await website(
            ctx,
            WebsiteInput(
                user_description=TOOL_NARRATION,
                run_command="npm run build",
                project_path="/workspace/../etc",
            ),
        )
    with pytest.raises(ValueError, match="escapes"):
        await start_server(
            ctx,
            StartServerInput(
                user_description=TOOL_NARRATION,
                command="python3 app.py",
                project_path="/workspace",
                log_file="/etc/cron.d/server.log",
            ),
        )
    assert sandbox.commands == []


async def test_a_server_log_defaults_into_the_engines_own_offload_dir(tmp_path: Path) -> None:
    """A log path defaulted into a shared directory is a predictable name in a place the agent can
    write, which is the setup for a swap rather than merely untidy. The default is `.tool-output`,
    the engine's own directory under the workspace the tool already serves from — a server log is
    scaffolding, and the workspace listing is the member's own file list. The tool reports it."""
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    result = await start_server(
        ctx,
        StartServerInput(
            user_description=TOOL_NARRATION,
            command="python3 app.py",
            project_path="site",
            port=5173,
        ),
    )
    payload = json.loads(result.content[0].text)
    assert payload["log"] == "/workspace/.tool-output/server-5173.log"
    assert payload["project_path"] == "/workspace/site"
    assert sandbox.programs == [
        (LOG_CLEAR_PROG, ("/workspace/.tool-output/server-5173.log", "/workspace"))
    ]
    launch = next(
        command
        for command in sandbox.commands
        if ">/workspace/.tool-output/server-5173.log" in command
    )
    redirect = launch.index(">/workspace/.tool-output/server-5173.log")
    assert "set -C\n" in launch[:redirect], "the redirect must create the log, never truncate it"
    assert "set +C\n" in launch[redirect:], "noclobber must not outlive the log's own redirect"


async def test_the_log_name_is_freed_through_the_guard_before_the_redirect_creates_it(
    tmp_path: Path,
) -> None:
    """`nohup … >log` follows a link and truncates what it points at, and the log's name is a
    predictable one in a directory the agent writes. So the name is emptied through the containment
    guard first and the redirect runs under `set -C`, which creates the file `O_CREAT|O_EXCL`: a
    link replanted between the two commands fails the launch rather than steering it. A clear the
    guard refuses stops the launch instead of running the redirect anyway."""
    sandbox = FakeSandbox(
        claim=ExecResult(stdout="", stderr="log.txt is not a regular file", exit_code=1)
    )
    ctx = _context(sandbox, tmp_path)

    with pytest.raises(RuntimeError, match="not a regular file"):
        await start_server(
            ctx,
            StartServerInput(
                user_description=TOOL_NARRATION,
                command="python3 app.py",
                project_path="/workspace",
                log_file="log.txt",
            ),
        )

    assert sandbox.programs == [(LOG_CLEAR_PROG, ("/workspace/log.txt", "/workspace"))]
    assert "containment" in LOG_CLEAR_PROG
    assert sandbox.commands == []


def test_site_name_slugs_bounds_and_refuses_a_nameless_site() -> None:
    """The name is the site's identity, half its link, and its object name, so a name that slugs to
    nothing is refused rather than stored as one — and the bound leaves room for the conversation
    digest the object name appends."""
    assert site_name("My Marketing Site!") == "my-marketing-site"
    assert site_name("Q3  --  Report") == "q3-report"
    assert site_name("x" * 80) == "x" * SITE_NAME_MAX
    assert SITE_NAME_MAX + len("-") + CONVERSATION_DIGEST_HEX <= OBJECT_NAME_MAX_LENGTH, (
        "a site's longest name plus its object suffix must stay addressable from chat"
    )
    assert len(site_object_name(uuid4(), site_name("x" * 80))) <= OBJECT_NAME_MAX_LENGTH
    conversation_id = uuid4()
    object_name = site_object_name(conversation_id, "dashboard")
    assert site_name_from_object(conversation_id, object_name) == "dashboard"
    assert site_name_from_object(uuid4(), object_name) is None
    for nameless in ("---", "   ", "🌱🌱", "!!!"):
        with pytest.raises(InvalidSiteName, match="letters or digits"):
            site_name(nameless)


def test_the_card_page_carries_the_approved_composition() -> None:
    """The card's measurements live in one page, so they are read back off it: the 1200x630 board,
    the 456px panel and the 744px the site's page fills, the kicker above the lockup, and the name
    at the foot clamped to two lines. The brand files ride inside the page rather than being
    fetched, because the sandbox that draws it has no checkout and no route to the portal.

    The name is the member's own text landing in markup, so the escape is asserted here too."""
    page = card_page("Marketing <script>alert(1)</script>", CARD_SHOT_DRAWN)

    assert CARD_WIDTH == PANEL_WIDTH + SHOT_WIDTH
    assert f"width:{CARD_WIDTH}px; height:{CARD_HEIGHT}px" in page
    assert f"width:{PANEL_WIDTH}px" in page
    assert f"width:{SHOT_WIDTH}px" in page
    assert ">Made with<" in page
    assert "font-size:26px" in page and "letter-spacing:0.15em" in page
    assert "font-size:34px" in page and "line-height:1.28" in page
    assert "-webkit-line-clamp:2" in page
    assert "<svg" in page and "<?xml" not in page
    assert "data:font/woff2;base64," in page
    assert SHOT_TOKEN in page
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    # The shot is sized two ways and fitted neither: halved for a shot taken at the card's own
    # width, at its own size for a stored page picture the box then crops.
    assert f"width:{SHOT_WIDTH}px;height:{CARD_HEIGHT}px" in page
    assert "width:auto;height:auto" in card_page("marketing", STORED_SHOT_DRAWN)


def test_the_cards_brand_files_are_the_portals_own() -> None:
    """The panel is drawn inside the sandbox, so the lockup and the face are copied into this
    package rather than read from the portal's source tree. A copy is only honest while it stays
    identical, so both are held byte for byte — the rule the gateway's own mark is held to."""
    portal = Path("extensions/web/frontend/src/assets")
    assets = Path(share_card.__file__).parent / "assets"

    assert (assets / LOGO_ASSET).read_bytes() == (portal / "ufo-logo.svg").read_bytes()
    assert (assets / FONT_ASSET).read_bytes() == (portal / "fonts" / FONT_ASSET).read_bytes()


def test_the_shot_is_drawn_by_the_image_own_browser_under_its_own_wall() -> None:
    """Both shots a card costs are taken by the browser the sandbox image and the CI runner already
    carry, which is the one the site's page shot is taken with, so a card costs no renderer that
    either of them has to install separately.

    The wall is asserted with it, because nothing on the browser's command line ends a run: the run
    is walled from outside, and the shot on disk rather than the browser's status is what says the
    picture was drawn. `--virtual-time-budget` is asserted absent, because the capture waits for
    that budget and virtual time stands still while any fetch is pending, so a browser carrying it
    draws nothing at all wherever a background service holds one open.

    The unattended flags ride in every run for the same reason: a runner has no keyring, no keychain
    and no crash server, and a browser that waits on one of those spends the whole wall and draws
    nothing."""
    shot = "/workspace/.tool-output/share-card-shot-marketing.png"
    command = shot_command(
        url="http://127.0.0.1:8000",
        width=SHOT_WIDTH,
        height=CARD_HEIGHT,
        scale=2,
        shot=shot,
        profile="/tmp/ufo-share-card",
        root="/workspace",
    )

    for browser in BROWSER_COMMANDS:
        assert browser in command
    assert "playwright" not in command
    assert "--virtual-time-budget" not in command
    for flag in UNATTENDED_FLAGS.split():
        assert flag in command
    assert "--password-store=basic" in command
    assert "--use-mock-keychain" in command
    assert "--disable-component-extensions-with-background-pages" in command
    assert f"timeout --signal=KILL {SHOT_DEADLINE_SECONDS}s" in command
    assert SHOT_DEADLINE_SECONDS < CARD_TIMEOUT_SECONDS
    assert f"test -s {shlex.quote(shot)}" in command


def test_the_shot_is_driven_to_a_settle_point_inside_its_wall() -> None:
    """The browser is driven rather than one-shot, because the load event is the only settle point a
    one-shot chromium offers and it is too early: a page that reveals its content with an entrance
    animation, or writes its DOM after load, is photographed blank there — and a blank shot is a
    white PNG, which is not an empty file, so the size check accepts it as a picture.

    So the run carries the driver: it speaks the DevTools protocol over the browser's own pipe,
    waits for the load event and then for a frame that repeats with no request in flight, and
    refuses a shot that is one flat colour. Every wait it takes is walled, and the two walls
    together sit inside the kill wall, so a page that never goes idle is photographed rather than
    waited on — the failure `--virtual-time-budget` had, which is why it is asserted absent
    above."""
    shot = "/workspace/.tool-output/preview-8000.png"
    command = shot_command(
        url="http://127.0.0.1:8000",
        width=PREVIEW_WIDTH,
        height=PREVIEW_HEIGHT,
        scale=1,
        shot=shot,
        profile="/tmp/ufo-site-preview",
        root="/workspace",
    )

    assert "--screenshot=" not in command
    assert "--remote-debugging-pipe" in command
    assert "Page.loadEventFired" in command
    assert "Network.enable" in command
    assert "Page.captureScreenshot" in command
    assert "Emulation.setDeviceMetricsOverride" in command
    assert "flat colour" in command
    # The driver runs isolated with the baked guard on its path, and writes the shot through the
    # guard rather than letting the browser create a name the agent's own directory holds.
    assert f"python3 {SANDBOX_PYTHON_FLAG} -" in command
    assert SANDBOX_MODULE_BOOTSTRAP in command
    assert "contained_file" in command
    assert LOAD_WALL_SECONDS + SETTLE_WALL_SECONDS < SHOT_DEADLINE_SECONDS
    assert f"LOAD_WALL = {LOAD_WALL_SECONDS}" in command
    assert f"SETTLE_WALL = {SETTLE_WALL_SECONDS}" in command
    assert f"FRAME = {FRAME_SECONDS}" in command
    for argument in (shlex.quote(shot), shlex.quote("/tmp/ufo-site-preview"), "/workspace"):
        assert argument in command


def test_start_server_rejects_an_out_of_range_port() -> None:
    with pytest.raises(ValidationError, match="between 1 and 65535"):
        StartServerInput(
            user_description=TOOL_NARRATION,
            command="python3 app.py",
            project_path="/workspace",
            port=99999,
        )
