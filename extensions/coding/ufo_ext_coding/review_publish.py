from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.tools import TextContent, ToolContext, ToolResult
from ufo_ext_coding.github_app import GIT_SLOT, GITHUB_API
from ufo_ext_coding.review_checkout import CodeReviewOutput
from ufo_ext_coding.review_routing import StoredReviewRun, review_run_for

CHECK_NAME = "ufo review"
CHECK_TITLE = "Code review"
CHECKS_API_VERSION = "2022-11-28"
CHECKS_TIMEOUT_SECONDS = 30
CHECKS_PAGE_SIZE = 100
CHECKS_PAGE_LIMIT = 10
CHECK_SUMMARY_MAX_CHARS = 60_000


class PublishCodeReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID = Field(description="Review run from the source-change wake.")
    review: CodeReviewOutput
    user_description: str = Field(description="That you are publishing the completed review.")


class CheckOutput(BaseModel):
    title: str
    summary: str


class CreateCheckRun(BaseModel):
    name: str
    head_sha: str
    status: Literal["completed"]
    conclusion: Literal["neutral"]
    external_id: str
    output: CheckOutput


class UpdateCheckRun(BaseModel):
    name: str
    status: Literal["completed"]
    conclusion: Literal["neutral"]
    external_id: str
    output: CheckOutput


class CheckRun(BaseModel):
    id: int
    external_id: str | None = None


class CheckRunList(BaseModel):
    check_runs: tuple[CheckRun, ...]


def render_check_summary(review: CodeReviewOutput) -> str:
    if review.findings:
        parts = ["Critical defects"]
        for finding in review.findings:
            parts.extend(
                (
                    "",
                    f"- `{finding.path}:{finding.line}` {finding.title}",
                    f"  Trigger: {finding.trigger}",
                    f"  Failure: {finding.failure}",
                    f"  Impact: {finding.impact[0].upper()}{finding.impact[1:]}.",
                )
            )
    else:
        parts = ["No critical defect found."]
    return "\n".join(parts)[:CHECK_SUMMARY_MAX_CHARS]


@dataclass(frozen=True)
class GitHubCheckPublisher:
    token: str
    transport: httpx.AsyncBaseTransport | None = None

    async def publish(self, run: StoredReviewRun, review: CodeReviewOutput) -> int:
        external_id = str(run.run_id)
        output = CheckOutput(title=CHECK_TITLE, summary=render_check_summary(review))
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": CHECKS_API_VERSION,
        }
        async with httpx.AsyncClient(
            base_url=GITHUB_API,
            headers=headers,
            timeout=CHECKS_TIMEOUT_SECONDS,
            transport=self.transport,
        ) as client:
            existing = await self._existing(client, run, external_id)
            if existing is not None:
                await self._request(
                    client,
                    "PATCH",
                    f"/repos/{run.repository}/check-runs/{existing}",
                    UpdateCheckRun(
                        name=CHECK_NAME,
                        status="completed",
                        conclusion="neutral",
                        external_id=external_id,
                        output=output,
                    ),
                )
                return existing
            response = await self._request(
                client,
                "POST",
                f"/repos/{run.repository}/check-runs",
                CreateCheckRun(
                    name=CHECK_NAME,
                    head_sha=run.head_sha,
                    status="completed",
                    conclusion="neutral",
                    external_id=external_id,
                    output=output,
                ),
            )
            return CheckRun.model_validate(response.json()).id

    async def _existing(
        self, client: httpx.AsyncClient, run: StoredReviewRun, external_id: str
    ) -> int | None:
        for page in range(1, CHECKS_PAGE_LIMIT + 1):
            response = await self._request(
                client,
                "GET",
                f"/repos/{run.repository}/commits/{run.head_sha}/check-runs",
                params={
                    "check_name": CHECK_NAME,
                    "filter": "all",
                    "per_page": str(CHECKS_PAGE_SIZE),
                    "page": str(page),
                },
            )
            runs = CheckRunList.model_validate(response.json()).check_runs
            found = next((check.id for check in runs if check.external_id == external_id), None)
            if found is not None:
                return found
            if len(runs) < CHECKS_PAGE_SIZE:
                return None
        raise RuntimeError("GitHub returned too many matching check runs to reconcile")

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        body: BaseModel | None = None,
        *,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        if body is None:
            response = await client.request(method, path, params=params)
        else:
            response = await client.request(
                method, path, params=params, json=body.model_dump(mode="json")
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise RuntimeError(f"GitHub Checks API returned {response.status_code}") from error
        return response


async def publish_code_review(ctx: ToolContext, args: PublishCodeReviewInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("review publication requires the coding extension context")
    run = await review_run_for(ctx.ext, args.run_id, ctx.turn.conversation_id)
    if run is None:
        raise ValueError("review run does not belong to this conversation")
    token = await ctx.ext.credentials.resolve(GIT_SLOT)
    check_id = await GitHubCheckPublisher(token).publish(run, args.review)
    return ToolResult(
        content=(
            TextContent(
                text=(
                    f"Published {CHECK_NAME} for {run.repository}#{run.pull_request_number} "
                    f"at {run.head_sha} as check {check_id}."
                )
            ),
        )
    )
