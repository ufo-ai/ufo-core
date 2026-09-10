import re
from pathlib import Path

import ufo_ext_app_radar.manifest as app_radar

SKILL_DIR = Path(app_radar.__file__).parent / "skills" / "app-radar-home"
BUILD_ENTRY = (
    Path(app_radar.__file__).parents[2] / "web" / "frontend" / "apps" / "radar" / "index.html"
)
MODULE_SCRIPT = re.compile(r'<script type="module" src="([^"]+)">')


def test_app_radar_ships_one_workspace_agent() -> None:
    manifest = app_radar.manifest()
    assert manifest.name == "app_radar"
    assert [provision.name for provision in manifest.agents] == ["radar"]
    provision = manifest.agents[0]
    assert provision.icon == "radar"
    assert provision.spec.visibility == "workspace"
    assert "app-radar-home" in provision.spec.prompt
    assert {path.name for path in (spec.path for spec in manifest.skills)} == {"app-radar-home"}


def test_the_home_skill_edits_builds_and_deploys_the_project() -> None:
    skill = (SKILL_DIR / "SKILL.md").read_text()
    assert "Copy this skill's `app.tsx`, `index.html`, and `tour.md`" in skill
    assert "Edit `app.tsx`" in skill
    assert (
        "`deploy_website` action (`object_action` with kind `site`) with that directory and "
        "`site_name` `radar-home`"
    ) in skill
    assert "do not run a build yourself" in skill
    assert "`object_get` with an empty `ref` reads this turn's own agent" in skill
    assert "`set_homepage` action's call template already carries the agent's name" in skill
    assert f"`{app_radar.manifest().agents[0].name}`" not in skill
    assert "`ref` unchanged to `object_get`" in skill
    assert "edit the `app.tsx` under `src`" in skill


PAGE = (SKILL_DIR / "app.tsx").read_text()
FEED_LIST = PAGE[PAGE.index("function Feed(") : PAGE.index("function RunRow(")]
FRONTMATTER, TOUR_BODY = (
    (SKILL_DIR / "tour.md").read_text().removeprefix("---\n").split("\n---\n", 1)
)


def test_the_tour_stands_as_the_oldest_row_in_every_workspace() -> None:
    """Every workspace reads the tour, new or old: it closes a rail the feed orders newest day
    first, so it stands under the oldest report and moves none of them. A workspace that has never
    run reads it as the only row, by the same one branch — the feed keeps no separate empty screen
    to drift from this one."""
    assert "{payload.next_cursor ? null : (" in FEED_LIST
    assert FEED_LIST.index("{days.map((key) => {") < FEED_LIST.index("<TourRow")
    assert "if (!runs.length" not in PAGE
    assert 'const TOUR_SLOT = "tour"' in PAGE
    assert "id === TOUR_SLOT ? (\n          <TourSheet" in PAGE


def test_the_tour_closes_the_rail_once_and_only_where_no_older_report_is_left() -> None:
    """The oldest row is the one with nothing behind it, so the tour stands on the page whose read
    answers no further cursor and on no page before it — a member stepping through older reports
    meets it once, at the foot of the last page. It takes the same `Row` every report takes, so it
    is read as one of them rather than as a banner under them."""
    row = PAGE[PAGE.index("function TourRow(") : PAGE.index("function Row(")]
    assert "<Row" in row
    assert 'sectionHash("radar", { opens: [TOUR_SLOT] })' in row
    assert "next_cursor" not in row


def test_the_tour_is_one_committed_document_and_no_stored_row() -> None:
    """The words are `tour.md` beside the page, taken into the bundle the deploy serves every
    workspace: editing the document reaches every workspace on the next deploy, with no
    per-workspace copy to migrate and no report row to mint. Nothing about the tour is held per
    browser or per member either, so two teammates read the same rail."""
    assert 'import TOUR_DOCUMENT from "./tour.md?raw"' in PAGE
    assert "const TOUR = tourWritten(TOUR_DOCUMENT)" in PAGE
    assert 'title: "What this workspace can do"' in FRONTMATTER
    assert 'lead: "Start here"' in FRONTMATTER
    assert re.search(r'^summary: ".*"$', FRONTMATTER, re.M)
    assert len(re.findall(r'^ *- text: ".*"\n *actor: ".*"$', FRONTMATTER, re.M)) == 4
    assert "localStorage" not in PAGE
    assert "/objects/report" in PAGE
    assert '"POST"' not in PAGE


def test_the_tour_states_what_the_workspace_does_and_what_to_connect() -> None:
    """The whole of what a new team is told. Each subject is one a member acts on — the surfaces
    they reach the workspace from, the two setups worth doing first, the parts the workspace keeps,
    the services it reaches, the model behind an agent, and where the files land — so a subject
    dropped from the document leaves the tour naming work the member cannot start."""
    for said in (
        "Every member reaches the same agents and the same conversations",
        "**Slack.**",
        "**The web portal.**",
        "**The terminal.**",
        "## Set up sales work",
        "Connect the CRM and the mailbox",
        "Ask for lead research",
        "Ask for outreach drafts",
        "## Set up engineering work",
        "Connect GitHub",
        "Ask for a pull request review",
        "Schedule a code check",
        "keeps its parts as objects",
        "**Agents**",
        "**Sites**",
        "**Skills**",
        "**Credentials**",
        "**Scheduled tasks**",
        "## Connect other services",
        "## Choose the model",
        "## Artifacts",
        "The Artifacts app lists every one",
        "ufo-logo-ratio.pdf",
        "share the logo sheet",
    ):
        assert said in TOUR_BODY, said


def test_the_tour_opens_the_artifacts_app_and_the_connectors_page() -> None:
    """The tour names two screens, and a named screen is reached rather than described: the drawer
    stands the anchors under the prose, because a link inside markdown opens in a tab of the app
    frame's own origin instead of moving the portal."""
    sheet = PAGE[PAGE.index("function TourSheet(") :]
    assert 'sectionHash("artifacts")' in sheet
    assert 'sectionHash("connectors")' in sheet
