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
from typing import Literal, get_args
from uuid import UUID

import yaml
from pydantic import BaseModel, Field, JsonValue, ValidationError, model_validator

from ufo.sdk.audience import conversation_audience
from ufo.sdk.http import JSONResponse, Request, Response
from ufo.sdk.hub import Parked, Terminal
from ufo.sdk.objects import AGENT_ICONS, AgentSpec, TablerIcon
from ufo.sdk.surfaces import CredentialSlotView, SurfaceContext, TerminalFrame, ToolIntent
from ufo_ext_web.audience import granted_emails, web_extension

INTENT_MAX_BYTES = 65_536
INTENT_RESULT_TIMEOUT_SECONDS = 120
ERROR_CLASS_PREFIX = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*: ")
DELETE_ONLY_KINDS = frozenset({"credential", "source_trigger"})
CONNECT_KINDS = frozenset({"connection"})
AGENT_SPEC_REQUIRED = frozenset({"model", "internet_access_allowed", "reasoning"})
INSTALL_LINK = re.compile(r"https://\S+")
STATED_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
SLACK_INSTALL_LINK_KEY = "authorize_url"
SLACK_INSTALL_HINT_KEY = "hint"
BILLING_PORTAL_LINK_KEY = "portal_url"


class ApplyIntent(BaseModel):
    """One mutation of one object, by kind and name. `verb` and `kind` are the closed sets a panel
    form produces today — the Literals are the gate keeping this route from becoming a general
    object endpoint, and each object verb's outcome is the kind's own. `connect` is the one verb no
    kind gates: it names the provider and opens the same private OAuth handoff chat's
    `connect_account` leaves — the URL rides the turn's terminal and is minted per speaking member
    at stream time, never in a transcript or an intent response — so it pairs with the `connection`
    kind exactly. That kind's other verb is `delete`, the disconnect the connect screen's
    per-account remove submits: an account is connected or disconnected and never edited, and the
    kind's own gate holds the disconnect to the connection's owner or a workspace admin. The
    `credential` kind pairs the other way: a slot's value is a secret a private prompt collects, so
    only `delete` (clear) names it here. The `source_trigger` kind pairs that way too: a trigger IS
    the conversation it wakes, and the lane runs on the member's intent conversation, so the portal
    can only ever end one. A delete names its object and carries no spec."""

    verb: Literal["apply", "delete", "connect", "attach", "detach"]
    kind: Literal[
        "agent",
        "member",
        "scheduled_task",
        "skill",
        "connector_grant",
        "connection",
        "source",
        "source_trigger",
        "credential",
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
        return cls.kinds().difference(DELETE_ONLY_KINDS | CONNECT_KINDS)

    @classmethod
    def deleting_kinds(cls) -> frozenset[str]:
        """The kinds the lane takes a `delete` for — every kind it names. A connection is the one
        kind whose delete is not its own creation undone: connecting goes through the provider, and
        the delete is the disconnect. Read off the same sets the validator refuses by, so the acts
        an object screen draws and the acts this lane admits cannot drift: a kind it only ever
        deletes draws a delete and no create."""
        return cls.kinds()

    @model_validator(mode="after")
    def _verb_pairs_with_its_kind(self) -> "ApplyIntent":
        if self.verb == "connect" and self.kind not in CONNECT_KINDS:
            raise ValueError("connect pairs with the connection kind exactly")
        if self.kind in CONNECT_KINDS and self.verb not in {"connect", "delete"}:
            raise ValueError("a connection is connected or disconnected, never edited")
        if self.kind == "credential" and self.verb != "delete":
            raise ValueError(
                "a credential slot's value is set through its private prompt, never a spec"
            )
        if self.kind == "source_trigger" and self.verb != "delete":
            raise ValueError(
                "a source trigger is created from the conversation it wakes, never from a panel"
            )
        if self.verb == "delete" and self.spec is not None:
            raise ValueError("a delete intent carries no spec")
        if self.verb in {"attach", "detach"} and self.kind != "connector_grant":
            raise ValueError("attach and detach pair with the connector_grant kind exactly")
        if self.verb == "detach" and self.spec is not None:
            raise ValueError("a detach intent carries no spec")
        if self.create_only and (self.verb != "apply" or self.kind != "agent"):
            raise ValueError("create_only pairs with applying the agent kind exactly")
        return self


class CredentialIntent(BaseModel):
    """One request for the private prompt that fills a member-fillable credential slot. The panel
    never carries the secret: this mints the same sealed `request_credentials` handoff a chat turn
    produces, the terminal frame returns it, and the value crosses only in the sealed fulfillment
    the prompt posts."""

    verb: Literal["request"]
    kind: Literal["credential"]
    name: str


class AudienceIntent(BaseModel):
    """One web-audience change for the intent's agent — the same admin-only chat verbs
    `grant_web_access`/`revoke_web_access`, prepared by the administration view and dispatched
    verbatim on the target agent's own intent lane."""

    verb: Literal["grant_web_access", "revoke_web_access"]
    email: str


class TranscriptIntent(BaseModel):
    """One admin's acknowledgement that another member's private conversation may hold private
    information, prepared by the conversations view and dispatched verbatim to
    `read_private_transcript` on the conversation's own agent. It is a granting act — the row it
    writes is what the content gate answers on — so it rides the lane rather than a route, and the
    turn is its audit record."""

    verb: Literal["read"]
    kind: Literal["transcript"]
    conversation_id: UUID


class RefillIntent(BaseModel):
    """An admin's standing authority to charge the card on file, given from the billing screen.

    It is here rather than only in chat because the workspace that most needs it is the one whose
    balance refuses every turn — the same reason `PaymentMethodIntent` sits beside it. Both figures
    together arrange a refill and neither stops it, which is the shape `manage_billing` action
    'autopay' already takes — the intent dispatches verbatim to that same verb, so the tool's own
    admin gate and its refusals answer, and nothing about who may do this is decided twice."""

    verb: Literal["refill"]
    kind: Literal["billing"]
    amount_dollars: int | None = None
    below_dollars: int | None = None


class PaymentMethodIntent(BaseModel):
    """The portal link that saves a card, asked for from the billing screen.

    A refill is an authority to charge a card, so it is refused until one is on file — and the only
    other way to put one there is a chat act, which the balance that needs the card refuses. The
    screen therefore carries the step before the refill as well: both dispatch to `manage_billing`,
    the one verb a spent balance still admits, so an admin whose workspace has stopped can reach a
    provider from a screen that still answers. The link is minted per submission and short-lived,
    which is why nothing about it is stored here."""

    verb: Literal["save_card"]
    kind: Literal["billing"]


class CorrectionIntent(BaseModel):
    """One memory correction from the workspace memory view: a corrective memory recorded through
    `memory_update`, exactly the write chat performs — a new item under the correcting member's own
    audience, naming the corrected item in `source_ref`. The named item is never edited or removed:
    the memory kind refuses apply and delete, and both statements stand until the dedup sweep
    retires a near-duplicate original toward the newest statement — the correction — leaving the row
    and its provenance in place.

    A correction is therefore a row the member writes, whatever shape the item it names has, and how
    long a row may run is the memory provider's answer rather than a number repeated here: the
    memory read carries `body_max_chars`, and the form holds the member to it — an item longer than
    that bound opens the form empty beside its current text, so what is collected is a statement
    written to the bound rather than a body the tool refuses. A second copy of the bound in this
    extension is a second answer that drifts the day the provider moves its own, and the tool stays
    the enforcer either way."""

    verb: Literal["record"]
    kind: Literal["memory"]
    corrects: UUID
    body: str = Field(min_length=1)


class DigestRebuildIntent(BaseModel):
    """The radar's rebuild, prepared by the feed and dispatched verbatim to `rebuild_report_digest`
    on the main agent's lane. The tool's own admin gate answers who may spend a workspace's balance
    writing its whole feed again, and the tool marks the reports due rather than writing anything —
    the digest job owns that text and drains the backlog on its own interval."""

    verb: Literal["rebuild_reports"]


class PageFactRebuildIntent(BaseModel):
    """The wiki's rebuild, prepared by the page and dispatched verbatim to `rebuild_page_facts`. It
    reaches exactly the rows a job can produce again — the facts derived from synced pages, which
    the pages themselves still hold. The consolidated Overview summaries re-form on the
    consolidation job's own terms as facts age into a cluster, and an item an app recorded in a
    conversation came from a turn that has ended, so neither is this intent's to redo; the page
    states both before the member presses it."""

    verb: Literal["rebuild_page_facts"]


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


UNLOCKS = (
    Unlock(
        name="pr-babysitter",
        mark="gnomon",
        does="Reports what each open pull request waits on: age, reviewer, checks.",
        needs=(("github",),),
    ),
    Unlock(
        name="release-notes",
        mark="triglyph",
        does="Posts what shipped since the last release, in plain sentences.",
        needs=(("github",), ("slack",)),
    ),
    Unlock(
        name="issue-assigner",
        mark="deltoton",
        does="Routes each new issue to one owner, and states the evidence.",
        needs=(("github",), TRACKERS),
    ),
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
    Unlock(
        name="meeting-to-issues",
        mark="denticulus",
        does="Files the next steps a meeting agreed, once they are confirmed.",
        needs=(("googlecalendar",), TRACKERS),
    ),
    Unlock(
        name="day-ahead",
        mark="akhet",
        does="Posts the day's meetings and what to read before each one.",
        needs=(("googlecalendar",), ("slack",)),
    ),
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


TOOLING_PREFIX = "My team uses "


def _tools_recorded(labels: tuple[str, ...], budget: int) -> str:
    """The first-run picks as one row of the member's wiki. A member may pick every tile and the
    tiles carry labels rather than slugs, so the sentence is built to the row it is drawn as: it
    names the tools that fit and counts the rest. A sentence cut at the ceiling instead loses
    whichever names fall past it and says nothing about how many there were."""
    for named in range(len(labels), 0, -1):
        rest = len(labels) - named
        tail = f", and {rest} more." if rest else "."
        body = TOOLING_PREFIX + ", ".join(labels[:named]) + tail
        if len(body) <= budget:
            return body
    return f"{TOOLING_PREFIX}{len(labels)} tools."


class ToolingIntent(BaseModel):
    """What the team already uses, picked on the first run and recorded through `memory_update` —
    exactly the write chat performs when a member says it, so the item lands under the picking
    member's own audience and every later turn recalls it. The picks name the offered tiles: a name
    outside the catalog is refused before a turn exists, because the body the memory carries is
    written from the catalog's own labels."""

    verb: Literal["record_tooling"]
    kind: Literal["memory"]
    providers: list[str] = Field(min_length=1, max_length=len(FIRST_RUN_PROVIDERS))

    @model_validator(mode="after")
    def _picks_are_offered(self) -> "ToolingIntent":
        unoffered = sorted(set(self.providers) - FIRST_RUN_PROVIDER_NAMES)
        if unoffered:
            raise ValueError(f"no provider tile named {unoffered[0]!r}")
        return self


class ConnectSlackIntent(BaseModel):
    """The first run's Slack step: the `slack_connect` chat verb, dispatched verbatim so the tool's
    own admin gate answers and nothing about who may install a workspace-wide bot is decided
    twice. The tool seals the install link inside the turn and states it in its answer, so the
    outcome carries that link back to the member who asked — `connect_account`'s per-member consent
    URL is the one minted at stream time instead, because it authorizes a member's own account
    rather than the workspace's."""

    verb: Literal["connect_slack"]


class ConnectGitHubIntent(BaseModel):
    """The first run's GitHub step: the `connect_github` chat verb on the same terms as the Slack
    step — dispatched verbatim, admin-gated by the tool, answered with the install link it sealed
    for this workspace."""

    verb: Literal["connect_github"]


class AddMemberIntent(BaseModel):
    """One member added from the team panel — the same admin-only `add_member` chat verb, which
    mints the member at whatever email domain their address carries and reports whether they took a
    seat. Changing an existing member's role or seat is an apply on the member kind, never this."""

    verb: Literal["add_member"]
    email: str
    admin: bool = False


class RestoreApplicationIntent(BaseModel):
    verb: Literal["restore_application"]
    app_id: UUID
    name: str


class PanelIntent(BaseModel):
    """What a panel form submits: the closed set of mutations a panel produces today."""

    submitted: (
        ApplyIntent
        | AddMemberIntent
        | AudienceIntent
        | ConnectGitHubIntent
        | ConnectSlackIntent
        | CorrectionIntent
        | CredentialIntent
        | DigestRebuildIntent
        | PageFactRebuildIntent
        | PaymentMethodIntent
        | RefillIntent
        | RestoreApplicationIntent
        | ToolingIntent
        | TranscriptIntent
    ) = Field(discriminator="verb")


def _tool_intent(
    submitted: (
        ApplyIntent
        | AddMemberIntent
        | AudienceIntent
        | ConnectGitHubIntent
        | ConnectSlackIntent
        | CorrectionIntent
        | CredentialIntent
        | DigestRebuildIntent
        | PageFactRebuildIntent
        | PaymentMethodIntent
        | RefillIntent
        | RestoreApplicationIntent
        | ToolingIntent
        | TranscriptIntent
    ),
    slot: CredentialSlotView | None,
    body_max_chars: int,
) -> ToolIntent:
    """Every panel intent that is a tool call, as the call it dispatches to. Every intent here is
    one: a panel act that needs a model round is asked for in the composer, not admitted here."""
    match submitted:
        case RefillIntent():
            return ToolIntent(
                tool="manage_billing",
                input={
                    "action": "autopay",
                    "autopay_dollars": submitted.amount_dollars,
                    "autopay_below_dollars": submitted.below_dollars,
                    "user_description": "Set automatic refills from the billing screen.",
                },
            )
        case PaymentMethodIntent():
            return ToolIntent(
                tool="manage_billing",
                input={
                    "action": "portal",
                    "user_description": "Open the billing portal from the billing screen.",
                },
            )
        case RestoreApplicationIntent():
            return ToolIntent(
                tool="restore_application",
                input={
                    "app_id": str(submitted.app_id),
                    "name": submitted.name,
                    "user_description": f"Restore the app {submitted.name} from the portal.",
                },
            )
        case TranscriptIntent():
            return ToolIntent(
                tool="read_private_transcript",
                input={
                    "conversation_id": str(submitted.conversation_id),
                    "user_description": "Open a private transcript from the portal.",
                },
            )
        case CredentialIntent():
            assert slot is not None
            return ToolIntent(
                tool="request_credentials",
                input={
                    "reason": (
                        f"{slot.extension} authenticates with this value; it is stored "
                        "encrypted and never shown again."
                    ),
                    "prompts": [{"slot": slot.slot, "prompt": slot.description or slot.slot}],
                    "user_description": f"Set credential {slot.slot} from the portal.",
                },
            )
        case CorrectionIntent():
            return ToolIntent(
                tool="memory_update",
                input={
                    "body": submitted.body,
                    "source_ref": f"corrects memory/{submitted.corrects}",
                    "user_description": "Correct a memory from the portal.",
                },
            )
        case DigestRebuildIntent():
            return ToolIntent(
                tool="rebuild_report_digest",
                input={"user_description": "Write the radar entries again from the portal."},
            )
        case PageFactRebuildIntent():
            return ToolIntent(
                tool="rebuild_page_facts",
                input={"user_description": "Write the wiki's page facts again from the portal."},
            )
        case ToolingIntent():
            picked = set(submitted.providers)
            labels = tuple(tile.label for tile in FIRST_RUN_PROVIDERS if tile.name in picked)
            return ToolIntent(
                tool="memory_update",
                input={
                    "body": _tools_recorded(labels, body_max_chars),
                    "source_ref": "first run",
                    "user_description": "Record what the team uses from the first run.",
                },
            )
        case ConnectSlackIntent():
            return ToolIntent(
                tool="slack_connect",
                input={"user_description": "Connect Slack from the first run."},
            )
        case ConnectGitHubIntent():
            return ToolIntent(
                tool="connect_github",
                input={"user_description": "Connect GitHub from the first run."},
            )
        case AddMemberIntent():
            return ToolIntent(
                tool="add_member",
                input={
                    "email": submitted.email,
                    "admin": submitted.admin,
                    "user_description": f"Add {submitted.email} to the workspace from the portal.",
                },
            )
        case AudienceIntent():
            return ToolIntent(
                tool=submitted.verb,
                input={
                    "email": submitted.email,
                    "user_description": (
                        f"{submitted.verb} for {submitted.email} from the portal."
                    ),
                },
            )
        case ApplyIntent() if submitted.verb == "connect":
            return ToolIntent(
                tool="connect_account",
                input={
                    "provider": submitted.name,
                    "shared": bool((submitted.spec or {}).get("shared", False)),
                    "user_description": f"Connect {submitted.name} from the portal.",
                },
            )
        case ApplyIntent() if submitted.verb in {"delete", "detach"}:
            return ToolIntent(
                tool="object_delete",
                input={
                    "kind": submitted.kind,
                    "name": submitted.name,
                    "user_description": (
                        f"Delete {submitted.kind} {submitted.name} from the portal."
                    ),
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
                    "user_description": (
                        f"Apply {submitted.kind} {submitted.name} from the portal."
                    ),
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


def _connect_outcome(
    submitted: ConnectSlackIntent | ConnectGitHubIntent, frame: TerminalFrame, turn_id: UUID
) -> Response:
    """The install link the connect step asked for, taken off the turn's own answer and read the
    way the answering tool writes it. Slack answers an install state: `authorize_url` is the link
    and `hint` says why it minted none, and the object arrives inside the wall its tool's
    `untrusted` declaration renders every result in — the deploy's own `events_url` sits in that
    same object, so the key is read rather than the first address in the text. GitHub answers a
    sentence carrying its link. Where no link was minted the tool's own words stand in its place:
    the workspace already holds the connector, or only an admin may install it."""
    if frame.status != "done":
        return _outcome(frame, turn_id)
    match submitted:
        case ConnectSlackIntent():
            stated_state = STATED_JSON_OBJECT.search(frame.text)
            if stated_state is None:
                raise RuntimeError("slack_connect answered no install state")
            state = json.loads(stated_state.group())
            link = state.get(SLACK_INSTALL_LINK_KEY)
            stated = "" if link else state[SLACK_INSTALL_HINT_KEY]
        case ConnectGitHubIntent():
            found = INSTALL_LINK.search(frame.text)
            link = found.group() if found else None
            stated = "" if link else frame.text
    return JSONResponse({"applied": True, "message": stated, "url": link, "turn_id": str(turn_id)})


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


def _rebuild_outcome(frame: TerminalFrame, turn_id: UUID) -> Response:
    """What a rebuild made due, in the tool's own words. A rebuild changes nothing the member can
    see when they press it — the job it marked work for writes the new text minutes later — so the
    answer has to say what was queued and what was left alone, and the tool that knows both is what
    says it. A refusal keeps the tool's words the way every other outcome does."""
    if frame.status != "done":
        return _outcome(frame, turn_id)
    return JSONResponse({"applied": True, "message": frame.text, "turn_id": str(turn_id)})


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
SPOKEN_ROOM_PREFIXES = ("homepage/",)
"""The portal rooms a member may speak in, by the key they are opened under.

A member speaks where the app answers, and the homepage room is where it does: the sweep builds the
app's page there, and the member reads what it did and says what the page should hold instead. It
takes their message safely because a build is an ordinary turn — a message folded onto a live one is
read by the rounds it is already running.

The prepared-intent lane takes none, and that is the whole of the rest of the rule. An intent turn
dispatches its one tool call and runs no model round, so it claims no arrivals: a message folded
onto a live one is a message no round ever reads, and the member waits for a reply that is not
coming. A member reads that room and speaks to the app in their own chat with it."""


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
    told no model was named."""
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
    submitted_fields = (
        frozenset(submitted.spec)
        if isinstance(submitted, ApplyIntent)
        and submitted.kind == "agent"
        and submitted.spec is not None
        else frozenset()
    )
    if (
        isinstance(submitted, ApplyIntent)
        and submitted.verb == "apply"
        and submitted.kind == "agent"
        and submitted.spec is not None
        and not AGENT_SPEC_REQUIRED <= submitted_fields
    ):
        detail = await ctx.agent_detail(agent_id, member_id)
        if detail is None or detail.name != submitted.name:
            return JSONResponse({"applied": False, "message": "No such app."})
        submitted = submitted.model_copy(
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
    if isinstance(submitted, ApplyIntent) and submitted.kind == "agent" and submitted.spec:
        model = submitted.spec.get("model")
        if model not in ctx.models:
            return JSONResponse({"applied": False, "message": f"No model named {model!r}."})
        if "sandbox_size" in submitted_fields and not ctx.sandbox_sizes:
            return JSONResponse(
                {"applied": False, "message": "This deploy does not offer sandbox sizes."}
            )
    slot: CredentialSlotView | None = None
    if isinstance(submitted, ApplyIntent | CredentialIntent) and submitted.kind == "credential":
        by_name = {view.name: view for view in await ctx.list_credential_slots()}
        slot = by_name.get(submitted.name)
        if slot is None:
            return JSONResponse(
                {"applied": False, "message": f"No credential slot named {submitted.name!r}."}
            )
    if isinstance(submitted, CorrectionIntent | ToolingIntent) and not ctx.memory_available:
        return JSONResponse({"applied": False, "message": "This deploy runs without memory."})
    body_max_chars = ctx.memory_body_max_chars if ctx.memory_available else 0
    intent = _tool_intent(submitted, slot, body_max_chars)
    if intent.tool == "object_apply":
        manifest = intent.input.get("manifest")
        if not isinstance(manifest, str):
            raise RuntimeError("an object apply intent has no manifest")
        if len(manifest.encode()) > INTENT_MAX_BYTES:
            return JSONResponse(
                {
                    "applied": False,
                    "message": f"Intent exceeds {INTENT_MAX_BYTES} bytes.",
                },
                status_code=413,
            )
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
                        if isinstance(submitted, ConnectSlackIntent | ConnectGitHubIntent):
                            return _connect_outcome(submitted, frame.frame, admitted.turn_id)
                        if isinstance(submitted, PaymentMethodIntent):
                            return _portal_outcome(frame.frame, admitted.turn_id)
                        if isinstance(submitted, DigestRebuildIntent | PageFactRebuildIntent):
                            return _rebuild_outcome(frame.frame, admitted.turn_id)
                        return _outcome(frame.frame, admitted.turn_id)
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
    internet capability as the ceiling the agent setting narrows, the deploy's model ids for the
    model choice, the writable spec's own schema (the form renders its fields from it, never a
    parallel description), what an extension-shipped agent still needs granted, and — for an admin —
    the web audience this extension grants."""
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
                "setup": None if detail.setup is None else detail.setup.model_dump(mode="json"),
            },
            "deploy": {"sandbox_internet": ctx.deploy_sandbox_internet},
            "models": list(ctx.models),
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
