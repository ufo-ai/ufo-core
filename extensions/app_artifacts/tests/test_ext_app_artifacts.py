import re
from pathlib import Path

import ufo_ext_app_artifacts.manifest as app_artifacts

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
    assert "Copy this skill's `app.tsx` and `index.html`" in skill
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
