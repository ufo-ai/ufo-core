"""The portal's panel contract: prepared intents and the settings projection.

A panel form submits a structured intent; the surface admits it as a turn on the member's one
durable intent conversation with the selected agent, the engine dispatches the named object verb
verbatim (no model round — the submitted values apply exactly or the kind's refusal returns, never
a paraphrase), and the handler waits for the terminal frame so the submit answers synchronously
with the typed outcome. Intent admission never folds into a live turn, so concurrent submits queue
as whole turns and the per-conversation partition runs a member's intents one at a time in order.
The turn is the audit record: speaker, envelope, and result all live on it. No bespoke mutation
endpoint exists — the chat transport carries every write, and the settings read is a projection
like every other portal read."""

import asyncio
import json
import re
from collections.abc import Callable, Iterable
from typing import Literal, get_args
from uuid import UUID

import yaml
from pydantic import BaseModel, JsonValue, ValidationError, model_validator
from ufo_ext_imessage.tools import IMESSAGE_CONNECT_ACTION
from ufo_ext_slack.tools import SLACK_CONNECT_ACTION

from ufo.sdk.audience import conversation_audience
from ufo.sdk.flags import flag_enabled
from ufo.sdk.http import JSONResponse, Request, Response
from ufo.sdk.hub import Parked, Terminal
from ufo.sdk.objects import (
    AGENT_ICONS,
    CONVERSATION_KIND,
    SURFACE_KIND,
    WORKSPACE_KIND,
    AgentSpec,
    TablerIcon,
)
from ufo.sdk.surfaces import SurfaceContext, TerminalFrame, ToolIntent
from ufo.sdk.tools import ActionBinding
from ufo_ext_web.audience import (
    MAKE_CONVERSATION_PRIVATE,
    SHARE_CONVERSATION,
    granted_emails,
    web_extension,
)

INTENT_MAX_BYTES = 65_536
INTENT_RESULT_TIMEOUT_SECONDS = 120
ERROR_CLASS_PREFIX = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*: ")
DELETE_ONLY_KINDS = frozenset({"credential"})
APPLY_ONLY_KINDS = frozenset({"member_profile"})
CONNECT_KINDS = frozenset({"connection"})
AGENT_SPEC_REQUIRED = frozenset({"model", "internet_access_allowed", "reasoning"})
DEEPSEEK_FLASH_MODEL = "deepseek/deepseek-v4.1-flash"
DEEPSEEK_FLASH_FLAG = "enable-deepseek-v4-1-flash"
FLAGGED_MODELS = {DEEPSEEK_FLASH_MODEL: DEEPSEEK_FLASH_FLAG}
STATED_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
SLACK_INSTALL_LINK_KEY = "authorize_url"
SLACK_INSTALL_HINT_KEY = "hint"
BILLING_PORTAL_LINK_KEY = "portal_url"
IMESSAGE_STATE_KEY = "state"
IMESSAGE_INSTRUCTION_KEY = "instruction"
IMESSAGE_LINK_KEY = "opt_in_link"
MANAGE_BILLING_ACTION = "manage_billing"
BILLING_OPERATION_KEY = "operation"
BILLING_PORTAL_OPERATION = "portal"
REPORT_KIND = "report"
REBUILD_REPORT_DIGEST_ACTION = "rebuild_report_digest"
PAGE_KIND = "page"
REBUILD_PAGE_FACTS_ACTION = "rebuild_page_facts"


class ApplyIntent(BaseModel):
    """One mutation of one object, by kind and name. `verb` and `kind` are the closed sets a panel
    form produces today — the Literals are the gate keeping this route from becoming a general
    object endpoint, and each object verb's outcome is the kind's own. `connect` is the one verb no
    kind gates: it names the provider and opens the same private OAuth handoff chat's
    `connect_account` leaves — the URL rides the turn's terminal and is minted per speaking member
    at stream time, never in a transcript or an intent response — so it pairs with the `connection`
    kind exactly. The connection itself is never created here and never renamed: its other verbs
    are `apply`, which sets who may use the account and what its streams read, and `delete`, the
    disconnect the connect screen's per-account remove submits. The kind's own gate holds both to
    the connection's owner or a workspace admin. The
    `credential` kind pairs the other way: a slot's value is a secret a private prompt collects, so
    only `delete` (clear) names it here, while `credential_slot` — the declaration a workspace
    writes for a provider no extension covers — takes the `apply` and the `delete` of the
    declaration itself. The `source_trigger` kind pairs a third way: a trigger IS
    the conversation it wakes, and the lane runs on the member's intent conversation, so the portal
    never creates one — its `apply` carries `paused`, the one field a standing trigger changes, and
    its `delete` ends it. The `mcp_server` kind takes `apply` — the endpoint alone, its token being
    a secret its own private prompt collects — and `delete`, which takes the server's endpoint and
    token with the row. A delete names its object and carries no spec."""

    verb: Literal["apply", "delete", "connect", "attach", "detach"]
    kind: Literal[
        "agent",
        "member",
        "member_profile",
        "scheduled_task",
        "skill",
        "connector_grant",
        "connection",
        "source",
        "source_trigger",
        "credential",
        "credential_slot",
        "mcp_server",
    ]
    name: str
    spec: dict[str, JsonValue] | None = None
    generation: str | None = None
    create_only: bool = False

    @classmethod
    def kinds(cls) -> frozenset[str]:
        """The kinds a panel may submit a mutation for, read off the closed Literal that admits
        them — so an object page offers a control exactly where the lane accepts one, and states
        the kind's own description in place of a dead control everywhere else."""
        return frozenset(get_args(cls.model_fields["kind"].annotation))

    @classmethod
    def applying_kinds(cls) -> frozenset[str]:
        """The kinds the lane takes an `apply` for."""
        return cls.kinds().difference(DELETE_ONLY_KINDS)

    @classmethod
    def deleting_kinds(cls) -> frozenset[str]:
        """The kinds the lane takes a `delete` for. A connection is the one kind whose delete is
        not its own creation undone: connecting goes through the provider, and the delete is the
        disconnect. A member profile is the one kind with no delete at all — it is emptied, and a
        member has one for as long as they are a member. Read off the same sets the validator
        refuses by, so the acts an object screen draws and the acts this lane admits cannot drift:
        a kind it only ever deletes draws a delete and no create."""
        return cls.kinds().difference(APPLY_ONLY_KINDS)

    @model_validator(mode="after")
    def _verb_pairs_with_its_kind(self) -> "ApplyIntent":
        if self.verb == "connect" and self.kind not in CONNECT_KINDS:
            raise ValueError("connect pairs with the connection kind exactly")
        if self.kind in CONNECT_KINDS and self.verb not in {"connect", "apply", "delete"}:
            raise ValueError("a connection is connected, edited or disconnected, never attached")
        if self.kind == "credential" and self.verb != "delete":
            raise ValueError(
                "a credential slot's value is set through its private prompt, never a spec"
            )
        if (
            self.kind == "source_trigger"
            and self.verb == "apply"
            and "paused" not in (self.spec or {})
        ):
            raise ValueError(
                "a source trigger is created from the conversation it wakes, never from a panel; "
                "a panel applies its paused state"
            )
        if self.verb == "delete" and self.spec is not None:
            raise ValueError("a delete intent carries no spec")
        if self.verb in {"attach", "detach"} and self.kind != "connector_grant":
            raise ValueError("attach and detach pair with the connector_grant kind exactly")
        if self.verb == "detach" and self.spec is not None:
            raise ValueError("a detach intent carries no spec")
        if self.kind == "member_profile" and self.verb != "apply":
            raise ValueError(
                "a member profile is named through its spec and pictured through its actions; "
                "it is never created or deleted"
            )
        if self.create_only and (self.verb != "apply" or self.kind not in CREATE_ONLY_KINDS):
            raise ValueError(
                "create_only pairs with applying "
                + " or ".join(sorted(CREATE_ONLY_KINDS))
                + " exactly"
            )
        return self


CREATE_ONLY_KINDS = frozenset({"agent", "skill"})
"""The kinds whose portal path creates a row rather than editing one, so a name already taken is a
refusal rather than a silent replacement of everything the row holds."""

ENVELOPE_FIELDS = frozenset({"kind", "action", "name", "agent", "generation", "input"})
"""The `object_action` envelope's own fields, which the action lane writes from its route and never
reads from a body — a body naming one is refused before a turn exists."""

FRAME_HEADER = "x-ufo-frame"
"""The header the portal shell sets on a call it forwards for an embedded app page. A page speaks
with the viewer's whole session, so the lane admits a marked post only for a callable whose
declaration says `frame`."""

NO_FRAME_ACCESS = "This action is not available from an app page."


class ProviderTile(BaseModel):
    """One tool the first run offers as a pick. `name` is the connector provider slug the portal
    draws the brand glyph by; `label` is what the member reads and what the memory states;
    `summary` says what the agent does once the account is connected, and `group` is the heading
    the catalog stands under. The catalog's own order is the order both pages draw, so the tiles
    of one group arrive together without either page sorting them."""

    name: str
    label: str
    summary: str
    group: str


FIRST_RUN_PROVIDERS = (
    ProviderTile(
        name="slack",
        label="Slack",
        summary="Answer in channels and direct messages.",
        group="Communication",
    ),
    ProviderTile(
        name="discord",
        label="Discord",
        summary="Answer in channels and direct messages.",
        group="Communication",
    ),
    ProviderTile(
        name="zoom",
        label="Zoom",
        summary="Read meetings, recordings, and transcripts.",
        group="Communication",
    ),
    ProviderTile(
        name="gmail",
        label="Gmail",
        summary="Search, read, and draft email.",
        group="Email and calendar",
    ),
    ProviderTile(
        name="googlecalendar",
        label="Google Calendar",
        summary="Search events and schedule meetings.",
        group="Email and calendar",
    ),
    ProviderTile(
        name="googledrive",
        label="Google Drive",
        summary="Search, read, and create files.",
        group="Files and documents",
    ),
    ProviderTile(
        name="googlesheets",
        label="Google Sheets",
        summary="Read and update spreadsheets.",
        group="Files and documents",
    ),
    ProviderTile(
        name="notion",
        label="Notion",
        summary="Search pages and update databases.",
        group="Files and documents",
    ),
    ProviderTile(
        name="airtable",
        label="Airtable",
        summary="Read and update bases.",
        group="Files and documents",
    ),
    ProviderTile(
        name="figma",
        label="Figma",
        summary="Read files, frames, and comments.",
        group="Files and documents",
    ),
    ProviderTile(
        name="github",
        label="GitHub",
        summary="Read repositories, open issues, and push changes.",
        group="Projects and code",
    ),
    ProviderTile(
        name="linear",
        label="Linear",
        summary="Read issues and update projects.",
        group="Projects and code",
    ),
    ProviderTile(
        name="jira",
        label="Jira",
        summary="Track issues and update boards.",
        group="Projects and code",
    ),
    ProviderTile(
        name="asana",
        label="Asana",
        summary="Read tasks and update projects.",
        group="Projects and code",
    ),
    ProviderTile(
        name="stripe",
        label="Stripe",
        summary="Read customers, payments, and invoices.",
        group="Customers and revenue",
    ),
    ProviderTile(
        name="intercom",
        label="Intercom",
        summary="Read conversations and draft replies.",
        group="Customers and revenue",
    ),
    ProviderTile(
        name="attio",
        label="Attio",
        summary="Read and update records.",
        group="Customers and revenue",
    ),
    ProviderTile(
        name="hubspot",
        label="HubSpot",
        summary="Read and update deals and contacts.",
        group="Customers and revenue",
    ),
    ProviderTile(
        name="salesforce",
        label="Salesforce",
        summary="Read and update accounts and opportunities.",
        group="Customers and revenue",
    ),
    ProviderTile(
        name="zendesk",
        label="Zendesk",
        summary="Read tickets and draft replies.",
        group="Customers and revenue",
    ),
    ProviderTile(
        name="quickbooks",
        label="QuickBooks",
        summary="Read invoices, expenses, and reports.",
        group="Customers and revenue",
    ),
    ProviderTile(
        name="google_search_console",
        label="Search Console",
        summary="Read search traffic, queries, and pages.",
        group="Customers and revenue",
    ),
)

FIRST_RUN_PROVIDER_NAMES = frozenset(tile.name for tile in FIRST_RUN_PROVIDERS)


class McpServerTile(BaseModel):
    """One MCP server this deploy offers by name. `url` is the server's own endpoint and `token`
    names the credential its vendor issues, so connecting one asks for that token alone and the
    member never types a URL. `name` is the name the value is filed under in the `mcp_servers`
    slot, which is also the name `list_mcp_tools` and `call_mcp_tool` take as `server`."""

    name: str
    label: str
    url: str
    token: str
    summary: str
    group: str


MCP_SERVERS = (
    McpServerTile(
        name="neon",
        label="Neon",
        url="https://mcp.neon.tech/mcp",
        token="Neon API key",
        summary="Read and change Postgres projects and branches.",
        group="Developer platforms",
    ),
    McpServerTile(
        name="render",
        label="Render",
        url="https://mcp.render.com/mcp",
        token="Render API key",
        summary="Read services, deploys, and logs.",
        group="Developer platforms",
    ),
)
"""A row earns its place by answering an `Authorization: Bearer` header and by reaching a provider
no broker already connects. Supabase and Hugging Face answer the header but Composio brokers both
over managed OAuth, which beats asking a member for a personal access token; a server authorizing
over OAuth issues no token to ask for at all."""

MCP_SERVER_NAMES = frozenset(tile.name for tile in MCP_SERVERS)

TRACKERS = ("linear", "jira", "asana")
SUPPORT = ("intercom", "zendesk")
CRM = ("hubspot", "salesforce", "attio")
DOCS = ("notion", "googledrive")
BOOKS = ("stripe", "quickbooks")


class Unlock(BaseModel):
    """One application the start screen can offer, and the accounts it takes to run.

    `needs` is a conjunction of alternatives: every group must be met, and any one name in a group
    meets that group — so two single-name groups read as `all of`, one many-name group as `any of`,
    and an empty `needs` is work that reaches nothing and is always ready. A row whose groups the
    member already holds is an application they can build now; a row short of one or two accounts
    is an unlock, offered as the accounts it would take. The names are the offered tiles, checked
    at import, because the row the portal draws is labelled from the tile's own label and a name no
    tile carries would draw no glyph and no words."""

    name: str
    mark: TablerIcon
    does: str
    needs: tuple[tuple[str, ...], ...] = ()

    @model_validator(mode="after")
    def _names_offered_tiles_and_a_drawn_mark(self) -> "Unlock":
        if self.mark not in AGENT_ICONS:
            raise ValueError(f"unlock {self.name!r} wears no drawn mark {self.mark!r}")
        for group in self.needs:
            if not group:
                raise ValueError(f"unlock {self.name!r} states an empty alternative")
            unoffered = sorted(set(group) - FIRST_RUN_PROVIDER_NAMES)
            if unoffered:
                raise ValueError(f"unlock {self.name!r} needs no tile named {unoffered[0]!r}")
        return self

    def missing(self, held: frozenset[str]) -> tuple[str, ...]:
        """The tiles still to connect before this row can run, in catalog order — empty where the
        member already holds every group. A group met by one held name costs nothing; a group met
        by none contributes its first name, the catalog's own preference for that class."""
        return tuple(group[0] for group in self.needs if not held.intersection(group))


class AppUnlock(Unlock):
    extension: str


PR_BABYSITTER = AppUnlock(
    name="pr-babysitter",
    mark="gnomon",
    does="Reports what each open pull request waits on: age, reviewer, checks.",
    needs=(("github",),),
    extension="app_code",
)
ISSUE_ASSIGNER = AppUnlock(
    name="issue-assigner",
    mark="deltoton",
    does="Routes each new issue to one owner, and states the evidence.",
    needs=(("github",),),
    extension="app_issues",
)
MEETING_TO_ISSUES = AppUnlock(
    name="meeting-to-issues",
    mark="denticulus",
    does="Files the next steps a meeting agreed, once they are confirmed.",
    needs=(("googlecalendar",),),
    extension="app_meetings",
)
DAY_AHEAD = AppUnlock(
    name="day-ahead",
    mark="akhet",
    does="Posts the day's meetings and what to read before each one.",
    needs=(("googlecalendar",),),
    extension="app_meetings",
)
APP_UNLOCKS = (PR_BABYSITTER, ISSUE_ASSIGNER, MEETING_TO_ISSUES, DAY_AHEAD)


UNLOCKS = (
    PR_BABYSITTER,
    Unlock(
        name="release-notes",
        mark="triglyph",
        does="Posts what shipped since the last release, in plain sentences.",
        needs=(("github",), ("slack",)),
    ),
    ISSUE_ASSIGNER,
    Unlock(
        name="doc-drift",
        mark="ostrakon",
        does="Flags the documents a merged change made wrong, quoting the line.",
        needs=(("github",), DOCS),
    ),
    Unlock(
        name="sprint-reporter",
        mark="stele",
        does="Reports what moved, what stalled, and what nobody owns.",
        needs=(TRACKERS,),
    ),
    Unlock(
        name="meeting-scribe",
        mark="lekythos",
        does="Writes a note per meeting — decided, owed, open — and files it.",
        needs=(("googlecalendar",), DOCS),
    ),
    MEETING_TO_ISSUES,
    DAY_AHEAD,
    Unlock(
        name="inbox-triage",
        mark="hydria",
        does="Groups unread mail by what it asks, and drafts the replies it needs.",
        needs=(("gmail",),),
    ),
    Unlock(
        name="invoice-chaser",
        mark="kylix",
        does="Drafts the chase for every invoice past its terms.",
        needs=(("gmail",), BOOKS),
    ),
    Unlock(
        name="runway-report",
        mark="omphalos",
        does="Reports cash, burn, and the months of runway left.",
        needs=(("stripe",), ("quickbooks",)),
    ),
    Unlock(
        name="payment-watch",
        mark="patera",
        does="Posts failed payments and cancellations as they land.",
        needs=(("stripe",), ("slack",)),
    ),
    Unlock(
        name="ticket-themes",
        mark="anthemion",
        does="Groups the week's tickets by underlying problem, and quotes each.",
        needs=(SUPPORT,),
    ),
    Unlock(
        name="support-to-issues",
        mark="nirah",
        does="Files the problems support keeps answering, one issue each.",
        needs=(SUPPORT, TRACKERS),
    ),
    Unlock(
        name="pipeline-reviewer",
        mark="carnyx",
        does="Names the deals gone quiet and what each one needs next.",
        needs=(CRM,),
    ),
    Unlock(
        name="account-brief",
        mark="adyton",
        does="Keeps a one-page brief per account current from the record.",
        needs=(CRM, DOCS),
    ),
    Unlock(
        name="thread-summarizer",
        mark="osculum",
        does="Writes a long channel or thread down to what was decided.",
        needs=(("slack",),),
    ),
    Unlock(
        name="search-performance",
        mark="aten",
        does="Reports the queries and pages that gained or lost search traffic.",
        needs=(("google_search_console",),),
    ),
    Unlock(
        name="funnel-report",
        mark="nochtli",
        does="Reports where signups came from and where they fell away.",
        needs=(("google_search_console",), ("googlesheets",)),
    ),
    Unlock(
        name="lead-followup",
        mark="ashnan",
        does="Drafts the reply each inbound lead is still owed.",
        needs=(("gmail",),),
    ),
    Unlock(
        name="announcement-writer",
        mark="thyrsus",
        does="Drafts the launch announcement and says where it should post.",
        needs=(("slack",), DOCS),
    ),
    Unlock(
        name="competitor-watch",
        mark="wedjat",
        does="Tracks the competitors you name, with a source for every claim.",
    ),
    Unlock(
        name="market-researcher",
        mark="nephele",
        does="Researches a market, a company, or a person on request.",
    ),
    Unlock(
        name="writing-desk",
        mark="kalyx",
        does="Drafts the recurring note: the update, the announcement, the post.",
    ),
)

UNLOCKS_BY_NAME = {unlock.name: unlock for unlock in UNLOCKS}
STARTER_APP_EXTENSIONS = frozenset(unlock.extension for unlock in APP_UNLOCKS)


class PanelIntent(BaseModel):
    """What a panel form submits on the intents lane: an object verb on the record panels' typed
    lane. A presented action rides its own lane, whose route names the target."""

    submitted: ApplyIntent


def _action_intent(
    kind: str, name: str | None, action: str, body: dict[str, JsonValue]
) -> ToolIntent:
    """The `object_action` call for one presented action: the target the route holds — kind, the
    row for an instance action — written first and in one order, the action's own body under
    `input`, and nothing the browser said about where it lands. The serialized intent is the turn's
    inbound and its audit record, compared byte for byte at admission."""
    call: dict[str, JsonValue] = {"kind": kind, "action": action}
    if name is not None:
        call["name"] = name
    call["input"] = body
    return ToolIntent(tool="object_action", input=call)


def _tool_intent(submitted: ApplyIntent) -> ToolIntent:
    """Every panel intent that is a tool call, as the call it dispatches to. Every intent here is
    one: a panel act that needs a model round is asked for in the composer, not admitted here."""
    match submitted:
        case ApplyIntent() if submitted.verb == "connect":
            return ToolIntent(
                tool="connect_account",
                input={
                    "provider": submitted.name,
                    "shared": bool((submitted.spec or {}).get("shared", False)),
                },
            )
        case ApplyIntent() if submitted.verb in {"delete", "detach"}:
            return ToolIntent(
                tool="object_delete",
                input={
                    "kind": submitted.kind,
                    "name": submitted.name,
                },
            )
        case ApplyIntent():
            envelope: dict[str, object] = {
                "kind": submitted.kind,
                "name": submitted.name,
                "spec": submitted.spec or {},
            }
            if submitted.generation is not None:
                envelope["generation"] = submitted.generation
            manifest = yaml.safe_dump(envelope, sort_keys=False, allow_unicode=True)
            return ToolIntent(
                tool="object_apply",
                input={
                    "manifest": manifest,
                    "create_only": submitted.create_only,
                },
            )


def _outcome(frame: TerminalFrame, turn_id: UUID) -> Response:
    if frame.status == "done":
        if frame.credential_request is not None:
            return JSONResponse(
                {
                    "applied": True,
                    "message": "",
                    "turn_id": str(turn_id),
                    "credentials": frame.credential_request.model_dump(mode="json"),
                }
            )
        return JSONResponse({"applied": True, "message": "Saved.", "turn_id": str(turn_id)})
    reason = frame.error_message or frame.text
    message = (
        ERROR_CLASS_PREFIX.sub("", reason, count=1) if reason else f"Not applied ({frame.status})."
    )
    return JSONResponse({"applied": False, "message": message, "turn_id": str(turn_id)})


def _slack_outcome(frame: TerminalFrame, turn_id: UUID) -> Response:
    """The install link the Slack step asked for, taken off the turn's own answer and read the way
    the action writes it: an install state whose `authorize_url` is the link and whose `hint` says
    why it minted none, arriving inside the wall the action's `untrusted` declaration renders every
    result in — the deploy's own `events_url` sits in that same object, so the key is read rather
    than the first address in the text. Where no link was minted the action's own words stand in
    its place: the workspace already holds the connector, or only an admin may install it."""
    if frame.status != "done":
        return _outcome(frame, turn_id)
    stated_state = STATED_JSON_OBJECT.search(frame.text)
    if stated_state is None:
        raise RuntimeError("slack_connect answered no install state")
    state = json.loads(stated_state.group())
    link = state.get(SLACK_INSTALL_LINK_KEY)
    stated = "" if link else state[SLACK_INSTALL_HINT_KEY]
    return JSONResponse({"applied": True, "message": stated, "url": link, "turn_id": str(turn_id)})


def _imessage_outcome(frame: TerminalFrame, turn_id: UUID) -> Response:
    if frame.status != "done":
        return _outcome(frame, turn_id)
    stated_state = STATED_JSON_OBJECT.search(frame.text)
    if stated_state is None:
        raise RuntimeError("imessage_connect answered no connection state")
    state = json.loads(stated_state.group())
    connection = state.get(IMESSAGE_STATE_KEY)
    instruction = state.get(IMESSAGE_INSTRUCTION_KEY)
    if not isinstance(connection, str) or not isinstance(instruction, str):
        raise RuntimeError("imessage_connect answered an invalid connection state")
    link = state.get(IMESSAGE_LINK_KEY)
    if link is not None and not isinstance(link, str):
        raise RuntimeError("imessage_connect answered an invalid Messages link")
    return JSONResponse(
        {
            "applied": connection in {"pending", "connected"},
            "message": instruction,
            "url": link,
            "turn_id": str(turn_id),
        }
    )


def _portal_outcome(frame: TerminalFrame, turn_id: UUID) -> Response:
    """The provider link `manage_billing` minted, taken off the turn's own answer. The tool states a
    JSON object and the result arrives inside the wall its `untrusted` declaration renders every
    result in, so the key is read rather than the first address in the text — the same shape the
    connect steps read their install link by. A refusal keeps the tool's words: only an admin may
    reach billing, and that sentence is the answer."""
    if frame.status != "done":
        return _outcome(frame, turn_id)
    stated = STATED_JSON_OBJECT.search(frame.text)
    if stated is None:
        raise RuntimeError("manage_billing answered no portal state")
    link = json.loads(stated.group()).get(BILLING_PORTAL_LINK_KEY)
    if not isinstance(link, str):
        raise RuntimeError("manage_billing answered no portal url")
    return JSONResponse({"applied": True, "message": "", "url": link, "turn_id": str(turn_id)})


def _worded_outcome(frame: TerminalFrame, turn_id: UUID) -> Response:
    """The tool's own words, for an act whose answer is what the member needs to read. A rebuild
    changes nothing the member can see when they press it — the job it marked work for writes the
    new text minutes later — so the answer has to say what was queued and what was left alone; a
    visibility change says who reads the conversation from now on. The tool that knows is what says
    it. A refusal keeps the tool's words the way every other outcome does."""
    if frame.status != "done":
        return _outcome(frame, turn_id)
    return JSONResponse({"applied": True, "message": frame.text, "turn_id": str(turn_id)})


ACTION_OUTCOMES: dict[tuple[str, str], Callable[[TerminalFrame, UUID], Response]] = {
    (SURFACE_KIND, SLACK_CONNECT_ACTION): _slack_outcome,
    (SURFACE_KIND, IMESSAGE_CONNECT_ACTION): _imessage_outcome,
    (REPORT_KIND, REBUILD_REPORT_DIGEST_ACTION): _worded_outcome,
    (PAGE_KIND, REBUILD_PAGE_FACTS_ACTION): _worded_outcome,
    (CONVERSATION_KIND, MAKE_CONVERSATION_PRIVATE): _worded_outcome,
    (CONVERSATION_KIND, SHARE_CONVERSATION): _worded_outcome,
}
"""The actions whose answer says more than done or refused, and how each is read: the install link
Slack mints, the connection state iMessage reports, the queued-work sentence a rebuild states, who
reads a conversation once its visibility moved. Every other action answers done, or the refusal in
its own words."""


def _action_outcome(
    kind: str, action: str, body: dict[str, JsonValue], frame: TerminalFrame, turn_id: UUID
) -> Response:
    if (kind, action) == (WORKSPACE_KIND, MANAGE_BILLING_ACTION):
        portal = body.get(BILLING_OPERATION_KEY) == BILLING_PORTAL_OPERATION
        return _portal_outcome(frame, turn_id) if portal else _outcome(frame, turn_id)
    read = ACTION_OUTCOMES.get((kind, action), _outcome)
    return read(frame, turn_id)


PORTAL_ROOM = "Portal actions"
"""What the portal's prepared-intent lane is called.

A room is named where it is opened, because nothing else will: a conversation with no title lists
its own first words, and an intent's first words are the serialized tool call the lane admitted — so
a member's list of conversations reads as a column of raw JSON.

It holds every act a member takes from the portal for this app, and the runs those acts armed: a
scheduled task reports into the conversation that created it, and the conversation that created it
is this one. Arming it anywhere else would be a second durable lane for one member and one app, and
two lanes are two partitions — a queued arm could then apply after the delete that followed it and
leave the task armed and firing."""

PORTAL_LANE_PREFIX = "intent/"


async def offered_models(models: Iterable[str], keep: str | None = None) -> list[str]:
    """The served model ids this workspace chooses an agent's model from: the registry's set, less
    a flagged model whose flag is off. The flag reads closed, so a deploy whose flag service holds
    no key, and one it cannot reach, offer the models they offered before the flagged one landed.
    `keep` is the model an agent already runs on, and it stays offered whatever its flag says: a
    flag that closes over a running app must not empty that app's model choice."""
    served = list(models)
    gated = {
        model: FLAGGED_MODELS[model]
        for model in served
        if model in FLAGGED_MODELS and model != keep
    }
    if not gated:
        return served
    answers = await asyncio.gather(*(flag_enabled(flag, default=False) for flag in gated.values()))
    withheld = {model for model, on in zip(gated, answers, strict=True) if not on}
    return [model for model in served if model not in withheld]


async def _complete_agent_spec(
    ctx: SurfaceContext,
    submitted: ApplyIntent,
    submitted_fields: frozenset[str],
    agent_id: UUID,
    member_id: UUID,
) -> ApplyIntent | Response:
    if (
        submitted.verb != "apply"
        or submitted.kind != "agent"
        or submitted.spec is None
        or AGENT_SPEC_REQUIRED <= submitted_fields
    ):
        return submitted
    detail = await ctx.agent_detail(agent_id, member_id)
    if detail is None or detail.name != submitted.name:
        return JSONResponse({"applied": False, "message": "No such app."})
    return submitted.model_copy(
        update={
            "spec": {
                "model": detail.model,
                "internet_access_allowed": detail.internet_access_allowed,
                "reasoning": detail.reasoning,
                "sandbox_size": detail.sandbox_size,
                **submitted.spec,
            }
        }
    )


async def _runs_on(ctx: SurfaceContext, agent_id: UUID, member_id: UUID, model: object) -> bool:
    if model not in ctx.models:
        return False
    detail = await ctx.agent_detail(agent_id, member_id)
    return detail is not None and detail.model == model


async def _intent_refusal(
    ctx: SurfaceContext,
    submitted: ApplyIntent,
    submitted_fields: frozenset[str],
    agent_id: UUID,
    member_id: UUID,
) -> Response | None:
    if submitted.kind == "agent" and submitted.spec:
        model = submitted.spec.get("model")
        if model not in await offered_models(ctx.models) and not await _runs_on(
            ctx, agent_id, member_id, model
        ):
            return JSONResponse({"applied": False, "message": f"No model named {model!r}."})
        if "sandbox_size" in submitted_fields and not ctx.sandbox_sizes:
            return JSONResponse(
                {"applied": False, "message": "This deploy does not offer sandbox sizes."}
            )
    if submitted.kind == "credential":
        slots = {view.name for view in await ctx.list_credential_slots()}
        if submitted.name not in slots:
            return JSONResponse(
                {"applied": False, "message": f"No credential slot named {submitted.name!r}."}
            )
    return None


async def _prepare_panel_intent(
    ctx: SurfaceContext, request: Request, agent_id: UUID, member_id: UUID
) -> ApplyIntent | Response:
    body = await request.body()
    if len(body) > INTENT_MAX_BYTES:
        return JSONResponse(
            {
                "applied": False,
                "message": f"Intent exceeds {INTENT_MAX_BYTES} bytes.",
            },
            status_code=413,
        )
    try:
        submitted = PanelIntent.model_validate({"submitted": json.loads(body)}).submitted
    except (ValidationError, ValueError):
        return JSONResponse({"error": "malformed intent"}, status_code=400)
    if (
        FRAME_HEADER in request.headers
        and submitted.verb == "connect"
        and not ctx.frame_admits("connect_account")
    ):
        return JSONResponse({"applied": False, "message": NO_FRAME_ACCESS})
    submitted_fields = frozenset(submitted.spec or {}) if submitted.kind == "agent" else frozenset()
    completed = await _complete_agent_spec(ctx, submitted, submitted_fields, agent_id, member_id)
    if isinstance(completed, Response):
        return completed
    refused = await _intent_refusal(ctx, completed, submitted_fields, agent_id, member_id)
    if refused is not None:
        return refused
    return completed


def _oversized_manifest(intent: ToolIntent) -> Response | None:
    if intent.tool != "object_apply":
        return None
    manifest = intent.input.get("manifest")
    if not isinstance(manifest, str):
        raise RuntimeError("an object apply intent has no manifest")
    if len(manifest.encode()) <= INTENT_MAX_BYTES:
        return None
    return JSONResponse(
        {
            "applied": False,
            "message": f"Intent exceeds {INTENT_MAX_BYTES} bytes.",
        },
        status_code=413,
    )


async def _intent_result(ctx: SurfaceContext, turn_id: UUID) -> Response:
    try:
        async with (
            ctx.tail(turn_id) as frames,
            asyncio.timeout(INTENT_RESULT_TIMEOUT_SECONDS),
        ):
            async for _cursor, frame in frames:
                match frame:
                    case Terminal():
                        return _outcome(frame.frame, turn_id)
                    case Parked():
                        return JSONResponse(
                            {
                                "applied": False,
                                "message": frame.message,
                                "turn_id": str(turn_id),
                            }
                        )
    except TimeoutError:
        return JSONResponse(
            {
                "applied": False,
                "message": "The change is still being applied — check back.",
                "turn_id": str(turn_id),
            },
            status_code=504,
        )
    raise RuntimeError("the turn's tail ended without a terminal frame")


async def submit_intent(
    ctx: SurfaceContext, request: Request, agent_id: UUID, member_id: UUID, email: str
) -> Response:
    """Admit one prepared intent for the selected agent and answer with its terminal outcome. The
    intent lands on the member's one durable intent conversation with this agent — never the chat
    conversation, so it cannot fold into a live chat turn — and a model the deploy's registry
    cannot serve refuses before a turn exists, because a stored unknown id would wedge the agent's
    every later turn at setup.

    A panel that writes one part of an agent — its prompt, its icon — submits that part alone, so
    the fields `AgentSpec` requires are read from the agent and merged beneath what was submitted.
    Without it the spec fails validation on fields the member was never shown, and the panel is
    told no model was named.

    An action intent is admitted only for an action this deploy presents on its kind — the rule
    the projection draws controls by — so a panel cannot prepare an act the portal never
    offered."""
    prepared = await _prepare_panel_intent(ctx, request, agent_id, member_id)
    if isinstance(prepared, Response):
        return prepared
    intent = _tool_intent(prepared)
    refused = _oversized_manifest(intent)
    if refused is not None:
        return refused
    conversation_id = await ctx.conversation_for(
        f"{PORTAL_LANE_PREFIX}{agent_id}/{email}",
        conversation_audience(member_id),
        agent_id=agent_id,
    )
    await ctx.retitle_conversation(conversation_id, PORTAL_ROOM)
    admitted = await ctx.admit(
        conversation_id,
        intent.model_dump_json(),
        speaker_member_id=member_id,
        intent=intent,
    )
    return await _intent_result(ctx, admitted.turn_id)


async def submit_action(
    ctx: SurfaceContext,
    request: Request,
    agent_id: UUID,
    member_id: UUID,
    email: str,
    *,
    kind: str,
    name: str | None,
    action: str,
) -> Response:
    """Admit one presented action for the selected agent and answer with its terminal outcome. The
    route holds the target — the kind, the row for an instance action, the lane's agent — and the
    body is the action's own input alone: a body naming an envelope field is refused before a turn
    exists, so nothing a browser posts can address a different target. The action must be one the
    deploy presents on that target, and a post the shell marks as an app page's must be one its
    declaration admits from a frame; the handler's own gate decides who may, and dispatch's
    instance recheck reads the row under the acting member."""
    raw = await request.body()
    if len(raw) > INTENT_MAX_BYTES:
        return JSONResponse(
            {"applied": False, "message": f"Intent exceeds {INTENT_MAX_BYTES} bytes."},
            status_code=413,
        )
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        return JSONResponse({"error": "malformed intent"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "malformed intent"}, status_code=400)
    named = sorted(ENVELOPE_FIELDS.intersection(body))
    if named:
        return JSONResponse(
            {
                "applied": False,
                "message": f"The body names {', '.join(named)}; the route binds the target.",
            }
        )
    binding: ActionBinding = "collection" if name is None else "instance"
    view = next(
        (entry for entry in ctx.object_actions(kind, binding, name=name) if entry.name == action),
        None,
    )
    if view is None:
        target = kind if name is None else f"{kind}/{name}"
        return JSONResponse(
            {"applied": False, "message": f"{target} has no portal action named {action!r}."}
        )
    if FRAME_HEADER in request.headers and not ctx.frame_admits(f"action:{kind}:{action}"):
        return JSONResponse({"applied": False, "message": NO_FRAME_ACCESS})
    intent = _action_intent(kind, name, action, body)
    conversation_id = await ctx.conversation_for(
        f"{PORTAL_LANE_PREFIX}{agent_id}/{email}",
        conversation_audience(member_id),
        agent_id=agent_id,
    )
    await ctx.retitle_conversation(conversation_id, PORTAL_ROOM)
    admitted = await ctx.admit(
        conversation_id,
        intent.model_dump_json(),
        speaker_member_id=member_id,
        intent=intent,
    )
    try:
        async with (
            ctx.tail(admitted.turn_id) as frames,
            asyncio.timeout(INTENT_RESULT_TIMEOUT_SECONDS),
        ):
            async for _cursor, frame in frames:
                match frame:
                    case Terminal():
                        return _action_outcome(kind, action, body, frame.frame, admitted.turn_id)
                    case Parked():
                        return JSONResponse(
                            {
                                "applied": False,
                                "message": frame.message,
                                "turn_id": str(admitted.turn_id),
                            }
                        )
    except TimeoutError:
        return JSONResponse(
            {
                "applied": False,
                "message": "The change is still being applied — check back.",
                "turn_id": str(admitted.turn_id),
            },
            status_code=504,
        )
    raise RuntimeError("the turn's tail ended without a terminal frame")


def _update_schema(sandbox_sizes: tuple[str, ...]) -> dict[str, JsonValue]:
    """The settings form's field source: the writable spec schema minus the fields the page renders
    itself. The settings page draws each in a control the schema cannot describe — a multiline
    editor for the prompt, a grid of drawn icons for the icon, the purpose as the line under the
    app's own name — and takes its current value from `spec`."""
    schema = AgentSpec.model_json_schema()
    hidden = {"input_schema", "output_schema", "prompt", "icon", "purpose"} | (
        set() if sandbox_sizes else {"sandbox_size"}
    )
    schema["properties"] = {
        key: value for key, value in schema["properties"].items() if key not in hidden
    }
    return schema


async def agent_settings(
    ctx: SurfaceContext, agent_id: UUID, member_id: UUID, *, admin: bool, archivable: bool
) -> Response:
    """The settings projection: the agent's configuration and prompt digest, the deploy's public
    internet capability as the ceiling the agent setting narrows, the model ids this workspace is
    offered for the model choice, the writable spec's own schema (the form renders its fields from
    it, never a parallel description), and — for an admin — the web audience this extension
    grants."""
    detail = await ctx.agent_detail(agent_id, member_id)
    if detail is None:
        return Response("no such agent", status_code=404)
    audience: list[str] | None = None
    if admin:
        grants = await granted_emails(web_extension().store)
        audience = list(grants.get(agent_id, ()))
    return JSONResponse(
        {
            "agent": {
                "name": detail.name,
                "main": detail.main,
                "prompt": detail.prompt,
                "prompt_digest": detail.prompt_digest,
                "surfaces": list(detail.surfaces),
                "archivable": archivable,
                "updated_at": detail.updated_at.isoformat(),
            },
            "deploy": {"sandbox_internet": ctx.deploy_sandbox_internet},
            "models": await offered_models(ctx.models, keep=detail.model),
            "spec": AgentSpec(
                model=detail.model,
                internet_access_allowed=detail.internet_access_allowed,
                use_workspace_skills=detail.use_workspace_skills,
                reasoning=detail.reasoning,
                sandbox_size=detail.sandbox_size,
                visibility=detail.visibility,
                icon=detail.icon,
            ).model_dump(
                mode="json",
                exclude={"prompt", "purpose"}
                if ctx.sandbox_sizes
                else {"prompt", "purpose", "sandbox_size"},
            ),
            "spec_schema": _update_schema(ctx.sandbox_sizes),
            "audience": audience,
        }
    )
