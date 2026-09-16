"""The two jobs that turn events into messages.

`Reconciling` offers every event nobody has read yet to every sequence that measures from it, and
starts the ones that match. `SequenceRunner` sends what is due. They are separate jobs because they
are separate failure domains: a pass that cannot read the approved sequences must not hold back a
message already due.

Neither fires on what it wrote. The events they read were written by another job, a minute or a
week earlier."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ufo.sdk.context import ExtensionContext
from ufo.sdk.email import EmailRefused, EmailSends, EmailUnanswered
from ufo.sdk.o11y import log
from ufo_ext_lifecycle_email.enrollments import Enrollment, Enrollments
from ufo_ext_lifecycle_email.sends import Sends
from ufo_ext_lifecycle_email.sequences import SEQUENCES, Composed, Sequence, row_step


@dataclass(frozen=True)
class Reconciling:
    """Offer every event nobody has read yet to every sequence that measures from it — the ones in
    the tree and the ones an operator approved alike, so a producer never knows which is which.

    A deploy with no send seam raises rather than passing, as the runner does on the same absence.
    Reading an event marks it read and nothing ever unmarks one, so a pass that read the log while
    it could reach no sequence would consume every event in it for good."""

    ctx: ExtensionContext

    async def run(self) -> None:
        if self.ctx.email is None:
            raise RuntimeError("lifecycle_email needs the deploy's send seam and holds none")
        await Enrollments(ctx=self.ctx).reconcile(await sequences(self.ctx))


async def sequences(ctx: ExtensionContext) -> tuple[Sequence, ...]:
    """Every sequence this deploy may run: the ones a diff reviews, and the drip ones an operator
    has approved. A deploy with no send seam has neither, because it sends nothing.

    Both halves share one name space, so a row that takes a name the tree holds is refused here.
    Two sequences of one name measure from different events and hold different words, and every
    read of the set answers with one of them: the row would enroll a member on its own event and
    then send them the tree's copy. The tree keeps the name — a deploy put it there, and a diff
    reviewed it."""
    if ctx.email is None:
        return ()
    shipped = {sequence.name for sequence in SEQUENCES}
    rows: list[Sequence] = []
    for row in await ctx.email.sequences():
        if row["name"] in shipped:
            log("lifecycle_email.name_taken", sequence=row["name"])
            continue
        rows.append(
            Sequence(
                name=row["name"],
                event=row["event"],
                steps=tuple(row_step(step) for step in row["steps"]),
                row_id=row["id"],
            )
        )
    return SEQUENCES + tuple(rows)


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
        held = await sequences(self.ctx)
        for enrollment in await enrollments.claim_due(datetime.now(UTC)):
            await self._fire(email, enrollments, enrollment, held)

    async def _fire(
        self,
        email: EmailSends,
        enrollments: Enrollments,
        enrollment: Enrollment,
        held: tuple[Sequence, ...],
    ) -> None:
        """A sequence this pass cannot resolve is deferred before it is given up on: a rolling
        deploy runs an image that holds neither the tree sequences the newer one ships nor the
        steps they name, and ending the enrollment there would settle it unsent for good. Past the
        window a roll can account for, it is retired rather than skewed, and the enrollment ends."""
        sequence = next((found for found in held if found.identity == enrollment.sequence), None)
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
