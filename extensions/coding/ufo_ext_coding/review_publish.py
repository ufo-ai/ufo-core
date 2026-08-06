from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.tools import TextContent, ToolContext, ToolResult
from ufo_ext_coding.github_app import GIT_SLOT, GITHUB_API
from ufo_ext_coding.review_checkout import CODE_REVIEW_PROFILE_NAME, CodeReviewOutput
from ufo_ext_coding.review_routing import StoredReviewRun, review_run_for

CHECK_NAME = "ufo review"
CHECK_TITLE = "Code review"
WEB_SURFACE_PATH = "/surface/web"
REVIEW_CONVERSATION_LABEL = "Review conversation:"
CHECKS_API_VERSION = "2022-11-28"
CHECKS_TIMEOUT_SECONDS = 30
CHECKS_PAGE_SIZE = 100
CHECKS_PAGE_LIMIT = 10
CHECK_SUMMARY_MAX_CHARS = 60_000
CheckConclusion = Literal["success", "action_required"]


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
    conclusion: CheckConclusion
    external_id: str
    output: CheckOutput
    details_url: str | None = None


class UpdateCheckRun(BaseModel):
    name: str
    status: Literal["completed"]
    conclusion: CheckConclusion
    external_id: str
    output: CheckOutput
    details_url: str | None = None


class CheckRun(BaseModel):
    id: int
    external_id: str | None = None


class CheckRunList(BaseModel):
    check_runs: tuple[CheckRun, ...]


def review_conversation_url(public_base_url: str | None, run: StoredReviewRun) -> str | None:
    """Where the review itself happened, on the web surface: the reviewer child's own run page under
    its profile, which is the portal route that opens a conversation of the subagent surface — the
    chat permalink opens a web chat or an agent's listed conversation, and a child's is neither.
    Absent until that child records it, and on a deploy that publishes no public base URL."""
    if public_base_url is None or run.review_conversation_id is None:
        return None
    return (
        f"{public_base_url.rstrip('/')}{WEB_SURFACE_PATH}#/subagents/{CODE_REVIEW_PROFILE_NAME}"
        f"/conversations/{run.review_conversation_id}"
    )


def render_check_summary(review: CodeReviewOutput, conversation_url: str | None = None) -> str:
    if review.findings:
        parts = ["Severe defects"]
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
        parts = ["No severe defect found."]
    body = "\n".join(parts)
    if conversation_url is None:
        return body[:CHECK_SUMMARY_MAX_CHARS]
    link = f"\n\n{REVIEW_CONVERSATION_LABEL} {conversation_url}"
    return body[: CHECK_SUMMARY_MAX_CHARS - len(link)] + link


@dataclass(frozen=True)
class GitHubCheckPublisher:
    token: str
    conversation_url: str | None = None
    transport: httpx.AsyncBaseTransport | None = None

    async def publish(self, run: StoredReviewRun, review: CodeReviewOutput) -> int:
        external_id = str(run.run_id)
        output = CheckOutput(
            title=CHECK_TITLE, summary=render_check_summary(review, self.conversation_url)
        )
        conclusion: CheckConclusion = "action_required" if review.findings else "success"
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
                        conclusion=conclusion,
                        external_id=external_id,
                        output=output,
                        details_url=self.conversation_url,
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
                    conclusion=conclusion,
                    external_id=external_id,
                    output=output,
                    details_url=self.conversation_url,
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
                method, path, params=params, json=body.model_dump(mode="json", exclude_none=True)
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
    check_id = await GitHubCheckPublisher(
        token, review_conversation_url(ctx.public_base_url, run)
    ).publish(run, args.review)
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
