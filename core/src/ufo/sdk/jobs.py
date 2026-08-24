"""Public re-export: extensions declare their background jobs from here, never from core internals.

`ufo.sdk` is a package of thin re-export modules with an empty `__init__.py`, so the public
surface lives in named modules like this one. `owner_candidates` is how an extension declares which
workspaces its job has work in — a builder, invoked per tick, of a select over its own tables
projecting distinct `workspace_id`, which core runs through the one RLS-bypass read to name the
workspaces the dispatcher binds. Building per tick lets dueness be time-relative: compute `now`
inside the builder and embed the cutoff.

`PAGE_CHANGE_CURSOR_KEY` is the prefix core keys a `page_change` consumer's cursor by inside the
declaring extension's own ScopedStore, `{prefix}:{handler name}`. An extension that sends its own
consumer back over pages it already drained clears that key rather than spelling core's format
itself."""

from ufo.ext.context import (
    agent_is_live as agent_is_live,
)
from ufo.ext.context import (
    connection_workspaces as connection_workspaces,
)
from ufo.ext.context import (
    seated_member_workspaces as seated_member_workspaces,
)
from ufo.ext.context import (
    unseeded_agent_workspaces as unseeded_agent_workspaces,
)
from ufo.ext.context import (
    untitled_conversation_workspaces as untitled_conversation_workspaces,
)
from ufo.ext.manifest import (
    PAGE_CHANGE_CURSOR_KEY as PAGE_CHANGE_CURSOR_KEY,
)
from ufo.ext.manifest import (
    JobSpec as JobSpec,
)
from ufo.runtime.candidates import (
    WorkspaceCandidates as WorkspaceCandidates,
)
from ufo.runtime.candidates import (
    owner_candidates as owner_candidates,
)
