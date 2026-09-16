"""Public re-export: extensions declare their background jobs from here, never from core internals.

`ufo.sdk` is a package of thin re-export modules with an empty `__init__.py`, so the public
surface lives in named modules like this one. `owner_candidates` is how an extension declares which
workspaces its job has work in — a builder, invoked per tick, of a select over its own tables
projecting distinct `workspace_id`, which core runs through the one RLS-bypass read to name the
workspaces the dispatcher binds. Building per tick lets dueness be time-relative: compute `now`
inside the builder and embed the cutoff.

`JobFault` is how a handler names why its work failed: a stack carries no exception message, so
without it every failure reads as a class and a frame, and a provider outage cannot be told from a
revoked token without a pod's stderr. Raise it with text the handler authored against the call it
made — a status and a request, never the provider's payload.

`PAGE_CHANGE_CURSOR_KEY` is the prefix core keys a `page_change` consumer's cursor by inside the
declaring extension's own ScopedStore, `{prefix}:{handler name}`. An extension that sends its own
consumer back over pages it already drained clears that key rather than spelling core's format
itself."""

from ufo.runtime.candidates import (
    WorkspaceCandidates as WorkspaceCandidates,
)
from ufo.runtime.candidates import (
    owner_candidates as owner_candidates,
)
from ufo.runtime.ext.context import (
    agent_is_live as agent_is_live,
)
from ufo.runtime.ext.context import (
    feed_workspaces as feed_workspaces,
)
from ufo.runtime.ext.context import (
    seated_member_workspaces as seated_member_workspaces,
)
from ufo.runtime.ext.context import (
    stored_key_workspaces as stored_key_workspaces,
)
from ufo.runtime.ext.context import (
    undrawn_surface_member_workspaces as undrawn_surface_member_workspaces,
)
from ufo.runtime.ext.context import (
    unseeded_agent_workspaces as unseeded_agent_workspaces,
)
from ufo.runtime.ext.context import (
    untitled_conversation_workspaces as untitled_conversation_workspaces,
)
from ufo.runtime.ext.manifest import (
    PAGE_CHANGE_CURSOR_KEY as PAGE_CHANGE_CURSOR_KEY,
)
from ufo.runtime.ext.manifest import (
    JobFault as JobFault,
)
from ufo.runtime.ext.manifest import (
    JobSpec as JobSpec,
)
