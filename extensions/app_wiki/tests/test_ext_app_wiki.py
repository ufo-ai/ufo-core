import re
from pathlib import Path

import ufo_ext_app_wiki.manifest as app_wiki

SKILL_DIR = Path(app_wiki.__file__).parent / "skills" / "app-wiki-home"
BUILD_ENTRY = (
    Path(app_wiki.__file__).parents[2] / "web" / "frontend" / "apps" / "wiki" / "index.html"
)
MODULE_SCRIPT = re.compile(r'<script type="module" src="([^"]+)">')


def test_app_wiki_ships_one_private_agent() -> None:
    manifest = app_wiki.manifest()
    assert manifest.name == "app_wiki"
    assert [provision.name for provision in manifest.agents] == ["wiki"]
    provision = manifest.agents[0]
    assert provision.icon == "book"
    assert provision.spec.visibility == "private"
    assert "app-wiki-home" in provision.spec.prompt
    assert {path.name for path in (spec.path for spec in manifest.skills)} == {"app-wiki-home"}


def test_the_home_skill_edits_builds_and_deploys_the_project() -> None:
    skill = (SKILL_DIR / "SKILL.md").read_text()
    assert "Copy this skill's `app.tsx` and `index.html`" in skill
    assert "Edit `app.tsx`" in skill
    assert (
        "`deploy_website` action (`object_action` with kind `site`) with that directory and "
        "`site_name` `wiki-home`"
    ) in skill
    assert "do not run a build yourself" in skill
    assert "`object_get` kind `agent` with an empty name reads this turn's own agent" in skill
    assert "`set_homepage` action's call template already carries the agent's name" in skill
    assert f"`{app_wiki.manifest().agents[0].name}`" not in skill
    assert "`object_get` the site" in skill
    assert "edit the `app.tsx` under `src`" in skill
