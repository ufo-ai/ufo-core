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
from ufo.sdk.objects import AgentSpec
from ufo.sdk.surfaces import CredentialSlotView, SurfaceContext, TerminalFrame, ToolIntent
from ufo_ext_web.audience import granted_emails, web_extension

INTENT_MAX_BYTES = 65_536
INTENT_RESULT_TIMEOUT_SECONDS = 120
ERROR_CLASS_PREFIX = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*: ")
DELETE_ONLY_KINDS = frozenset({"credential", "source_trigger"})
CONNECT_ONLY_KINDS = frozenset({"connection"})
AGENT_SPEC_REQUIRED = frozenset({"model", "internet_access_allowed", "reasoning"})


class ApplyIntent(BaseModel):
    """One mutation of one object, by kind and name. `verb` and `kind` are the closed sets a panel
    form produces today — the Literals are the gate keeping this route from becoming a general
    object endpoint, and each object verb's outcome is the kind's own. `connect` is the one verb no
    kind gates: it names the provider and opens the same private OAuth handoff chat's
    `connect_account` leaves — the URL rides the turn's terminal and is minted per speaking member
    at stream time, never in a transcript or an intent response — so it pairs with the `connection`
    kind exactly, both ways. The `credential` kind pairs the other way: a slot's value is a secret
    a private prompt collects, so only `delete` (clear) names it here. The `source_trigger` kind
    pairs that way too: a trigger IS the conversation it wakes, and the lane runs on the member's
    intent conversation, so the portal can only ever end one. A delete names its object and carries
    no spec."""

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
        return cls.kinds().difference(DELETE_ONLY_KINDS | CONNECT_ONLY_KINDS)

    @classmethod
    def deleting_kinds(cls) -> frozenset[str]:
        """The kinds the lane takes a `delete` for. Read off the same two sets the validator
        refuses by, so the acts an object screen draws and the acts this lane admits cannot drift:
        a kind it only ever deletes draws a delete and no create, and one it only ever connects
        draws neither."""
        return cls.kinds().difference(CONNECT_ONLY_KINDS)

    @model_validator(mode="after")
    def _verb_pairs_with_its_kind(self) -> "ApplyIntent":
        if (self.verb == "connect") != (self.kind in CONNECT_ONLY_KINDS):
            raise ValueError("connect pairs with the connection kind exactly")
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

    This is the one mutation that screen carries, and it is here rather than only in chat because
    the workspace that most needs it is the one whose balance refuses every turn. Both figures
    together arrange a refill and neither stops it, which is the shape `manage_billing` action
    'autopay' already takes — the intent dispatches verbatim to that same verb, so the tool's own
    admin gate and its refusals answer, and nothing about who may do this is decided twice."""

    verb: Literal["refill"]
    kind: Literal["billing"]
    amount_dollars: int | None = None
    below_dollars: int | None = None


class CorrectionIntent(BaseModel):
    """One memory correction from the workspace memory view: a corrective memory recorded through
    `memory_update`, exactly the write chat performs — a new item under the correcting member's own
    audience, naming the corrected item in `source_ref`. The named item is never edited or removed:
    the memory kind refuses apply and delete, and both statements stand until the dedup sweep
    retires a near-duplicate original toward the newest statement — the correction — leaving the row
    and its provenance in place."""

    verb: Literal["record"]
    kind: Literal["memory"]
    corrects: UUID
    body: str = Field(min_length=1)


class AddMemberIntent(BaseModel):
    """One member added from the team panel — the same admin-only `add_member` chat verb, which
    mints the member at whatever email domain their address carries and reports whether they took a
    seat. Changing an existing member's role or seat is an apply on the member kind, never this."""

    verb: Literal["add_member"]
    email: str
    admin: bool = False


class PanelIntent(BaseModel):
    """What a panel form submits: the closed set of mutations a panel produces today."""

    submitted: (
        ApplyIntent
        | AddMemberIntent
        | AudienceIntent
        | CorrectionIntent
        | CredentialIntent
        | RefillIntent
        | TranscriptIntent
    ) = Field(discriminator="verb")


def _tool_intent(
    submitted: (
        ApplyIntent
        | AddMemberIntent
        | AudienceIntent
        | CorrectionIntent
        | CredentialIntent
        | RefillIntent
        | TranscriptIntent
    ),
    slot: CredentialSlotView | None,
) -> ToolIntent:
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
            manifest = yaml.safe_dump(
                {"kind": submitted.kind, "name": submitted.name, "spec": submitted.spec or {}},
                sort_keys=False,
                allow_unicode=True,
            )
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
        detail = await ctx.agent_detail(agent_id)
        if detail is None or detail.name != submitted.name:
            return JSONResponse({"applied": False, "message": "No such agent."})
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
    intent = _tool_intent(submitted, slot)
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
        f"intent/{agent_id}/{email}",
        conversation_audience(member_id),
        agent_id=agent_id,
    )
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


def agent_create_schema(sandbox_sizes: tuple[str, ...]) -> dict[str, JsonValue]:
    """The create form's field source: the writable spec, with `prompt` among the required
    fields. The kind takes the initial prompt at birth and refuses a create without one, so the
    form states the requirement exactly where the kind enforces it. The I/O contract fields stay
    with `object_apply`, where a raw schema is typed, not formed. `icon` is absent because the
    kind assigns one at birth, so the form never asks a member to pick before the agent exists.
    `sandbox_size` renders only where the deploy's carrier offers sizes — on a single-shape
    backend the field would change nothing a member can observe, so the form never states the
    choice."""
    schema = AgentSpec.model_json_schema()
    hidden = {"input_schema", "output_schema", "icon"} | (
        set() if sandbox_sizes else {"sandbox_size"}
    )
    schema["properties"] = {
        key: value for key, value in schema["properties"].items() if key not in hidden
    }
    return {**schema, "required": [*schema["required"], "prompt"]}


def _update_schema(sandbox_sizes: tuple[str, ...]) -> dict[str, JsonValue]:
    """The settings form's field source: the writable spec schema minus `prompt` and `icon`. The
    settings page renders each in a control the schema cannot describe — a multiline editor for
    the prompt, a grid of drawn icons for the icon — and takes its current value from `spec`."""
    schema = AgentSpec.model_json_schema()
    hidden = {"input_schema", "output_schema", "prompt", "icon"} | (
        set() if sandbox_sizes else {"sandbox_size"}
    )
    schema["properties"] = {
        key: value for key, value in schema["properties"].items() if key not in hidden
    }
    return schema


async def agent_settings(ctx: SurfaceContext, agent_id: UUID, *, admin: bool) -> Response:
    """The settings projection: the agent's configuration and prompt digest, the deploy's public
    internet capability as the ceiling the agent setting narrows, the deploy's model ids for the
    model choice, the writable spec's own schema (the form renders its fields from it, never a
    parallel description), what an extension-shipped agent still needs granted, and — for an admin —
    the web audience this extension grants."""
    detail = await ctx.agent_detail(agent_id)
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
                "updated_at": detail.updated_at.isoformat(),
                "setup": None if detail.setup is None else detail.setup.model_dump(mode="json"),
            },
            "deploy": {"sandbox_internet": ctx.deploy_sandbox_internet},
            "models": list(ctx.models),
            "spec": AgentSpec(
                model=detail.model,
                internet_access_allowed=detail.internet_access_allowed,
                reasoning=detail.reasoning,
                sandbox_size=detail.sandbox_size,
                visibility=detail.visibility,
                icon=detail.icon,
            ).model_dump(
                mode="json",
                exclude={"prompt"} if ctx.sandbox_sizes else {"prompt", "sandbox_size"},
            ),
            "spec_schema": _update_schema(ctx.sandbox_sizes),
            "audience": audience,
        }
    )
