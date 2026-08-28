import sqlalchemy as sa
from alembic import op

revision: str = "20260828010853"
down_revision: str | None = "20260828052547"
branch_labels: str | None = None
depends_on: str | None = None

RENAMED = {
    "slack_connect": "action:surface:slack_connect",
    "slack_app_manifest": "action:surface:slack_app_manifest",
    "slack_channels": "action:surface:slack_channels",
    "imessage_connect": "action:surface:imessage_connect",
    "skill_search": "action:skill:skill_search",
    "monitor": "action:monitor:monitor",
    "add_member": "action:member:add_member",
    "restore_application": "action:agent:restore_application",
    "request_credentials": "action:credential:request_credentials",
    "grant_web_access": "action:member:grant_web_access",
    "revoke_web_access": "action:member:revoke_web_access",
    "read_private_transcript": "action:conversation:read_private_transcript",
    "connect_github": "action:credential:connect_github",
    "manage_billing": "action:workspace:manage_billing",
    "rebuild_page_facts": "action:page:rebuild_page_facts",
    "rebuild_report_digest": "action:report:rebuild_report_digest",
    "deploy_website": "action:site:deploy_website",
    "publish_website": "action:site:publish_website",
    "build_website": "action:site:build_website",
    "build_ufo_application": "action:site:build_ufo_application",
    "render_application_preview": "action:site:render_application_preview",
    "set_homepage": "action:agent:set_homepage",
    "generate_image": "action:artifact:generate_image",
    "generate_video": "action:artifact:generate_video",
}
WEB_EXTENSION = "web"
HOMEPAGE_SEED_PREFIX = "homepage-seed/"
WITHHELD_TOOLS = "withheld-tools"

agent = sa.table(
    "agent",
    sa.column("id", sa.Uuid()),
    sa.column("tools", sa.JSON()),
)
ext_store = sa.table(
    "ext_store",
    sa.column("workspace_id", sa.Uuid()),
    sa.column("extension", sa.Text()),
    sa.column("key", sa.Text()),
    sa.column("value", sa.JSON()),
)


def _rewrite(mapping: dict[str, str]) -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(agent.c.id, agent.c.tools).where(agent.c.tools.is_not(None))
    ).all()
    for agent_id, tools in rows:
        if not isinstance(tools, list):
            continue
        renamed = [mapping.get(name, name) for name in tools]
        if renamed == tools:
            continue
        bind.execute(sa.update(agent).where(agent.c.id == agent_id).values(tools=renamed))


def upgrade() -> None:
    _rewrite(RENAMED)
    bind = op.get_bind()
    marked = bind.execute(
        sa.select(ext_store.c.workspace_id, ext_store.c.key, ext_store.c.value).where(
            ext_store.c.extension == WEB_EXTENSION,
            ext_store.c.key.startswith(HOMEPAGE_SEED_PREFIX),
        )
    ).all()
    for workspace_id, key, value in marked:
        if value == WITHHELD_TOOLS:
            bind.execute(
                ext_store.delete().where(
                    ext_store.c.workspace_id == workspace_id,
                    ext_store.c.extension == WEB_EXTENSION,
                    ext_store.c.key == key,
                )
            )


def downgrade() -> None:
    _rewrite({action: name for name, action in RENAMED.items()})
