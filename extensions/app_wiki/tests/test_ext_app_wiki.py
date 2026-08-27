import re
from pathlib import Path

import ufo_ext_app_wiki.manifest as app_wiki

SKILL_DIR = Path(app_wiki.__file__).parent / "skills" / "app-wiki-home"
BUILD_ENTRY = (
    Path(app_wiki.__file__).parents[2] / "web" / "frontend" / "apps" / "wiki" / "index.html"
)
MODULE_SCRIPT = re.compile(r'<script type="module" src="([^"]+)">')


def test_app_wiki_ships_one_workspace_agent() -> None:
    manifest = app_wiki.manifest()
    assert manifest.name == "app_wiki"
    assert [provision.name for provision in manifest.agents] == ["wiki"]
    provision = manifest.agents[0]
    assert provision.icon == "book"
    assert provision.spec.visibility == "workspace"
    assert "app-wiki-home" in provision.spec.prompt
    assert provision.flag == app_wiki.WIKI_APP_FLAG
    assert {path.name for path in (spec.path for spec in manifest.skills)} == {"app-wiki-home"}


def test_the_app_ships_dark_behind_the_wiki_app_flag() -> None:
    """The provision names a flag, so the app reaches a workspace only where the deploy's flag
    backend turns `wiki-app` on. The read fails closed, so a deploy with no flag backend creates no
    wiki agent at all — which is what lets the app ship before it is offered."""
    assert app_wiki.WIKI_APP_FLAG == "wiki-app"
    assert app_wiki.manifest().agents[0].flag == app_wiki.WIKI_APP_FLAG


def test_the_built_page_is_the_apps_own_tsx() -> None:
    entry = MODULE_SCRIPT.search(BUILD_ENTRY.read_text())
    assert entry is not None
    assert (BUILD_ENTRY.parent / entry[1]).resolve() == (SKILL_DIR / "app.tsx").resolve()
    source = (SKILL_DIR / "app.tsx").read_text()
    assert "Facts" in source
    assert "mountApp(" in source


def test_the_home_skill_edits_builds_and_deploys_the_project() -> None:
    skill = (SKILL_DIR / "SKILL.md").read_text()
    assert "Copy this skill's `app.tsx` and `index.html`" in skill
    assert "Edit `app.tsx`" in skill
    assert "`deploy_website` with that directory and `site_name` `wiki-home`" in skill
    assert "do not run a build yourself" in skill
    assert "set_homepage" in skill
    assert "`object_get` the site" in skill
    assert "edit the `app.tsx` under `src`" in skill
