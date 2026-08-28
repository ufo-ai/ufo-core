import io
import json
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import uuid4

import segno
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ufo.sdk.surfaces import AddressClaimState
from ufo.sdk.tools import TextContent, ToolContext, ToolResult
from ufo_ext_imessage.provider import MessageProvider, ProviderNotConfigured
from ufo_ext_imessage.surface import (
    OPT_IN_CODE_ALPHABET,
    OPT_IN_CODE_LENGTH,
    OPT_IN_TEXT,
    SURFACE_IMESSAGE,
    PendingClaim,
    claim_key,
    read_claim,
)

US_E164_PATTERN = re.compile(r"^\+1([2-9][0-9]{2})([2-9][0-9]{2})([0-9]{4})$")
US_FORMATTING = re.compile(r"\D")
PHONE_CLAIM_MINUTES = 30
PHONE_CLAIM_TTL = timedelta(minutes=PHONE_CLAIM_MINUTES)
OPT_IN_QR_FILENAME = "opt-in.png"
OPT_IN_QR_CAPTION = "Scan with that phone to open the message."
OPT_IN_QR_SCALE = 10
OPT_IN_QR_BORDER = 2


def opt_in_link(assigned_phone_number: str, opt_in_code: str) -> str:
    """The deeplink that opens Messages with the opt-in text ready, so the member sends the first
    message a shared line needs before it can message that phone. `sms:` is the only scheme Apple
    documents, and the `?&body=` form is the one Photon's own redirect ships."""
    return f"sms:{assigned_phone_number}?&body={quote(f'{OPT_IN_TEXT} {opt_in_code}')}"


def opt_in_qr(assigned_phone_number: str, opt_in_code: str) -> bytes:
    """The same prefilled message as a QR, so a member reading this in a desktop chat points a
    phone camera at it instead of retyping the code. `SMSTO:` is the payload a phone camera opens
    Messages from; the `sms:` URI the link uses is not read as consistently."""
    image = io.BytesIO()
    segno.make(f"SMSTO:{assigned_phone_number}:{OPT_IN_TEXT} {opt_in_code}", error="m").save(
        image, kind="png", scale=OPT_IN_QR_SCALE, border=OPT_IN_QR_BORDER
    )
    return image.getvalue()


IMESSAGE_CONNECT_ACTION = "imessage_connect"


def _display_phone(phone_number: str) -> str:
    us = US_E164_PATTERN.fullmatch(phone_number)
    if us is None:
        return phone_number
    return f"({us.group(1)}) {us.group(2)}-{us.group(3)}"


class ImessageConnectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone_number: str = Field(
        description="The member's iMessage phone number in E.164 form, such as +14155550123."
    )

    @field_validator("phone_number")
    @classmethod
    def _e164(cls, value: str) -> str:
        printed = value.strip()
        if any(character.isalpha() for character in printed):
            raise ValueError("phone_number must be a 10-digit US number")
        digits = US_FORMATTING.sub("", printed)
        if "+" in printed and not digits.startswith("1"):
            raise ValueError("phone_number must be a 10-digit US number")
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        stated = f"+1{digits}"
        if US_E164_PATTERN.fullmatch(stated) is None:
            raise ValueError("phone_number must be a 10-digit US number")
        return stated


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
        f'Text "{opt_in_text}" to {_display_phone(assigned_phone_number)} from that phone within '
        f"{PHONE_CLAIM_MINUTES} minutes.",
        assigned_phone_number=assigned_phone_number,
        opt_in_text=opt_in_text,
        opt_in_link=opt_in_link(assigned_phone_number, opt_in_code),
    )


@dataclass(frozen=True)
class ImessageConnect:
    provider: Callable[[], MessageProvider]

    async def run(self, ctx: ToolContext, args: ImessageConnectInput) -> ToolResult:
        """Bind this deploy's provider to the workspace and stage one member's phone claim."""
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
            await ctx.ext.installations.bind(SURFACE_IMESSAGE, provider.installation_id)
        claimed = await ctx.ext.installations.reserve_address(
            SURFACE_IMESSAGE,
            args.phone_number,
            ctx.speaker_member_id,
            datetime.now(UTC) + PHONE_CLAIM_TTL,
        )
        if claimed is AddressClaimState.TAKEN:
            return _result("not_connected", "That phone belongs to another member.")
        line_idempotency_key = f"imessage-line:{ctx.idempotency_key or uuid4()}"
        if claimed is AddressClaimState.LINKED:
            assigned_phone_number = await provider.assign_line(
                args.phone_number, line_idempotency_key
            )
            return _result(
                "connected",
                f"That phone is connected. Text {_display_phone(assigned_phone_number)} from it.",
                assigned_phone_number=assigned_phone_number,
            )
        key = claim_key(ctx.speaker_member_id, args.phone_number)
        stored = await ctx.ext.store.get(key)
        claim = read_claim(stored)
        if claim is None:
            claim = PendingClaim(
                assigned_phone_number=await provider.assign_line(
                    args.phone_number, line_idempotency_key
                ),
                opt_in_code="".join(
                    secrets.choice(OPT_IN_CODE_ALPHABET) for _ in range(OPT_IN_CODE_LENGTH)
                ),
            )
            if not await ctx.ext.store.put_if(key, claim.model_dump(mode="json"), expected=stored):
                return _result("not_connected", "The phone connection changed. Ask again.")
        await ctx.share_artifact(
            OPT_IN_QR_FILENAME,
            opt_in_qr(claim.assigned_phone_number, claim.opt_in_code),
            OPT_IN_QR_CAPTION,
        )
        return _opt_in_result(claim.assigned_phone_number, claim.opt_in_code)
