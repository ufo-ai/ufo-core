"""Carry the Datadog feed key into the two slots this release reads it from.

Two shapes hold that key before this one. The release being replaced reads `datadog_api_key` and
`datadog_application_key`, the keyed connector's slots; the release before it held both secrets as
JSON in one slot named for the provider. This release reads `datadog_feed_api_key` and
`datadog_feed_application_key`, which the feed alone reads: the slots the keyed connector injects on
the sandbox wire carry the provider's plain names, and a carry into those would hand every sandbox
of the workspace a key its member filled for sync. A workspace that filled either shape has a live
feed hanging off it, and the registrar removes a keyed connection once none of its provider's slots
is filled, taking its streams, their synced pages and every grant on it by cascade. Without this the
roll deletes exactly that.

The keyed pair carries slot to slot as the sealed bytes it already is, so that path opens nothing.
Each JSON value is split into the slot that reads each of its fields, which does mean opening the
value. The keyed pair carries first, so a workspace holding both shapes keeps the key the outgoing
image reads and the JSON reaches only a slot the pair left empty. A slot the workspace holds is left
as it stands, so a workspace that filled this release's slots itself keeps the value it filled, and
the carry never overwrites a key a member set. A value that is not the JSON the release wrote is
skipped: the sync read it the same way, so a feed behind it was already failing every run.

Every row read here survives this revision. The migrate Job completes before the fleet rolls, and
the image being replaced reads its own two slots every minute to decide this same connection's fate
— dropping them here would have the outgoing pods remove the connection inside a minute, which is
the loss this revision exists to prevent. The revision that drops a slot comes after the release
that stops reading it.

Splitting the JSON row means opening the fleet Fernet, so `UFO_CREDENTIAL_KEY` is now on the migrate
container beside the serve and jobs Deployments that already carry it. A deploy that finds a JSON
row still to carry and no key raises rather than rolling past it: the alternative is the outgoing
image's next tick deleting the feed this revision protects."""

import json
import os
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from cryptography.fernet import Fernet, InvalidToken

revision: str = "20260909203000"
down_revision: str | None = "20260909195911"
branch_labels: str | None = None
depends_on: str | None = None

CREDENTIAL_KEY_ENV = "UFO_CREDENTIAL_KEY"
API_SLOT = "datadog_feed_api_key"
APPLICATION_SLOT = "datadog_feed_application_key"
JSON_SLOT = "datadog"
JSON_FIELDS = (("api_key", API_SLOT), ("application_key", APPLICATION_SLOT))
KEYED_SLOTS = (("datadog_api_key", API_SLOT), ("datadog_application_key", APPLICATION_SLOT))

credential = sa.table(
    "credential",
    sa.column("workspace_id", sa.Uuid()),
    sa.column("slot", sa.Text()),
    sa.column("ciphertext", sa.LargeBinary()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def _carry_into(
    bind: sa.Connection, workspace_id: UUID, slot: str, ciphertext: bytes, now: datetime
) -> None:
    bind.execute(
        sa.insert(credential).values(
            workspace_id=workspace_id,
            slot=slot,
            ciphertext=ciphertext,
            created_at=now,
            updated_at=now,
        )
    )


def _carry_the_keyed_pair(bind: sa.Connection, held: set[tuple[UUID, str]], now: datetime) -> None:
    targets = dict(KEYED_SLOTS)
    rows = bind.execute(
        sa.select(credential.c.workspace_id, credential.c.slot, credential.c.ciphertext).where(
            credential.c.slot.in_(list(targets))
        )
    ).all()
    for workspace_id, slot, ciphertext in rows:
        target = targets[slot]
        if (workspace_id, target) in held:
            continue
        _carry_into(bind, workspace_id, target, bytes(ciphertext), now)
        held.add((workspace_id, target))


def _split_the_json_row(bind: sa.Connection, held: set[tuple[UUID, str]], now: datetime) -> None:
    rows = bind.execute(
        sa.select(credential.c.workspace_id, credential.c.ciphertext).where(
            credential.c.slot == JSON_SLOT
        )
    ).all()
    carried = [
        (workspace_id, ciphertext)
        for workspace_id, ciphertext in rows
        if any((workspace_id, slot) not in held for _, slot in JSON_FIELDS)
    ]
    if not carried:
        return
    key = os.environ.get(CREDENTIAL_KEY_ENV)
    if not key:
        raise RuntimeError(
            f"{CREDENTIAL_KEY_ENV} is unset and a {JSON_SLOT!r} credential needs carrying into "
            "its own slots; a deploy that cannot read the secret must not roll past it"
        )
    fernet = Fernet(key)
    for workspace_id, ciphertext in carried:
        try:
            secrets = json.loads(fernet.decrypt(bytes(ciphertext)).decode())
        except (InvalidToken, UnicodeDecodeError, ValueError):
            continue
        if not isinstance(secrets, dict):
            continue
        for field, slot in JSON_FIELDS:
            value = secrets.get(field)
            if not isinstance(value, str) or not value or (workspace_id, slot) in held:
                continue
            _carry_into(bind, workspace_id, slot, fernet.encrypt(value.encode()), now)
            held.add((workspace_id, slot))


def upgrade() -> None:
    bind = op.get_bind()
    held = {
        (row.workspace_id, row.slot)
        for row in bind.execute(
            sa.select(credential.c.workspace_id, credential.c.slot).where(
                credential.c.slot.in_([API_SLOT, APPLICATION_SLOT])
            )
        ).all()
    }
    now = datetime.now(UTC)
    _carry_the_keyed_pair(bind, held, now)
    _split_the_json_row(bind, held, now)


def downgrade() -> None:
    pass
