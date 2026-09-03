"""Public re-export: an extension registers a `CarrierSpec` and implements the `Carrier` protocol
over the sandbox value objects the session passes it — the seam a deploy swaps backends at.

`ufo.sdk` is a package of thin re-export modules with an empty `__init__.py` (the gate bans
code in any `__init__.py`), so the public surface lives in named modules like this one."""

from ufo.harness.containment import ContainmentError as ContainmentError
from ufo.harness.containment import contained_file as contained_file
from ufo.harness.containment import contained_leaf as contained_leaf
from ufo.harness.containment import contained_relative as contained_relative
from ufo.harness.containment import contained_root as contained_root
from ufo.harness.sandbox.client_binary import client_binary as client_binary
from ufo.harness.sandbox.ingress_host import serve_port as serve_port
from ufo.harness.sandbox.ingress_host import shipped_anchor as shipped_anchor
from ufo.harness.sandbox.ingress_host import shipped_app_slug as shipped_app_slug
from ufo.harness.sandbox.ingress_token import (
    INGRESS_SESSION_ENDED_MESSAGE as INGRESS_SESSION_ENDED_MESSAGE,
)
from ufo.harness.sandbox.session import CA_SANDBOX_PATH as CA_SANDBOX_PATH
from ufo.harness.sandbox.session import CA_STAGING_PATH as CA_STAGING_PATH
from ufo.harness.sandbox.session import COPY_IN_PROG as COPY_IN_PROG
from ufo.harness.sandbox.session import NO_PROXY_HOSTS as NO_PROXY_HOSTS
from ufo.harness.sandbox.session import NODE_GLOBAL_MODULES as NODE_GLOBAL_MODULES
from ufo.harness.sandbox.session import PLAYWRIGHT_BROWSERS_DIR as PLAYWRIGHT_BROWSERS_DIR
from ufo.harness.sandbox.session import PLAYWRIGHT_VERSION as PLAYWRIGHT_VERSION
from ufo.harness.sandbox.session import PROXY_PASSWORD as PROXY_PASSWORD
from ufo.harness.sandbox.session import SANDBOX_ENV as SANDBOX_ENV
from ufo.harness.sandbox.session import SANDBOX_GID as SANDBOX_GID
from ufo.harness.sandbox.session import SANDBOX_MODULE_BOOTSTRAP as SANDBOX_MODULE_BOOTSTRAP
from ufo.harness.sandbox.session import SANDBOX_PYTHON_FLAG as SANDBOX_PYTHON_FLAG
from ufo.harness.sandbox.session import SANDBOX_RUNS_ROOT as SANDBOX_RUNS_ROOT
from ufo.harness.sandbox.session import SANDBOX_SIZES as SANDBOX_SIZES
from ufo.harness.sandbox.session import SANDBOX_TMPDIR as SANDBOX_TMPDIR
from ufo.harness.sandbox.session import SANDBOX_UID as SANDBOX_UID
from ufo.harness.sandbox.session import SENTINEL_MODEL_KEY as SENTINEL_MODEL_KEY
from ufo.harness.sandbox.session import SYSTEM_CA_BUNDLE as SYSTEM_CA_BUNDLE
from ufo.harness.sandbox.session import SYSTEM_SKILLS_ROOT as SYSTEM_SKILLS_ROOT
from ufo.harness.sandbox.session import WORKSPACE_DIR as WORKSPACE_DIR
from ufo.harness.sandbox.session import Carrier as Carrier
from ufo.harness.sandbox.session import DialTarget as DialTarget
from ufo.harness.sandbox.session import ExecResult as ExecResult
from ufo.harness.sandbox.session import ProxyEndpoint as ProxyEndpoint
from ufo.harness.sandbox.session import Sandbox as Sandbox
from ufo.harness.sandbox.session import SandboxHandle as SandboxHandle
from ufo.harness.sandbox.session import SandboxProviderUnavailable as SandboxProviderUnavailable
from ufo.harness.sandbox.session import SandboxSession as SandboxSession
from ufo.harness.sandbox.session import SandboxSpec as SandboxSpec
from ufo.harness.sandbox.session import SandboxUnreachable as SandboxUnreachable
from ufo.harness.sandbox.session import SkillExecuting as SkillExecuting
from ufo.harness.sandbox.session import egress_proxy_env as egress_proxy_env
from ufo.harness.sandbox.session import sandbox_runtime_root as sandbox_runtime_root
from ufo.harness.sandbox.session import shell_path as shell_path
from ufo.harness.sandbox.session import ufo_fs_file_op as ufo_fs_file_op
from ufo.harness.sandbox.session import workspace_path as workspace_path
from ufo.runtime.ext.manifest import CarrierSpec as CarrierSpec
