"""Public structured logging and metrics for extensions.

The counter registry stays in core, so an extension emits a name core declares or fails loud —
it cannot mint one, and the fleet's metric surface stays enumerable from one place."""

from ufo.harness.o11y import emit_metric as emit_metric
from ufo.harness.o11y import log as log
from ufo.harness.o11y import log_error as log_error
from ufo.harness.o11y import turn_profile as turn_profile
from ufo.harness.o11y import warn as warn
