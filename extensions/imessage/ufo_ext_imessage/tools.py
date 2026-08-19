import json
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from ufo.sdk.surfaces import SurfaceInstallationConflict
from ufo.sdk.tools import TextContent, ToolContext, ToolResult
from ufo_ext_imessage.provider import MessageProvider, ProviderNotConfigured
from ufo_ext_imessage.surface import (
    OPT_IN_CODE_ALPHABET,
    OPT_IN_CODE_LENGTH,
    OPT_IN_TEXT,
    SURFACE_IMESSAGE,
    PendingClaim,
    phone_key,
    read_claim,
)

E164_PATTERN = re.compile(r"^\+[1-9][0-9]{7,14}$")
PHONE_CLAIM_MINUTES = 30
PHONE_CLAIM_TTL = timedelta(minutes=PHONE_CLAIM_MINUTES)


def opt_in_link(assigned_phone_number: str, opt_in_code: str) -> str:
    """The deeplink that opens Messages with the opt-in text ready, so the member sends the first
    message a shared line needs before it can message that phone. `sms:` is the only scheme Apple
    documents, and the `?&body=` form is the one Photon's own redirect ships."""
    return f"sms:{assigned_phone_number}?&body={quote(f'{OPT_IN_TEXT} {opt_in_code}')}"


class ImessageConnectInput(BaseModel):
    phone_number: str = Field(
        description="The member's iMessage phone number in E.164 form, such as +14155550123."
    )
    user_description: str = Field(
        description="That you are connecting their phone to iMessage, in plain language."
    )

    @field_validator("phone_number")
    @classmethod
    def _e164(cls, value: str) -> str:
        phone = "".join(value.split())
        if E164_PATTERN.fullmatch(phone) is None:
            raise ValueError("phone_number must use E.164 form")
        return phone


def _result(state: str, instruction: str, **extra: object) -> ToolResult:
    return ToolResult(
        content=(
            TextContent(text=json.dumps({"state": state, "instruction": instruction, **extra})),
        ),
        untrusted=True,
    )


def _opt_in_result(assigned_phone_number: str, opt_in_code: str) -> ToolResult:
    opt_in_text = f"{OPT_IN_TEXT} {opt_in_code}"
    return _result(
        "pending",
        f'Text "{opt_in_text}" to {assigned_phone_number} from that phone within '
        f"{PHONE_CLAIM_MINUTES} minutes. Case, spaces and punctuation do not matter.",
        assigned_phone_number=assigned_phone_number,
        opt_in_text=opt_in_text,
        opt_in_link=opt_in_link(assigned_phone_number, opt_in_code),
    )


@dataclass(frozen=True)
class ImessageConnect:
    provider: Callable[[], MessageProvider]

    async def run(self, ctx: ToolContext, args: ImessageConnectInput) -> ToolResult:
        """Bind this provider installation and stage one member identity claim."""
        assert ctx.ext is not None
        if ctx.speaker_member_id is None:
            return _result(
                "not_connected", "A signed-in workspace member must request this connection."
            )
        try:
            provider = self.provider()
        except ProviderNotConfigured:
            return _result("not_connected", "This deploy has no iMessage provider credentials.")
        configured = await ctx.ext.installations.installation(SURFACE_IMESSAGE)
        if configured != provider.installation_id:
            if not await ctx.speaker_is_admin():
                return _result(
                    "not_connected", "Ask a workspace admin to connect the iMessage provider."
                )
            try:
                await ctx.ext.installations.bind(SURFACE_IMESSAGE, provider.installation_id)
            except SurfaceInstallationConflict:
                return _result(
                    "not_connected", "This iMessage provider is connected to another workspace."
                )
        linked = await ctx.ext.installations.linked_member(SURFACE_IMESSAGE, args.phone_number)
        if linked is not None and linked != ctx.speaker_member_id:
            return _result("not_connected", "That phone belongs to another workspace member.")
        key = phone_key(args.phone_number)
        stored = await ctx.ext.store.get(key)
        claim = read_claim(stored)
        if claim is not None and claim.expires_at <= datetime.now(UTC):
            claim = None
        if claim is not None and claim.member_id != ctx.speaker_member_id:
            return _result("not_connected", "Another workspace member is connecting that phone.")
        line_idempotency_key = f"imessage-line:{ctx.idempotency_key or uuid4()}"
        if linked is not None:
            assigned_phone_number = await provider.assign_line(
                args.phone_number, line_idempotency_key
            )
            return _result(
                "connected",
                f"That phone is connected. Text {assigned_phone_number} from it.",
                assigned_phone_number=assigned_phone_number,
            )
        if claim is not None:
            return _opt_in_result(claim.assigned_phone_number, claim.opt_in_code)
        assigned_phone_number = await provider.assign_line(args.phone_number, line_idempotency_key)
        opt_in_code = "".join(
            secrets.choice(OPT_IN_CODE_ALPHABET) for _ in range(OPT_IN_CODE_LENGTH)
        )
        landed = await ctx.ext.store.put_if(
            key,
            PendingClaim(
                member_id=ctx.speaker_member_id,
                phone_number=args.phone_number,
                assigned_phone_number=assigned_phone_number,
                opt_in_code=opt_in_code,
                expires_at=datetime.now(UTC) + PHONE_CLAIM_TTL,
            ).model_dump(mode="json"),
            expected=stored,
        )
        if not landed:
            return _result("not_connected", "The phone connection changed. Ask again.")
        return _opt_in_result(assigned_phone_number, opt_in_code)
