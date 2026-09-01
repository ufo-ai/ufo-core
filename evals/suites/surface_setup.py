"""Surface setup: connecting a chat surface is an action on that surface's own object.

Slack and iMessage setup live on core's `surface` kind — `slack_connect`, `slack_app_manifest`, and
`slack_channels` on `surface/slack`, `imessage_connect` on `surface/imessage` — so the trajectory a
case grades is the dispatched form: an `object_action` call naming the surface and the action. The
neighbors a member's words invite are the retained globals `connect_account`, which authorizes one
member's own broker account rather than installing the workspace's surface, and `object_apply`,
which the read-only kind refuses — plus, for a status question, the sibling actions that would
render a manifest or search channels when only the install state was asked for.

Every action has a cold case (no prior discovery in the transcript), a warm case, and the family
carries claimed neighbor negatives. A warm case seeds the discovery the model would otherwise make:
a prior member message and an undelivered round in which the agent already read the surface object
and saw the action listed on it, so the case message asks for the act and grades that the known
action is invoked without re-listing kinds or explaining the kind (a fresh read of the same object
stays legitimate before a side-effecting act). The negatives are the asks that sound like surface
setup and are not: a member's own Slack account is a connector matter, and a question about the
iMessage line is a read, not the side-effecting connect that would reserve an address.

Every brief here is authored (RFC 0042 sourcing rule): no production turn has invoked these actions
under the dispatched form yet, so each brief states the member's ask in their own words and names
no action. The eval deploy holds no Slack app env and no iMessage provider credentials, so each
handler answers its own refusal or install state; an attempt is what the trajectory graders read,
and whether the provider then answers is the extension's own integration proof.
"""

from json import dumps
from uuid import UUID

import sqlalchemy as sa
import yaml
from ufo_ext_imessage.manifest import manifest as imessage_manifest
from ufo_ext_imessage.surface import SURFACE_IMESSAGE
from ufo_ext_imessage.tools import IMESSAGE_CONNECT_ACTION
from ufo_ext_slack.manifest import NAME as SLACK_EXTENSION
from ufo_ext_slack.surface import (
    IDENTITY_BLOB_KEY,
    SLACK_BOT_TOKEN_SLOT,
    SLACK_SIGNING_SECRET_SLOT,
    SURFACE_SLACK,
    URL_VERIFIED_BLOB_KEY,
    URL_VERIFIED_STORE_KEY,
    SlackIdentity,
    bot_token_fingerprint,
    signing_secret_fingerprint,
    slack_installation_id,
)
from ufo_ext_slack.tools import (
    SLACK_APP_MANIFEST_ACTION,
    SLACK_CHANNELS_ACTION,
    SLACK_CONNECT_ACTION,
)
from ufo_ext_slack.tools import (
    TOOLS as SLACK_ACTIONS,
)

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    ToolInvocation,
    UndeliveredRound,
)
from evals.harness.scorers import attempted_tools_scorer, combine
from ufo.blob import WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.host.kinds.surface_kind import SURFACE_KIND
from ufo.runtime.ext.surface import SurfaceInstallationAccess
from ufo.runtime.workspace import ws_current
from ufo.schema import tables
from ufo.sdk.context import ScopedStore
from ufo.sdk.tools import ToolDef

OBJECT_ACTION = "object_action"
OBJECT_GET = "object_get"
NEIGHBORS = ("connect_account", "object_apply")
REDISCOVERY = ("object_list", "object_explain")
SURFACE_SETUP_PACKS = ("assistant_hosted",)
IMESSAGE_EXTENSION = imessage_manifest().name
IMESSAGE_ACTIONS = imessage_manifest().tools


def surface_action_scorer(surface: str, action: str) -> Grader:
    """`action` is attempted on exactly this surface. The target rides the envelope's `name`,
    outside the action's own arguments, so the match reads the dispatch record itself."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        if any(
            _is_surface_action(call, surface) and call.input.get("action") == action
            for call in output.calls
        ):
            return CapabilityVerdict(True, f"attempted {action} on surface/{surface}")
        return CapabilityVerdict(False, f"did not attempt {action} on surface/{surface}")

    return DescribedGrader(f"attempts {action} on surface/{surface}", grade)


def _is_surface_action(call: ToolInvocation, surface: str | None = None) -> bool:
    return (
        call.name == OBJECT_ACTION
        and call.input.get("kind") == SURFACE_KIND
        and (surface is None or call.input.get("name") == surface)
    )


def no_surface_action_scorer(surface: str, actions: tuple[str, ...] = ()) -> Grader:
    """No action on `surface` is attempted — or, given `actions`, none of those. The negative half
    of the family: an ask that sounds like surface setup and is not must leave the surface alone."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        attempted = sorted(
            {
                str(call.input.get("action"))
                for call in output.calls
                if _is_surface_action(call, surface)
                and (not actions or call.input.get("action") in actions)
            }
        )
        if attempted:
            return CapabilityVerdict(
                False, f"attempted surface action(s) on {surface}: {', '.join(attempted)}"
            )
        return CapabilityVerdict(True, f"attempted no surface action on {surface}")

    named = ", ".join(actions) if actions else "any action"
    return DescribedGrader(f"never attempts {named} on surface/{surface}", grade)


def status_read_scorer() -> Grader:
    """A question about Slack's install state is answered by reading it — the idempotent
    `slack_connect` on `surface/slack`, or the object itself — never by rendering a manifest or
    searching channels."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        read = any(
            (
                _is_surface_action(call, SURFACE_SLACK)
                and call.input.get("action") == SLACK_CONNECT_ACTION
            )
            or (
                call.name == OBJECT_GET
                and call.input.get("kind") == SURFACE_KIND
                and call.input.get("name") == SURFACE_SLACK
            )
            for call in output.calls
        )
        if not read:
            return CapabilityVerdict(False, "did not read the Slack install state")
        return CapabilityVerdict(True, "read the Slack install state")

    return DescribedGrader(
        f"attempts {SLACK_CONNECT_ACTION} on surface/{SURFACE_SLACK} or reads the object", grade
    )


def _surface_read(
    surface: str, extension: str, actions: tuple[ToolDef, ...], *, addressed: bool
) -> str:
    """The `object_get` envelope for an unbound surface, as the kind renders it, with the
    extension's actions listed — what a warm case's prior round already saw."""
    envelope: dict[str, object] = {
        "kind": SURFACE_KIND,
        "name": surface,
        "spec": {
            "surface": surface,
            "extension": extension,
            "addressed": addressed,
            "durable": True,
            "home": False,
        },
        "status": {"bound": False},
        "actions": [
            {
                "name": action.name,
                "description": action.description,
                "input_schema": action.input_model.model_json_schema(),
                "call": {"kind": SURFACE_KIND, "action": action.name, "name": surface, "input": {}},
            }
            for action in sorted(actions, key=lambda declared: declared.name)
        ],
        "links": [],
        "created_at": None,
        "updated_at": None,
    }
    return yaml.safe_dump(envelope, sort_keys=False)


SLACK_READ = _surface_read(SURFACE_SLACK, SLACK_EXTENSION, SLACK_ACTIONS, addressed=False)
IMESSAGE_READ = _surface_read(
    SURFACE_IMESSAGE, IMESSAGE_EXTENSION, IMESSAGE_ACTIONS, addressed=True
)


def _warm(
    surface: str, read: str, opening: str
) -> tuple[tuple[str, ...], tuple[UndeliveredRound, ...]]:
    return (
        (opening,),
        (
            UndeliveredRound(
                narration=f"Let me look at how the {surface} surface is set up here.",
                tool=OBJECT_GET,
                input={"kind": SURFACE_KIND, "name": surface},
                result=read,
            ),
        ),
    )


def _cold(surface: str, action: str) -> Grader:
    return combine(
        surface_action_scorer(surface, action),
        attempted_tools_scorer(required=(), forbidden=NEIGHBORS, orderings=()),
    )


def _known(surface: str, action: str) -> Grader:
    return combine(
        surface_action_scorer(surface, action),
        attempted_tools_scorer(required=(), forbidden=(*NEIGHBORS, *REDISCOVERY), orderings=()),
    )


SLACK_OPENING = "What's our Slack situation with you right now?"
IMESSAGE_OPENING = "Can I reach you over iMessage?"

SLACK_EVAL_TEAM_ID = "T0EVALSLACK"
SLACK_EVAL_BOT_USER_ID = "U0EVALSLACK"
SLACK_EVAL_BOT_TOKEN = "xoxb-eval-surface-setup"
SLACK_EVAL_SIGNING_SECRET = "eval-surface-setup-signing-secret"
SLACK_EVAL_FINGERPRINT = signing_secret_fingerprint(SLACK_EVAL_SIGNING_SECRET)
SLACK_EVAL_IDENTITY = SlackIdentity(
    bot_token_fingerprint=bot_token_fingerprint(SLACK_EVAL_BOT_TOKEN),
    team_id=SLACK_EVAL_TEAM_ID,
    bot_user_id=SLACK_EVAL_BOT_USER_ID,
)
SLACK_INSTALLATION_ID = slack_installation_id(SLACK_EVAL_TEAM_ID)
SLACK_EVAL_SLOTS = (SLACK_BOT_TOKEN_SLOT, SLACK_SIGNING_SECRET_SLOT)
SLACK_EVAL_BLOB_KEYS = (IDENTITY_BLOB_KEY, URL_VERIFIED_BLOB_KEY)


async def _refuse_a_slack_install_this_suite_did_not_make(blob: WorkspaceBlobStore) -> None:
    """Refuse a workspace holding Slack state that is not this suite's own, before anything is
    written or deleted.

    The seed and its cleanup remove the installation row, the bot token and signing secret, the
    identity and marker blobs, and the mirror. Against a workspace that really runs Slack that is an
    OAuth-minted token and a member-supplied secret destroyed, and an install that has to be made
    again. Every peer seed meeting this state refuses instead of writing through it, and this one
    now does too: state that is exactly ours is a rerun and proceeds, anything else stops the run.
    """
    workspace = ws_current()
    async with workspace_tx() as connection:
        installations = frozenset(
            (
                await connection.execute(
                    sa.select(tables.surface_installation.c.installation_id).where(
                        tables.surface_installation.c.workspace_id == workspace.workspace_id,
                        tables.surface_installation.c.surface == SURFACE_SLACK,
                    )
                )
            )
            .scalars()
            .all()
        )
        slots = frozenset(
            (
                await connection.execute(
                    sa.select(tables.credential.c.slot).where(
                        tables.credential.c.workspace_id == workspace.workspace_id,
                        tables.credential.c.slot.in_(SLACK_EVAL_SLOTS),
                    )
                )
            )
            .scalars()
            .all()
        )
    held = [await blob.exists(key) for key in SLACK_EVAL_BLOB_KEYS]
    if not (installations or slots or any(held)):
        return
    ours = installations in (frozenset(), frozenset((SLACK_INSTALLATION_ID,)))
    for slot, value in (
        (SLACK_BOT_TOKEN_SLOT, SLACK_EVAL_BOT_TOKEN),
        (SLACK_SIGNING_SECRET_SLOT, SLACK_EVAL_SIGNING_SECRET),
    ):
        if slot in slots and await workspace.credential(slot) != value:
            ours = False
    if not ours:
        raise RuntimeError("surface_setup requires a disposable workspace without Slack state")


async def _bind_slack_install(workspace_id: UUID, agent_id: UUID, blob: WorkspaceBlobStore) -> None:
    """The state a finished Slack install leaves: the surface installation, the credentials it runs
    on, the identity those proved, and the marker a signature-verified request stamps. That marker
    is what says Slack reaches this deploy — the row on its own is the `pending` install, which
    states no capability and is not what this case asks about — and it is written to both places
    the surface writes it, the blob `slack_connect` reads and the mirror the workspace fact reads.
    The bind is the extension's own writer, so the row the case runs against is the row a real
    install makes."""
    await _refuse_a_slack_install_this_suite_did_not_make(blob)
    await _drop_slack_install(workspace_id, agent_id, blob)
    await SurfaceInstallationAccess(declared=frozenset({SURFACE_SLACK})).bind(
        SURFACE_SLACK, SLACK_INSTALLATION_ID
    )
    workspace = ws_current()
    await workspace.put_credential(SLACK_BOT_TOKEN_SLOT, SLACK_EVAL_BOT_TOKEN)
    await workspace.put_credential(SLACK_SIGNING_SECRET_SLOT, SLACK_EVAL_SIGNING_SECRET)
    await blob.put(IDENTITY_BLOB_KEY, SLACK_EVAL_IDENTITY.model_dump_json().encode())
    await blob.put(
        URL_VERIFIED_BLOB_KEY, dumps({"fingerprint": SLACK_EVAL_FINGERPRINT, "at": 1.0}).encode()
    )
    await ScopedStore(extension=SLACK_EXTENSION).put(URL_VERIFIED_STORE_KEY, SLACK_EVAL_FINGERPRINT)


async def _drop_slack_install(
    _workspace_id: UUID, _agent_id: UUID, blob: WorkspaceBlobStore
) -> None:
    """Every other case in this suite asks an uninstalled workspace, so everything the seed wrote
    leaves with the case that made it — and only what it wrote: the same refusal guards the removal,
    since a cleanup that fires after a refused seed must not delete the state it refused to
    touch."""
    await _refuse_a_slack_install_this_suite_did_not_make(blob)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(tables.surface_installation).where(
                tables.surface_installation.c.workspace_id == ws_current().workspace_id,
                tables.surface_installation.c.surface == SURFACE_SLACK,
            )
        )
        await connection.execute(
            sa.delete(tables.credential).where(
                tables.credential.c.workspace_id == ws_current().workspace_id,
                tables.credential.c.slot.in_(SLACK_EVAL_SLOTS),
            )
        )
    for key in SLACK_EVAL_BLOB_KEYS:
        await blob.delete(key)
    await ScopedStore(extension=SLACK_EXTENSION).delete(URL_VERIFIED_STORE_KEY)


CASES = (
    CapabilityCase(
        "slack-workspace-install",
        "Set up Slack for the team so we can talk to you from our channels.",
        _cold(SURFACE_SLACK, SLACK_CONNECT_ACTION),
        digest_tag="surface-setup:slack-workspace-install:call",
    ),
    CapabilityCase(
        "slack-own-app-manifest",
        "We'd rather run you under our own Slack app than the shared one. What do I paste into "
        "Slack to create it?",
        _cold(SURFACE_SLACK, SLACK_APP_MANIFEST_ACTION),
        digest_tag="surface-setup:slack-own-app-manifest:call",
    ),
    CapabilityCase(
        "slack-channel-lookup",
        "Which of our Slack channels is the hiring one? I need its id.",
        _cold(SURFACE_SLACK, SLACK_CHANNELS_ACTION),
        digest_tag="surface-setup:slack-channel-lookup:call",
    ),
    CapabilityCase(
        "imessage-phone",
        "Hook up my iMessage so I can text you. My number is 415 555 0123.",
        _cold(SURFACE_IMESSAGE, IMESSAGE_CONNECT_ACTION),
        digest_tag="surface-setup:imessage-phone:call",
    ),
    CapabilityCase(
        "slack-workspace-install-known",
        "Go ahead and connect it for the whole team.",
        _known(SURFACE_SLACK, SLACK_CONNECT_ACTION),
        digest_tag="surface-setup:slack-workspace-install-known:call",
        prior_messages=_warm(SURFACE_SLACK, SLACK_READ, SLACK_OPENING)[0],
        undelivered=_warm(SURFACE_SLACK, SLACK_READ, SLACK_OPENING)[1],
    ),
    CapabilityCase(
        "slack-own-app-manifest-known",
        "We'll use our own Slack app. Give me what I paste into Slack to create it, bot name "
        '"Atlas".',
        _known(SURFACE_SLACK, SLACK_APP_MANIFEST_ACTION),
        digest_tag="surface-setup:slack-own-app-manifest-known:call",
        prior_messages=_warm(SURFACE_SLACK, SLACK_READ, SLACK_OPENING)[0],
        undelivered=_warm(SURFACE_SLACK, SLACK_READ, SLACK_OPENING)[1],
    ),
    CapabilityCase(
        "slack-channel-lookup-known",
        "Find the hiring channel and give me its id.",
        _known(SURFACE_SLACK, SLACK_CHANNELS_ACTION),
        digest_tag="surface-setup:slack-channel-lookup-known:call",
        prior_messages=_warm(SURFACE_SLACK, SLACK_READ, SLACK_OPENING)[0],
        undelivered=_warm(SURFACE_SLACK, SLACK_READ, SLACK_OPENING)[1],
    ),
    CapabilityCase(
        "imessage-phone-known",
        "Yes — set it up for my number, 415 555 0123.",
        _known(SURFACE_IMESSAGE, IMESSAGE_CONNECT_ACTION),
        digest_tag="surface-setup:imessage-phone-known:call",
        prior_messages=_warm(SURFACE_IMESSAGE, IMESSAGE_READ, IMESSAGE_OPENING)[0],
        undelivered=_warm(SURFACE_IMESSAGE, IMESSAGE_READ, IMESSAGE_OPENING)[1],
    ),
    CapabilityCase(
        "slack-status-question",
        "Is Slack already connected for this workspace?",
        combine(
            status_read_scorer(),
            no_surface_action_scorer(
                SURFACE_SLACK, (SLACK_APP_MANIFEST_ACTION, SLACK_CHANNELS_ACTION)
            ),
            attempted_tools_scorer(required=(), forbidden=NEIGHBORS, orderings=()),
        ),
        digest_tag="surface-setup:slack-status-question",
    ),
    CapabilityCase(
        "slack-installed-status-question",
        "Is Slack already connected for this workspace?",
        combine(
            no_surface_action_scorer(SURFACE_SLACK, (SLACK_APP_MANIFEST_ACTION,)),
            attempted_tools_scorer(required=(), forbidden=NEIGHBORS, orderings=()),
        ),
        rubric=(
            "The answer states that ufo is already installed in this workspace's Slack and "
            "answers in its channels and direct messages.",
            "The answer does not treat the Slack install as absent, unfinished, or as having "
            "produced nothing, and does not offer to install Slack or to create a Slack app. "
            "Naming a separate missing Slack account is not such a claim.",
        ),
        digest_tag="surface-setup:slack-installed-status-question",
        seed=_bind_slack_install,
        cleanup=_drop_slack_install,
    ),
    CapabilityCase(
        "slack-personal-account",
        "Connect my own Slack account so you can read my DMs and reply as me.",
        combine(
            no_surface_action_scorer(SURFACE_SLACK),
            attempted_tools_scorer(required=(), forbidden=("object_apply",), orderings=()),
        ),
        digest_tag="surface-setup:slack-personal-account",
    ),
    CapabilityCase(
        "imessage-line-question",
        "What number would I text you at?",
        no_surface_action_scorer(SURFACE_IMESSAGE, (IMESSAGE_CONNECT_ACTION,)),
        digest_tag="surface-setup:imessage-line-question",
    ),
)
