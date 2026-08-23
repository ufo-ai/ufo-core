from pathlib import Path

import ufo_ext_app_tasks.manifest as app_tasks

SKILL_DIR = Path(app_tasks.__file__).parent / "skills" / "app-tasks-home"


def test_app_tasks_ships_one_workspace_agent() -> None:
    manifest = app_tasks.manifest()
    assert manifest.name == "app_tasks"
    assert [provision.name for provision in manifest.agents] == ["tasks"]
    provision = manifest.agents[0]
    assert provision.icon == "clock-play"
    assert provision.spec.visibility == "workspace"
    assert "app-tasks-home" in provision.spec.prompt
    assert {path.name for path in (spec.path for spec in manifest.skills)} == {"app-tasks-home"}


def test_the_page_is_the_apps_own_tsx_run_by_the_kit() -> None:
    page = (SKILL_DIR / "index.html").read_text()
    assert '<script src="./bridge.js">' in page
    assert '<script src="./app.js">' in page
    loader = (SKILL_DIR / "app.js").read_text()
    assert 'UfoAppKit.run("./app.tsx")' in loader
    assert 'init.portal + "/surface/web/static/assets/"' in loader
    source = (SKILL_DIR / "app.tsx").read_text()
    assert "= UfoAppKit;" in source
    assert "mountApp(" in source


def test_the_home_skill_stages_the_page_and_the_bridge() -> None:
    skill = (SKILL_DIR / "SKILL.md").read_text()
    assert "app-bridge" in skill
    assert (
        "cp .skills/app-tasks-home/index.html .skills/app-tasks-home/app.js "
        ".skills/app-tasks-home/app.tsx .skills/app-bridge/bridge.js site/"
    ) in skill
    assert "deploy_website" in skill
    assert "set_homepage" in skill
