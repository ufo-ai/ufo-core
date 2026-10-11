"""Public re-export: an extension registers a `CarrierSpec` and implements the `Carrier` protocol
over the sandbox value objects the session passes it — the seam a deploy swaps backends at. The one
extension holding the deploy's bearer to the proxy service declares a `ProxyCredentialSpec` that
builds its `ProxyCredentials`.

`ufo.sdk` is a package of thin re-export modules with an empty `__init__.py` (the gate bans
code in any `__init__.py`), so the public surface lives in named modules like this one."""

from ufo.harness.sandbox.client_binary import client_binary as client_binary
from ufo.harness.sandbox.ingress_host import serve_port as serve_port
from ufo.harness.sandbox.ingress_host import shipped_anchor as shipped_anchor
from ufo.harness.sandbox.ingress_host import shipped_app_slug as shipped_app_slug
from ufo.harness.sandbox.ingress_token import (
    INGRESS_SESSION_ENDED_MESSAGE as INGRESS_SESSION_ENDED_MESSAGE,
)
from ufo.harness.sandbox.session import CA_SANDBOX_PATH as CA_SANDBOX_PATH
from ufo.harness.sandbox.session import CA_STAGING_PATH as CA_STAGING_PATH
from ufo.harness.sandbox.session import NO_PROXY_HOSTS as NO_PROXY_HOSTS
from ufo.harness.sandbox.session import NODE_GLOBAL_MODULES as NODE_GLOBAL_MODULES
from ufo.harness.sandbox.session import PLAYWRIGHT_BROWSERS_DIR as PLAYWRIGHT_BROWSERS_DIR
from ufo.harness.sandbox.session import PLAYWRIGHT_VERSION as PLAYWRIGHT_VERSION
from ufo.harness.sandbox.session import PROXY_CA_CERT_ENV as PROXY_CA_CERT_ENV
from ufo.harness.sandbox.session import PROXY_SESSION_ENV_NAMES as PROXY_SESSION_ENV_NAMES
from ufo.harness.sandbox.session import SANDBOX_ENV as SANDBOX_ENV
from ufo.harness.sandbox.session import SANDBOX_GID as SANDBOX_GID
from ufo.harness.sandbox.session import SANDBOX_PYTHON_FLAG as SANDBOX_PYTHON_FLAG
from ufo.harness.sandbox.session import SANDBOX_RUNS_ROOT as SANDBOX_RUNS_ROOT
from ufo.harness.sandbox.session import SANDBOX_SIZES as SANDBOX_SIZES
from ufo.harness.sandbox.session import SANDBOX_TMPDIR as SANDBOX_TMPDIR
from ufo.harness.sandbox.session import SANDBOX_UID as SANDBOX_UID
from ufo.harness.sandbox.session import SYSTEM_CA_BUNDLE as SYSTEM_CA_BUNDLE
from ufo.harness.sandbox.session import SYSTEM_SKILLS_ROOT as SYSTEM_SKILLS_ROOT
from ufo.harness.sandbox.session import TOOL_OUTPUT_DIRNAME as TOOL_OUTPUT_DIRNAME
from ufo.harness.sandbox.session import UFO_HOME_ENV as UFO_HOME_ENV
from ufo.harness.sandbox.session import WORKSPACE_DIR as WORKSPACE_DIR
from ufo.harness.sandbox.session import Carrier as Carrier
from ufo.harness.sandbox.session import DialTarget as DialTarget
from ufo.harness.sandbox.session import ExecResult as ExecResult
from ufo.harness.sandbox.session import Sandbox as Sandbox
from ufo.harness.sandbox.session import SandboxHandle as SandboxHandle
from ufo.harness.sandbox.session import SandboxProviderUnavailable as SandboxProviderUnavailable
from ufo.harness.sandbox.session import SandboxSession as SandboxSession
from ufo.harness.sandbox.session import SandboxSpec as SandboxSpec
from ufo.harness.sandbox.session import SandboxUnreachable as SandboxUnreachable
from ufo.harness.sandbox.session import ShellSession as ShellSession
from ufo.harness.sandbox.session import ShellSize as ShellSize
from ufo.harness.sandbox.session import ShellUnsupported as ShellUnsupported
from ufo.harness.sandbox.session import SkillExecuting as SkillExecuting
from ufo.harness.sandbox.session import sandbox_runtime_root as sandbox_runtime_root
from ufo.harness.sandbox.session import shell_path as shell_path
from ufo.harness.sandbox.session import ufo_fs_file_op as ufo_fs_file_op
from ufo.harness.sandbox.session import workspace_path as workspace_path
from ufo.runtime.ext.manifest import CarrierSpec as CarrierSpec
from ufo.runtime.ext.manifest import ProxyCredentials as ProxyCredentials
from ufo.runtime.ext.manifest import ProxyCredentialSpec as ProxyCredentialSpec
