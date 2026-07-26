"""The assistant pack plus Metronome, so the billing chain can be driven locally end to end.

Billing is a hosted capability: `assistant_hosted` bundles `metronome` alongside managed backends a
laptop has no use for (Turbopuffer, E2B, Slack, the Redis hub), and the plain `assistant` pack
leaves `metronome` out because a dev stack has no business shipping usage to a billing vendor. That
left the one member action with no local proof — hosted onboarding offers the owner `Set up billing`
and nothing in a local deploy could service it. This pack is the missing middle: the local
assistant bundle, plus billing.

Selecting it (`[pack] name = "assistant_billing"`, which `docker compose` renders from
`UFO_DEV_PACK`) turns on `manage_billing`, the `billing_activation` job, and the usage and seat
shippers — all of which then need real Metronome credentials, which is why this is opt-in and not
the default dev pack. Point it at a Metronome **sandbox** token and a Stripe **test-mode** key:
`docs/onboarding.md` has the recipe."""

import ufo_pack_assistant

from ufo.sdk.manifest import Pack

NAME = "assistant_billing"
VERSION = "0.1.0"
EXTENSIONS = (*ufo_pack_assistant.EXTENSIONS, "metronome")


def pack() -> Pack:
    return Pack(name=NAME, version=VERSION, extensions=EXTENSIONS)
