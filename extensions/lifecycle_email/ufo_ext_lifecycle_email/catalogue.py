"""Every message this extension holds the words for, declared so an operator can read them.

The words live here because they are product copy: a diff reviews them and the deploy that changed
them ships them. The control plane cannot read them — it runs no Python — so this module is the
`messages` Manifest point, and control asks core for the running image's own declaration when an
operator opens the catalogue. There is nothing to keep in step: a message deleted from this module
is gone from the next boot's answer.

`fires` is the one thing no template shows: what has to become true for a member to get this. It is
written here, beside the copy it explains, so the two cannot drift."""

from ufo.sdk.email import PRODUCT_NEWS, TRANSACTIONAL
from ufo.sdk.manifest import MessageSpec
from ufo_ext_lifecycle_email import balance_notice, reconnect_notice, sequences

MESSAGES: tuple[MessageSpec, ...] = (
    MessageSpec(
        kind=balance_notice.BALANCE_EXHAUSTED,
        topic=TRANSACTIONAL,
        fires="The workspace has spent its credit. Each seated admin, once per grant spent.",
        subject=balance_notice.SUBJECT,
        body=balance_notice.BODY,
    ),
    MessageSpec(
        kind=reconnect_notice.ACCOUNT_PARKED,
        topic=TRANSACTIONAL,
        fires=(
            "A provider stopped answering a connected account. The member who owns it, or the "
            "seated admins where nobody owns it, once per break."
        ),
        subject=reconnect_notice.SUBJECT,
        body=reconnect_notice.BODY,
    ),
    MessageSpec(
        kind=sequences.INVITED_TEAMMATE_KIND,
        topic=PRODUCT_NEWS,
        fires=(
            f"{sequences.INVITED_TEAMMATE}, "
            f"{sequences.INVITED_TEAMMATE_AFTER.days} days after the invitation. Ends unsent once "
            "they have spoken or their seat is gone."
        ),
        subject=sequences.SUBJECT,
        body=sequences.BODY,
    ),
    MessageSpec(
        kind=sequences.CONNECT_SOMETHING_KIND,
        topic=PRODUCT_NEWS,
        fires=(
            f"{sequences.CONNECT_SOMETHING}, at once on the event, which is written "
            f"{sequences.CONNECT_NUDGE_AFTER.days} days after an invitation to a member who has "
            f"connected nothing and within {sequences.CONNECT_NUDGE_UNTIL.days} days of it."
        ),
        subject=sequences.CONNECT_SUBJECT,
        body=sequences.CONNECT_BODY,
    ),
)
