"""The job that sends the steps that are due.

It is its own job because it is its own failure domain: a sweep that cannot read the rows it writes
events from must not hold back a message already due. It fires on the clock, never on what it
wrote — the enrollments it reads were created by another job, from events written by a third."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ufo.sdk.context import ExtensionContext
from ufo.sdk.email import EmailRefused, EmailSends, EmailUnanswered
from ufo.sdk.o11y import log
from ufo_ext_lifecycle_email.enrollments import Enrollment, Enrollments
from ufo_ext_lifecycle_email.sends import Sends
from ufo_ext_lifecycle_email.sequences import SEQUENCES, Composed

UNKNOWN_SEQUENCE_GRACE = timedelta(hours=1)
"""How long a step stays due, with no image able to resolve its sequence, before the enrollment is
read as orphaned rather than as a rolling deploy.

Measured from the instant the step fell due, never from when the row was written: an enrollment is
written days before its first step, so the age of the row says nothing about how long nobody has
been able to send it.

A roll is bounded — the migrate Job runs to completion, the outgoing pods serve until the new ones
are ready, and `graceful_shutdown_seconds` caps the drain at ten minutes. An hour is well past that,
so a skewed pass defers and a sequence genuinely torn out stops holding its enrollments live."""


@dataclass(frozen=True)
class SequenceRunner:
    """Fire the steps this workspace has due. One claimed enrollment at a time: compose the step,
    send it, and move the enrollment on — or end it, where the step found the reason for the
    message gone or the sequence has no step left."""

    ctx: ExtensionContext

    async def run(self) -> None:
        email = self.ctx.email
        if email is None:
            raise RuntimeError("lifecycle_email needs the deploy's send seam and holds none")
        enrollments = Enrollments(ctx=self.ctx)
        for enrollment in await enrollments.claim_due(datetime.now(UTC)):
            await self._fire(email, enrollments, enrollment)

    async def _fire(
        self, email: EmailSends, enrollments: Enrollments, enrollment: Enrollment
    ) -> None:
        sequence = next((held for held in SEQUENCES if held.name == enrollment.sequence), None)
        if sequence is None or enrollment.step >= len(sequence.steps):
            if datetime.now(UTC) - enrollment.next_due_at < UNKNOWN_SEQUENCE_GRACE:
                await enrollments.release(enrollment)
                return
            await enrollments.end(enrollment)
            return
        composed = await sequence.steps[enrollment.step].compose(self.ctx, enrollment.member_id)
        if composed is None:
            await enrollments.end(enrollment)
            log("lifecycle_email.exited", sequence=enrollment.sequence, step=str(enrollment.step))
            return
        await self._send(email, enrollment, composed)
        if enrollment.step + 1 >= len(sequence.steps):
            await enrollments.end(enrollment)
            return
        await enrollments.advance(
            enrollment, enrollment.occurred_at + sequence.steps[enrollment.step + 1].after
        )

    async def _send(self, email: EmailSends, enrollment: Enrollment, composed: Composed) -> None:
        """The enrollment and the step name the reason, so a claim that already exists is a step
        already attempted — a lapsed lease re-fires the enrollment and sends nothing twice."""
        sends = Sends(ctx=self.ctx)
        reason = f"{enrollment.id}:{enrollment.step}"
        if not await sends.claim(enrollment.member_id, composed.kind, reason):
            return
        try:
            message_id = await email.send(
                address=composed.address,
                kind=composed.kind,
                subject=composed.subject,
                body=composed.body,
                action_label=composed.action_label,
                action_url=composed.action_url,
            )
        except (EmailRefused, EmailUnanswered) as refusal:
            await sends.failed(enrollment.member_id, composed.kind, reason, refusal)
            return
        await sends.sent(enrollment.member_id, composed.kind, reason, message_id)
