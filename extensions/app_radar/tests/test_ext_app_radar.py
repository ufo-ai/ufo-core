from pathlib import Path

import ufo_ext_app_radar.manifest as app_radar

SKILL_DIR = Path(app_radar.__file__).parent / "skills" / "app-radar-home"


def test_app_radar_ships_one_workspace_agent() -> None:
    manifest = app_radar.manifest()
    assert manifest.name == "app_radar"
    assert [provision.name for provision in manifest.agents] == ["radar"]
    provision = manifest.agents[0]
    assert provision.spec.visibility == "workspace"
    assert "app-radar-home" in provision.spec.prompt
    assert {path.name for path in (spec.path for spec in manifest.skills)} == {"app-radar-home"}


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
        "cp .skills/app-radar-home/index.html .skills/app-radar-home/app.js "
        ".skills/app-radar-home/app.tsx .skills/app-bridge/bridge.js site/"
    ) in skill
    assert "deploy_website" in skill
    assert "set_homepage" in skill
