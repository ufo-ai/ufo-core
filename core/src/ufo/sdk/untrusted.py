"""The wall content declared untrusted is held in — one definition for every renderer, so an
extension fencing third-party output (a probe's stdout, a provider response) says exactly what
core's own tool-result and subagent hand-back paths say."""

from ufo.harness.untrusted import UNTRUSTED_CLOSE as UNTRUSTED_CLOSE
from ufo.harness.untrusted import UNTRUSTED_OPEN as UNTRUSTED_OPEN
from ufo.harness.untrusted import wall as wall
