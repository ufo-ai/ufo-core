from cryptography.fernet import Fernet

from ufo.host.ext.loader import load_manifests, turn_tools
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.turns.audience import conversation_audience

HOSTED_PACK = "assistant_hosted"

RETAINED_GLOBAL_TOOLS = frozenset(
    {
        "object_list",
        "object_get",
        "object_explain",
        "object_apply",
        "object_delete",
        "object_action",
        "bash",
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "ask_user",
        "pause_and_wait",
        "spawn",
        "cancel_spawn",
        "message_spawn",
        "update_todo_list",
        "update_todo_status",
        "plan_objective",
        "run_independent_steps",
        "record_step",
        "read_objective",
        "report_problem",
        "notify",
        "load_skill",
        "memory_search",
        "memory_update",
        "share_file",
        "start_server",
        "search_web",
        "fetch_url",
        "search_vertical",
        "wide_research",
        "js_repl",
        "xlsx_repl",
        "browser_task",
        "wide_browse",
        "connect_account",
        "list_external_tools",
        "describe_external_tools",
        "search_connector_tools",
        "call_external_tool",
        "list_mcp_tools",
        "call_mcp_tool",
    }
)

MOVED_CANDIDATES = frozenset(
    {
        "add_member",
        "grant_web_access",
        "revoke_web_access",
        "restore_application",
        "request_credentials",
        "read_private_transcript",
        "skill_search",
        "slack_connect",
        "slack_app_manifest",
        "slack_channels",
        "imessage_connect",
        "manage_billing",
        "rebuild_page_facts",
        "rebuild_report_digest",
        "monitor",
        "deploy_website",
        "publish_website",
        "build_website",
        "set_homepage",
        "generate_image",
        "generate_video",
    }
)


def _registry(pack: str | None):
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    return turn_tools(load_manifests(pack), store, audience=conversation_audience(None))


def test_hosted_member_facing_registry_is_exactly_the_retained_global_set() -> None:
    tools, _, _ = _registry(HOSTED_PACK)
    assert {tool.name for tool in tools if not tool.profile_only} == RETAINED_GLOBAL_TOOLS


def test_every_candidate_registers_as_an_action_and_never_as_a_global_tool() -> None:
    tools, _, verbs = _registry(None)
    assert {tool.name for tool in tools}.isdisjoint(MOVED_CANDIDATES)
    registered = {bound.action.name for held in verbs.actions.values() for bound in held.values()}
    assert MOVED_CANDIDATES <= registered
