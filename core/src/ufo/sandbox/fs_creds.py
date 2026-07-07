"""Mint a short-lived credential scoped to one conversation's workspace prefix in the blob bucket,
for the s3fs mount in that conversation's sandbox.

STS AssumeRole with an inline session policy limits the session to
`<bucket>/conversations/<id>/workspace/*`, so a credential leaked from a sandbox reaches only its
own workspace files (the agent reads and writes those anyway) — never the transcript
(`conversations/<id>/messages.json.lz4`) or compaction records that sit ABOVE the workspace prefix,
and never another conversation's. The role and bucket are fixed per deploy; `mint` runs per
conversation at sandbox bring-up. Uniform across AWS STS and MinIO STS (the role ARN is arbitrary on
MinIO, which applies the inline policy intersected with the caller's)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from aiobotocore.session import ClientCreatorContext, get_session

# The minted credential outlives one active sandbox session; bring-up re-mints on every create, so
# the floor only has to cover a session between reaps, comfortably under STS's 1h default.
SANDBOX_FS_CRED_TTL_SECONDS = 3600
WORKSPACE_SEGMENT = "workspace"
DEFAULT_S3_REGION = "us-east-1"


@dataclass(frozen=True)
class SandboxFsCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str


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


@dataclass(frozen=True)
class AwsStsClient:
    """The real STS client: a fresh aiobotocore client per `mint`, mirroring the blob store's
    per-call client, against AWS STS or a MinIO STS endpoint (`endpoint_url`). The host process's
    own credentials sign the AssumeRole call; the inline session policy narrows the result."""

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
    """Mints workspace-prefix-scoped credentials for the sandbox-fs mount and carries the
    sandbox-reachable endpoint the s3fs command needs. One per deploy (role, bucket, and endpoints
    fixed); `mint` is called per conversation at sandbox bring-up, against AWS STS or MinIO STS
    alike. The cred is confined to the conversation's `workspace/`, never the transcript above."""

    sts: StsClient
    role_arn: str
    bucket: str
    s3_url: str
    region: str
    path_style: bool
    ttl_seconds: int = SANDBOX_FS_CRED_TTL_SECONDS

    async def mint(self, conversation_id: UUID) -> SandboxFsCredentials:
        response = await self.sts.assume_role(
            RoleArn=self.role_arn,
            RoleSessionName=f"sbxfs-{conversation_id}"[:64],
            Policy=workspace_prefix_policy(self.bucket, workspace_key_prefix(conversation_id)),
            DurationSeconds=self.ttl_seconds,
        )
        credentials = response["Credentials"]
        return SandboxFsCredentials(
            credentials["AccessKeyId"],
            credentials["SecretAccessKey"],
            credentials["SessionToken"],
        )
