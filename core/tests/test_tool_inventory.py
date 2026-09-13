import pytest
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
        "get_context_remaining",
        "new_context",
        "search_history",
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

REQUESTER_GLOBAL_TOOLS = frozenset(
    {
        "bash",
        "call_external_tool",
        "connect_account",
        "list_external_tools",
        "memory_search",
        "notify",
        "object_action",
        "object_apply",
        "object_delete",
        "object_get",
        "object_list",
        "spawn",
        "start_server",
    }
)

CONVERSATION_COMMON_GLOBAL_TOOLS = frozenset(
    {
        "ask_user",
        "browser_task",
        "describe_external_tools",
        "js_repl",
        "pause_and_wait",
        "plan_objective",
        "read_objective",
        "record_step",
        "report_problem",
        "run_independent_steps",
        "search_vertical",
        "share_file",
        "wide_browse",
        "wide_research",
        "xlsx_repl",
    }
)

REQUESTER_FREE_GLOBAL_TOOLS = (
    frozenset(
        {
            "cancel_spawn",
            "edit",
            "fetch_url",
            "get_context_remaining",
            "glob",
            "grep",
            "load_skill",
            "message_spawn",
            "call_mcp_tool",
            "list_mcp_tools",
            "memory_update",
            "new_context",
            "object_explain",
            "read",
            "search_connector_tools",
            "search_history",
            "search_web",
            "update_todo_list",
            "update_todo_status",
            "write",
        }
    )
    | CONVERSATION_COMMON_GLOBAL_TOOLS
)

REQUESTER_ACTIONS = frozenset(
    {
        "action:agent:restore_application",
        "action:agent:set_homepage",
        "action:conversation:make_conversation_private",
        "action:conversation:read_private_transcript",
        "action:conversation:share_conversation",
        "action:credential:request_credentials",
        "action:member:add_member",
        "action:member:grant_web_access",
        "action:member:revoke_web_access",
        "action:page:rebuild_page_facts",
        "action:report:rebuild_report_digest",
        "action:site:deploy_website",
        "action:site:publish_website",
        "action:surface:imessage_connect",
        "action:surface:slack_connect",
        "action:workspace:manage_billing",
    }
)

REQUESTER_FREE_ACTIONS = frozenset(
    {
        "action:artifact:generate_image",
        "action:artifact:generate_video",
        "action:memory:record_correction",
        "action:memory:record_first_run",
        "action:site:build_website",
        "action:skill:skill_search",
        "action:surface:slack_app_manifest",
        "action:surface:slack_channels",
    }
)

REQUESTER_FREE_PROFILE_TOOLS = frozenset(
    {
        "computer",
        "find",
        "form_input",
        "get_page_text",
        "navigate",
        "read_page",
        "tabs_close",
        "tabs_context",
        "tabs_create",
        "upload_file",
        "wait_for_download",
    }
)

ALL_REQUESTER_GLOBAL_TOOLS = REQUESTER_GLOBAL_TOOLS | {"sample_connector_execute"}
ALL_REQUESTER_FREE_GLOBAL_TOOLS = REQUESTER_FREE_GLOBAL_TOOLS | {
    "sample_echo",
    "sample_note",
}
ALL_REQUESTER_ACTIONS = REQUESTER_ACTIONS | {
    "action:monitor:monitor",
    "action:sample_widget:polish",
}
ALL_REQUESTER_FREE_ACTIONS = REQUESTER_FREE_ACTIONS | frozenset(
    {
        "action:sample_widget:bless",
        "action:sample_widget:beseech",
        "action:sample_widget:divine",
        "action:sample_widget:engrave",
        "action:workspace:audit",
    }
)
CONDITIONAL_REQUESTER_ACTIONS = frozenset({"action:enrichment_profile:confirm_website"})

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


def test_every_hosted_tool_has_one_static_requester_classification() -> None:
    tools, _, verbs = _registry(HOSTED_PACK)
    member_tools = tuple(tool for tool in tools if not tool.profile_only)
    assert {tool.name for tool in member_tools if tool.binds_member_authority} == (
        REQUESTER_GLOBAL_TOOLS
    )
    assert {tool.name for tool in member_tools if not tool.binds_member_authority} == (
        REQUESTER_FREE_GLOBAL_TOOLS
    )
    for tool in member_tools:
        schema = tool.schema().input_schema
        assert ("requested_by" in schema["properties"]) == (tool.name in REQUESTER_GLOBAL_TOOLS)
        assert "requested_by" not in schema.get("required", ())
    profile_tools = tuple(tool for tool in tools if tool.profile_only)
    assert {tool.name for tool in profile_tools} == REQUESTER_FREE_PROFILE_TOOLS
    assert all(not tool.binds_member_authority for tool in profile_tools)
    assert {tool.name for tool in member_tools if tool.standing_authorization is not None} == {
        "call_external_tool"
    }

    actions = tuple(bound.action for held in verbs.actions.values() for bound in held.values())
    assert {action.canonical_id for action in actions if not action.profile_only} == (
        REQUESTER_ACTIONS | REQUESTER_FREE_ACTIONS
    )
    assert {
        action.canonical_id
        for action in actions
        if not action.profile_only and action.binds_member_authority
    } == REQUESTER_ACTIONS
    assert {
        action.canonical_id
        for action in actions
        if not action.profile_only and not action.binds_member_authority
    } == REQUESTER_FREE_ACTIONS
    for action in actions:
        schema = action.schema().input_schema
        assert ("requested_by" in schema["properties"]) == (
            action.canonical_id in REQUESTER_ACTIONS
        )
        assert "requested_by" not in schema.get("required", ())
    assert {
        action.canonical_id
        for action in actions
        if action.profile_only and not action.binds_member_authority
    } == {"action:notification:deliver"}


def test_conversation_common_tools_never_offer_a_requester_field() -> None:
    tools, _, _ = _registry(None)
    common = tuple(tool for tool in tools if tool.name in CONVERSATION_COMMON_GLOBAL_TOOLS)
    assert {tool.name for tool in common} == CONVERSATION_COMMON_GLOBAL_TOOLS
    assert all(not tool.binds_member_authority for tool in common)
    assert all("requested_by" not in tool.schema().input_schema["properties"] for tool in common)


def test_every_shipped_tool_is_named_in_the_exact_requester_inventory() -> None:
    tools, _, verbs = _registry(None)
    classifications: dict[str, set[bool]] = {}
    for tool in tools:
        classifications.setdefault(tool.name, set()).add(tool.binds_member_authority)
    assert all(len(values) == 1 for values in classifications.values())
    assert {
        name for name, values in classifications.items() if values == {True}
    } == ALL_REQUESTER_GLOBAL_TOOLS
    assert {
        name for name, values in classifications.items() if values == {False}
    } == ALL_REQUESTER_FREE_GLOBAL_TOOLS | REQUESTER_FREE_PROFILE_TOOLS

    actions = tuple(bound.action for held in verbs.actions.values() for bound in held.values())
    assert {
        action.canonical_id for action in actions if action.binds_member_authority
    } == ALL_REQUESTER_ACTIONS
    assert {
        action.canonical_id
        for action in actions
        if not action.binds_member_authority and not action.profile_only
    } == ALL_REQUESTER_FREE_ACTIONS
    assert {
        action.canonical_id
        for action in actions
        if not action.binds_member_authority and action.profile_only
    } == {"action:notification:deliver", "action:sample_widget:calibrate"}


def test_conditional_actions_are_in_the_exact_requester_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UFO_ENRICHMENT_PROVIDER", raising=False)
    monkeypatch.setenv("PEOPLE_DATA_LABS_API_KEY", "test")
    _, _, verbs = _registry(None)
    actions = tuple(bound.action for held in verbs.actions.values() for bound in held.values())
    conditional = tuple(
        action for action in actions if action.canonical_id in CONDITIONAL_REQUESTER_ACTIONS
    )
    assert {action.canonical_id for action in conditional} == CONDITIONAL_REQUESTER_ACTIONS
    assert all(action.binds_member_authority for action in conditional)
    assert all(
        "requested_by" in action.schema().input_schema["properties"] for action in conditional
    )
    assert all(
        "requested_by" not in action.schema().input_schema.get("required", ())
        for action in conditional
    )


def test_every_candidate_registers_as_an_action_and_never_as_a_global_tool() -> None:
    tools, _, verbs = _registry(None)
    assert {tool.name for tool in tools}.isdisjoint(MOVED_CANDIDATES)
    registered = {bound.action.name for held in verbs.actions.values() for bound in held.values()}
    assert MOVED_CANDIDATES <= registered


def test_the_window_tools_reach_every_subagent() -> None:
    """The `<context_window>` section rides the subagent shell prompt too, so the tools it names
    must ride into every child as subagent defaults rather than as per-profile grants. This pack
    bundles both boundaries, so every window tool either one offers rides in: `compact` names
    `get_context_remaining` alone, and `rollover` adds the reset and the history search."""
    tools, _, _ = _registry(HOSTED_PACK)
    defaults = {tool.name for tool in tools if tool.subagent_default}
    assert {"get_context_remaining", "new_context", "search_history"} <= defaults
