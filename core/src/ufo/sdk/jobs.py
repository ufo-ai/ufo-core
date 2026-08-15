"""Public re-export: extensions declare their background jobs from here, never from core internals.

`ufo.sdk` is a package of thin re-export modules with an empty `__init__.py`, so the public
surface lives in named modules like this one. `owner_candidates` is how an extension declares which
workspaces its job has work in — a builder, invoked per tick, of a select over its own tables
projecting distinct `workspace_id`, which core runs through the one RLS-bypass read to name the
workspaces the dispatcher binds. Building per tick lets dueness be time-relative: compute `now`
inside the builder and embed the cutoff."""

from ufo.candidates import (
    owner_candidates as owner_candidates,
)
from ufo.ext.context import (
    store_key_workspaces as store_key_workspaces,
)
from ufo.ext.manifest import (
    JobSpec as JobSpec,
)
