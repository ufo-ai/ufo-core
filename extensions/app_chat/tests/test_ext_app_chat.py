import re
from pathlib import Path

import ufo_ext_app_chat.manifest as app_chat

from ufo.schema.records import DEFAULT_AGENT_NAME

SKILL_DIR = Path(app_chat.__file__).parent / "skills" / "app-chat-home"
BUILD_ENTRY = (
    Path(app_chat.__file__).parents[2] / "web" / "frontend" / "apps" / "chat" / "index.html"
)
MODULE_SCRIPT = re.compile(r'<script type="module" src="([^"]+)">')


def test_app_chat_ships_one_workspace_agent() -> None:
    manifest = app_chat.manifest()
    assert manifest.name == "app_chat"
    assert [provision.name for provision in manifest.agents] == ["chat"]
    provision = manifest.agents[0]
    assert provision.icon == "message-circle"
    assert provision.spec.visibility == "workspace"
    assert "app-chat-home" in provision.spec.prompt
    assert provision.tools is None


def test_the_chat_app_is_the_workspaces_main_agent() -> None:
    provision = app_chat.manifest().agents[0]
    assert provision.main
    assert provision.spec.internet_access_allowed
    assert provision.name == DEFAULT_AGENT_NAME


def test_the_home_skill_edits_builds_and_deploys_the_project() -> None:
    skill = (SKILL_DIR / "SKILL.md").read_text()
    assert "Copy this skill's `app.tsx` and `index.html`" in skill
    assert "Edit `app.tsx`" in skill
    assert (
        "`deploy_website` action (`object_action` with kind `site`) with that directory and "
        "`site_name` `chat-home`"
    ) in skill
    assert "do not run a build yourself" in skill
    assert "`object_get` with an empty `ref` reads this turn's own agent" in skill
    assert "`set_homepage` action's call template already carries the agent's name" in skill
    assert f"`{app_chat.manifest().agents[0].name}`" not in skill
    assert "`ref` unchanged to `object_get`" in skill
    assert "edit the `app.tsx` under `src`" in skill
