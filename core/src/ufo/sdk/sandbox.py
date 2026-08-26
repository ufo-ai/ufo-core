"""Public re-export: an extension registers a `CarrierSpec` and implements the `Carrier` protocol
over the sandbox value objects the session passes it — the seam a deploy swaps backends at.

`ufo.sdk` is a package of thin re-export modules with an empty `__init__.py` (the gate bans
code in any `__init__.py`), so the public surface lives in named modules like this one."""

from ufo.ext.manifest import CarrierSpec as CarrierSpec
from ufo.sandbox.containment import ContainmentError as ContainmentError
from ufo.sandbox.containment import contained_file as contained_file
from ufo.sandbox.containment import contained_leaf as contained_leaf
from ufo.sandbox.containment import contained_relative as contained_relative
from ufo.sandbox.containment import contained_root as contained_root
from ufo.sandbox.ingress_host import serve_port as serve_port
from ufo.sandbox.ingress_host import shipped_anchor as shipped_anchor
from ufo.sandbox.ingress_host import shipped_app_slug as shipped_app_slug
from ufo.sandbox.ingress_token import (
    INGRESS_SESSION_ENDED_MESSAGE as INGRESS_SESSION_ENDED_MESSAGE,
)
from ufo.sandbox.session import CA_SANDBOX_PATH as CA_SANDBOX_PATH
from ufo.sandbox.session import CA_STAGING_PATH as CA_STAGING_PATH
from ufo.sandbox.session import COPY_IN_PROG as COPY_IN_PROG
from ufo.sandbox.session import NO_PROXY_HOSTS as NO_PROXY_HOSTS
from ufo.sandbox.session import NODE_GLOBAL_MODULES as NODE_GLOBAL_MODULES
from ufo.sandbox.session import PLAYWRIGHT_BROWSERS_DIR as PLAYWRIGHT_BROWSERS_DIR
from ufo.sandbox.session import PLAYWRIGHT_VERSION as PLAYWRIGHT_VERSION
from ufo.sandbox.session import SANDBOX_ENV as SANDBOX_ENV
from ufo.sandbox.session import SANDBOX_MODULE_BOOTSTRAP as SANDBOX_MODULE_BOOTSTRAP
from ufo.sandbox.session import SANDBOX_PYTHON_FLAG as SANDBOX_PYTHON_FLAG
from ufo.sandbox.session import SANDBOX_SIZES as SANDBOX_SIZES
from ufo.sandbox.session import SANDBOX_TMPDIR as SANDBOX_TMPDIR
from ufo.sandbox.session import SENTINEL_MODEL_KEY as SENTINEL_MODEL_KEY
from ufo.sandbox.session import SYSTEM_CA_BUNDLE as SYSTEM_CA_BUNDLE
from ufo.sandbox.session import SYSTEM_SKILLS_ROOT as SYSTEM_SKILLS_ROOT
from ufo.sandbox.session import WORKSPACE_DIR as WORKSPACE_DIR
from ufo.sandbox.session import Carrier as Carrier
from ufo.sandbox.session import CommandStopping as CommandStopping
from ufo.sandbox.session import DialTarget as DialTarget
from ufo.sandbox.session import ExecResult as ExecResult
from ufo.sandbox.session import ProxyEndpoint as ProxyEndpoint
from ufo.sandbox.session import Sandbox as Sandbox
from ufo.sandbox.session import SandboxHandle as SandboxHandle
from ufo.sandbox.session import SandboxSession as SandboxSession
from ufo.sandbox.session import SandboxSpec as SandboxSpec
from ufo.sandbox.session import SandboxUnreachable as SandboxUnreachable
from ufo.sandbox.session import SkillExecuting as SkillExecuting
from ufo.sandbox.session import egress_proxy_env as egress_proxy_env
from ufo.sandbox.session import sandbox_runtime_root as sandbox_runtime_root
from ufo.sandbox.session import shell_path as shell_path
from ufo.sandbox.session import ufo_fs_file_op as ufo_fs_file_op
from ufo.sandbox.session import workspace_path as workspace_path
