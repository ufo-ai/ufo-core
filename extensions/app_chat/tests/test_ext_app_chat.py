from pathlib import Path

import ufo_ext_app_chat.manifest as app_chat


def _skill_dir(name: str) -> Path:
    return Path(app_chat.__file__).parent / "skills" / name


def test_app_chat_ships_one_workspace_agent() -> None:
    manifest = app_chat.manifest()
    assert manifest.name == "app_chat"
    assert [provision.name for provision in manifest.agents] == ["chat"]
    provision = manifest.agents[0]
    assert provision.icon == "message-circle"
    assert provision.spec.visibility == "workspace"
    assert "app-chat-home" in provision.spec.prompt
    # tools None gives the member-facing set, so the homepage seed job can build and bind the page.
    assert provision.tools is None


def test_app_chat_ships_the_bridge_and_home_skills() -> None:
    manifest = app_chat.manifest()
    names = {path.name for path in (spec.path for spec in manifest.skills)}
    assert names == {"app-bridge", "app-chat-home"}
    assert (_skill_dir("app-bridge") / "bridge.js").is_file()
    assert (_skill_dir("app-bridge") / "SKILL.md").is_file()


def test_the_page_is_the_apps_own_tsx_run_by_the_kit() -> None:
    page = (_skill_dir("app-chat-home") / "index.html").read_text()
    assert '<script src="./bridge.js">' in page
    assert '<script src="./app.js">' in page
    loader = (_skill_dir("app-chat-home") / "app.js").read_text()
    assert 'UfoAppKit.run("./app.tsx")' in loader
    assert 'init.portal + "/surface/web/static/assets/"' in loader
    source = (_skill_dir("app-chat-home") / "app.tsx").read_text()
    assert "= UfoAppKit;" in source
    assert "ChatPane" in source
    assert "mountApp(" in source


def test_the_home_skill_stages_the_page_and_the_bridge() -> None:
    skill = (_skill_dir("app-chat-home") / "SKILL.md").read_text()
    assert "app-bridge" in skill
    assert (
        "cp .skills/app-chat-home/index.html .skills/app-chat-home/app.js "
        ".skills/app-chat-home/app.tsx .skills/app-bridge/bridge.js site/"
    ) in skill
    assert "deploy_website" in skill
    assert "set_homepage" in skill
