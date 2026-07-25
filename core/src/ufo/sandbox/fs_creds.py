"""Issue and redeem a sandbox mount token for a short-lived credential scoped to one conversation's
workspace prefix in the blob bucket.

STS AssumeRole with an inline session policy limits the session to
`<bucket>/conversations/<id>/workspace/*`, so a credential leaked from a sandbox reaches only its
own workspace files (the agent reads and writes those anyway) — never the transcript
(`conversations/<id>/messages.json.lz4`) or compaction records that sit ABOVE the workspace prefix,
and never another conversation's. s3fs redeems the signed, turn-bound token through the sandbox
proxy whenever its current credential nears expiry; the proxy admits each refresh only while that
turn remains live. The role and bucket are fixed per deploy. Uniform across AWS STS and MinIO STS
(the role ARN is arbitrary on MinIO, which applies the inline policy intersected with the caller's).
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from aiobotocore.session import ClientCreatorContext, get_session
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ufo.config import BlobConfig
from ufo.sandbox.session import RunToken
from ufo.token_signing import SignedTokenError, sign_token, verify_token

SANDBOX_FS_CRED_TTL_SECONDS = 3600
SANDBOX_FS_GATE_TOKEN_TTL_SECONDS = 600
SANDBOX_FS_TOKEN_SECRET_ENV = "UFO_SANDBOX_FS_TOKEN_SECRET"
SANDBOX_FS_CREDENTIAL_PATH = "/sandbox-fs-credentials/"
WORKSPACE_SEGMENT = "workspace"
DEFAULT_S3_REGION = "us-east-1"


class SandboxFsCredentials(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    access_key_id: str
    secret_access_key: str
    session_token: str
    expiration: datetime
    role_arn: str

    def ecs_json(self) -> bytes:
        return json.dumps(
            {
                "AccessKeyId": self.access_key_id,
                "SecretAccessKey": self.secret_access_key,
                "Token": self.session_token,
                "Expiration": self.expiration.isoformat().replace("+00:00", "Z"),
                "RoleArn": self.role_arn,
            },
            separators=(",", ":"),
        ).encode()


class InvalidSandboxFsToken(ValueError):
    """The sandbox credential path token failed authentication."""


class _SandboxFsTurnClaims(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["turn"] = "turn"
    conversation_id: UUID
    workspace_id: UUID
    turn_id: UUID


class _SandboxFsGateClaims(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["deploy_gate"] = "deploy_gate"
    conversation_id: UUID
    expires_at: int


_SANDBOX_FS_TOKEN_CLAIMS: TypeAdapter[_SandboxFsTurnClaims | _SandboxFsGateClaims] = TypeAdapter(
    Annotated[_SandboxFsTurnClaims | _SandboxFsGateClaims, Field(discriminator="kind")]
)


class StsClient(Protocol):
    async def assume_role(
        self, *, RoleArn: str, RoleSessionName: str, Policy: str, DurationSeconds: int
    ) -> Any: ...


def workspace_key_prefix(conversation_id: UUID) -> str:
    """The agent-mounted workspace prefix — `conversations/<id>/workspace`, the ONLY prefix mounted
    into the sandbox and the ONLY prefix the minted credential can touch. The conversation's
    `messages.json.lz4` transcript and compaction records sit ABOVE it (framework-only), so the
    agent cannot read or corrupt its own trajectory; it reaches only its workspace files."""
    return f"conversations/{conversation_id}/{WORKSPACE_SEGMENT}"


def workspace_prefix_policy(bucket: str, key_prefix: str) -> str:
    """An inline session policy that confines the sandbox's mount credential to one prefix: both
    object access (read/write/delete) and `ListBucket` are scoped to `key_prefix` (the
    conversation's `workspace/`). So a credential leaked from a sandbox reaches only its own
    workspace files and can enumerate only their key names — never the transcript/compactions above
    it, another conversation's, or another tenant's.

    `ListBucket` is prefix-scoped via an `s3:prefix` condition, so s3fs readdir stays within the
    mounted prefix without the condition breaking the mount — a fresh, empty prefix is handled by
    the mount's `compat_dir`, not by loosening the condition."""
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                    "Resource": f"arn:aws:s3:::{bucket}/{key_prefix}/*",
                },
                {
                    "Effect": "Allow",
                    "Action": "s3:ListBucket",
                    "Resource": f"arn:aws:s3:::{bucket}",
                    "Condition": {"StringLike": {"s3:prefix": [f"{key_prefix}/*", key_prefix]}},
                },
            ],
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def issue_sandbox_fs_gate_token(
    conversation_id: UUID, token_secret: bytes, expires_at: datetime
) -> str:
    claims = _SandboxFsGateClaims(
        conversation_id=conversation_id,
        expires_at=int(expires_at.timestamp()),
    )
    return sign_token(token_secret, claims.model_dump_json().encode())


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AwsStsClient:
    """The real STS client: a fresh aiobotocore client per AssumeRole call, mirroring the blob
    store's per-call client, against AWS STS or a MinIO STS endpoint (`endpoint_url`). The host
    process's own credentials sign the AssumeRole call; the inline session policy narrows the
    result."""

    endpoint_url: str | None
    region: str | None

    async def assume_role(
        self, *, RoleArn: str, RoleSessionName: str, Policy: str, DurationSeconds: int
    ) -> Any:
        async with self._client() as client:
            return await client.assume_role(
                RoleArn=RoleArn,
                RoleSessionName=RoleSessionName,
                Policy=Policy,
                DurationSeconds=DurationSeconds,
            )

    def _client(self) -> ClientCreatorContext:
        return get_session().create_client(
            "sts", endpoint_url=self.endpoint_url, region_name=self.region or DEFAULT_S3_REGION
        )


@dataclass(frozen=True)
class SandboxFsCredentialMinter:
    """Issues an opaque conversation token and redeems it for workspace-prefix-scoped credentials.

    The production token names its turn and is authenticated with a deploy secret shared by serve
    and the sandbox proxy. Every redemption checks that turn's live database state, while the agent
    sees neither the token nor any redeemed STS credential."""

    sts: StsClient
    role_arn: str
    bucket: str
    s3_url: str
    region: str
    path_style: bool
    token_secret: bytes
    ttl_seconds: int = SANDBOX_FS_CRED_TTL_SECONDS
    now: Callable[[], datetime] = _utc_now

    def issue(self, conversation_id: UUID, run: RunToken) -> str:
        return sign_token(
            self.token_secret,
            _SandboxFsTurnClaims(
                conversation_id=conversation_id,
                workspace_id=run.workspace_id,
                turn_id=run.turn_id,
            )
            .model_dump_json()
            .encode(),
        )

    async def refresh(
        self, token: str, authorize: Callable[[RunToken], Awaitable[bool]]
    ) -> SandboxFsCredentials:
        try:
            claims = _SANDBOX_FS_TOKEN_CLAIMS.validate_json(verify_token(token, self.token_secret))
        except (SignedTokenError, ValueError) as error:
            raise InvalidSandboxFsToken("invalid sandbox-fs token") from error
        match claims:
            case _SandboxFsTurnClaims():
                run = RunToken(workspace_id=claims.workspace_id, turn_id=claims.turn_id)
                if not await authorize(run):
                    raise InvalidSandboxFsToken("invalid sandbox-fs token")
            case _SandboxFsGateClaims():
                if claims.expires_at <= int(self.now().timestamp()):
                    raise InvalidSandboxFsToken("invalid sandbox-fs token")
        return await self._mint(claims.conversation_id)

    async def _mint(self, conversation_id: UUID) -> SandboxFsCredentials:
        response = await self.sts.assume_role(
            RoleArn=self.role_arn,
            RoleSessionName=f"sbxfs-{conversation_id}"[:64],
            Policy=workspace_prefix_policy(self.bucket, workspace_key_prefix(conversation_id)),
            DurationSeconds=self.ttl_seconds,
        )
        credentials = response["Credentials"]
        return SandboxFsCredentials(
            access_key_id=credentials["AccessKeyId"],
            secret_access_key=credentials["SecretAccessKey"],
            session_token=credentials["SessionToken"],
            expiration=credentials["Expiration"],
            role_arn=self.role_arn,
        )


def sandbox_fs_minter(blob: BlobConfig) -> SandboxFsCredentialMinter | None:
    if blob.backend != "s3":
        return None
    if blob.bucket is None or blob.s3_url is None or blob.sts_role_arn is None:
        raise ValueError("the s3 blob backend requires bucket, s3_url, and sts_role_arn")
    token_secret = os.environ.get(SANDBOX_FS_TOKEN_SECRET_ENV)
    if not token_secret:
        raise RuntimeError(
            f"{SANDBOX_FS_TOKEN_SECRET_ENV} must sign the sandbox-fs credential endpoint token"
        )
    return SandboxFsCredentialMinter(
        sts=AwsStsClient(endpoint_url=blob.sts_endpoint, region=blob.region),
        role_arn=blob.sts_role_arn,
        bucket=blob.bucket,
        s3_url=blob.s3_url,
        region=blob.region or DEFAULT_S3_REGION,
        path_style=blob.path_style,
        token_secret=token_secret.encode(),
    )
