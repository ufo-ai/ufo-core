"""Public re-export: an extension registers a `CarrierSpec` and implements the `Carrier` protocol
over the sandbox value objects the session passes it — the seam a deploy swaps backends at.

`ufo.sdk` is a package of thin re-export modules with an empty `__init__.py` (the gate bans
code in any `__init__.py`), so the public surface lives in named modules like this one."""

from ufo.ext.manifest import CarrierSpec as CarrierSpec
from ufo.sandbox.session import NO_PROXY_HOSTS as NO_PROXY_HOSTS
from ufo.sandbox.session import SENTINEL_MODEL_KEY as SENTINEL_MODEL_KEY
from ufo.sandbox.session import WORKSPACE_DIR as WORKSPACE_DIR
from ufo.sandbox.session import Carrier as Carrier
from ufo.sandbox.session import DialTarget as DialTarget
from ufo.sandbox.session import ExecResult as ExecResult
from ufo.sandbox.session import ProxyEndpoint as ProxyEndpoint
from ufo.sandbox.session import SandboxHandle as SandboxHandle
from ufo.sandbox.session import SandboxSession as SandboxSession
from ufo.sandbox.session import SandboxSpec as SandboxSpec
from ufo.sandbox.session import SandboxUnreachable as SandboxUnreachable
from ufo.sandbox.session import workspace_path as workspace_path
