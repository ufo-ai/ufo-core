"""Public re-export: the operator web session an operator-only surface reuses — the `identify`
resolver that gates on the operator domain and resolves `?ws=`, and the POST handler that binds the
shared session cookie — plus the fleet directory those surfaces index themselves by. One home for
the operator-surface auth every debug tool shares."""

from ufo.runtime.ext.operator import (
    FleetDirectory as FleetDirectory,
)
from ufo.runtime.ext.operator import (
    bind_operator_session as bind_operator_session,
)
from ufo.runtime.ext.operator import (
    resolve_operator_workspace as resolve_operator_workspace,
)
