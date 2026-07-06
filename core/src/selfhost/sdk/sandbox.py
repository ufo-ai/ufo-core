"""Public re-export: an extension registers a `CarrierSpec` and implements the `Carrier` protocol
over the sandbox value objects the session passes it — the seam a deploy swaps backends at.

`selfhost.sdk` is a package of thin re-export modules with an empty `__init__.py` (the gate bans
code in any `__init__.py`), so the public surface lives in named modules like this one."""

from selfhost.blob import BlobStore as BlobStore
from selfhost.ext.manifest import CarrierSpec as CarrierSpec
from selfhost.sandbox.fs_mount import AWS_CREDENTIALS_PATH as AWS_CREDENTIALS_PATH
from selfhost.sandbox.fs_mount import (
    MOUNT_HEALTH_CHECK_TIMEOUT_SECONDS as MOUNT_HEALTH_CHECK_TIMEOUT_SECONDS,
)
from selfhost.sandbox.fs_mount import MOUNT_TIMEOUT_SECONDS as MOUNT_TIMEOUT_SECONDS
from selfhost.sandbox.fs_mount import aws_credentials_file as aws_credentials_file
from selfhost.sandbox.fs_mount import mount_scripts as mount_scripts
from selfhost.sandbox.fs_mount import s3fs_command as s3fs_command
from selfhost.sandbox.session import SENTINEL_MODEL_KEY as SENTINEL_MODEL_KEY
from selfhost.sandbox.session import WORKSPACE_DIR as WORKSPACE_DIR
from selfhost.sandbox.session import Carrier as Carrier
from selfhost.sandbox.session import ExecResult as ExecResult
from selfhost.sandbox.session import MountSpec as MountSpec
from selfhost.sandbox.session import SandboxHandle as SandboxHandle
from selfhost.sandbox.session import SandboxSpec as SandboxSpec
from selfhost.sandbox.session import workspace_path as workspace_path
