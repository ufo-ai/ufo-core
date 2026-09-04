import re
from pathlib import Path

import ufo_ext_app_artifacts.manifest as app_artifacts

from ufo.runtime.skills.runtime import CORE_SKILL_REGISTRY

SKILL_DIR = Path(app_artifacts.__file__).parent / "skills" / "app-artifacts-home"
BUILD_ENTRY = (
    Path(app_artifacts.__file__).parents[2]
    / "web"
    / "frontend"
    / "apps"
    / "artifacts"
    / "index.html"
)
MODULE_SCRIPT = re.compile(r'<script type="module" src="([^"]+)">')


def test_app_artifacts_ships_one_workspace_agent() -> None:
    manifest = app_artifacts.manifest()
    assert manifest.name == "app_artifacts"
    assert [provision.name for provision in manifest.agents] == ["artifacts"]
    provision = manifest.agents[0]
    assert provision.icon == "books"
    assert provision.spec.visibility == "workspace"
    assert "app-artifacts-home" in provision.spec.prompt
    assert {path.name for path in (spec.path for spec in manifest.skills)} == {"app-artifacts-home"}


def test_the_home_skill_edits_builds_and_deploys_the_project() -> None:
    skill = (SKILL_DIR / "SKILL.md").read_text()
    assert "Copy this skill's `app.tsx`, `index.html`, and the two logo-sheet files" in skill
    assert "Edit `app.tsx`" in skill
    assert (
        "`deploy_website` action (`object_action` with kind `site`) with that directory and "
        "`site_name` `artifacts-home`"
    ) in skill
    assert "do not run a build yourself" in skill
    assert "`object_get` with an empty `ref` reads this turn's own agent" in skill
    assert "`set_homepage` action's call template already carries the agent's name" in skill
    assert f"`{app_artifacts.manifest().agents[0].name}`" not in skill
    assert "`ref` unchanged to `object_get`" in skill
    assert "edit the `app.tsx` under `src`" in skill


def test_the_shelf_lists_the_logo_sheet_the_deploy_ships() -> None:
    """The entry is the page's own card, drawn from bytes committed beside it, so nothing is seeded
    and no share is faked. The bytes are the ones `ufo-style` ships: two copies of one file drift,
    and this copy is the one every member reads."""
    page = (SKILL_DIR / "app.tsx").read_text()
    assert 'import LOGO_SHEET_URL from "./ufo-logo-ratio.pdf?url"' in page
    assert 'import LOGO_SHEET_COVER from "./ufo-logo-ratio-cover.png?url"' in page
    assert "sheet && !files.payload.next_cursor ? [fileCard(SHEET, viewer)] : []" in page
    # The card is the page's own, so it never stands in for the workspace's shelf: a member who has
    # shared nothing still reads where their own files land.
    assert "const bare = !shown.length && !files.payload.objects.length;" in page
    assert "{payload.bare ? (" in page
    sheet = SKILL_DIR / "ufo-logo-ratio.pdf"
    assert f"size_bytes: {sheet.stat().st_size}" in page
    style = dict(CORE_SKILL_REGISTRY.named("ufo-style").files)
    assert sheet.read_bytes() == style["assets/ufo-logo-ratio.pdf"]
