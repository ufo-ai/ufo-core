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


def test_the_built_page_is_the_apps_own_tsx() -> None:
    entry = MODULE_SCRIPT.search(BUILD_ENTRY.read_text())
    assert entry is not None
    assert (BUILD_ENTRY.parent / entry[1]).resolve() == (SKILL_DIR / "app.tsx").resolve()
    source = (SKILL_DIR / "app.tsx").read_text()
    assert "RebuildDialog" in source
    assert "FileSheet" in source
    assert "FileBody" not in source
    assert "FileDownload" not in source
    assert "mountApp(" in source


def test_the_home_skill_edits_builds_and_deploys_the_project() -> None:
    skill = (SKILL_DIR / "SKILL.md").read_text()
    assert "Copy this skill's `app.tsx` and `index.html`" in skill
    assert "Edit `app.tsx`" in skill
    assert "`deploy_website` with that directory and `site_name` `radar-home`" in skill
    assert "do not run a build yourself" in skill
    assert "set_homepage" in skill
    assert "`object_get` the site" in skill
    assert "edit the `app.tsx` under `src`" in skill
