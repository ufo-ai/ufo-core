"""Public re-export: an extension registers a `CarrierSpec` and implements the `Carrier` protocol
over the sandbox value objects the session passes it — the seam a deploy swaps backends at.

`ufo.sdk` is a package of thin re-export modules with an empty `__init__.py` (the gate bans
code in any `__init__.py`), so the public surface lives in named modules like this one."""

from ufo.ext.manifest import CarrierSpec as CarrierSpec
from ufo.sandbox.containment import ContainmentError as ContainmentError
from ufo.sandbox.containment import contained_leaf as contained_leaf
from ufo.sandbox.containment import contained_relative as contained_relative
from ufo.sandbox.session import COPY_IN_PROG as COPY_IN_PROG
from ufo.sandbox.session import NO_PROXY_HOSTS as NO_PROXY_HOSTS
from ufo.sandbox.session import SANDBOX_MODULE_BOOTSTRAP as SANDBOX_MODULE_BOOTSTRAP
from ufo.sandbox.session import SANDBOX_PYTHON_FLAG as SANDBOX_PYTHON_FLAG
from ufo.sandbox.session import SENTINEL_MODEL_KEY as SENTINEL_MODEL_KEY
from ufo.sandbox.session import TOOL_OUTPUT_DIR as TOOL_OUTPUT_DIR
from ufo.sandbox.session import WORKSPACE_DIR as WORKSPACE_DIR
from ufo.sandbox.session import Carrier as Carrier
from ufo.sandbox.session import DialTarget as DialTarget
from ufo.sandbox.session import ExecResult as ExecResult
from ufo.sandbox.session import ProxyEndpoint as ProxyEndpoint
from ufo.sandbox.session import SandboxHandle as SandboxHandle
from ufo.sandbox.session import SandboxSession as SandboxSession
from ufo.sandbox.session import SandboxSpec as SandboxSpec
from ufo.sandbox.session import SandboxUnreachable as SandboxUnreachable
from ufo.sandbox.session import sbxfs_file_op as sbxfs_file_op
from ufo.sandbox.session import workspace_path as workspace_path
