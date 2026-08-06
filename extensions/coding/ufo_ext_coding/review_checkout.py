import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ufo.sdk.manifest import SubagentProfile
from ufo.sdk.sandbox import WORKSPACE_DIR, ExecResult, SandboxSession, workspace_path
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_coding.review_routing import ReviewTarget, record_review_conversation

REVIEW_CHECKOUT_TIMEOUT_SECONDS = 300
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
CODE_REVIEW_PROFILE_NAME = "code_review"
CODE_REVIEW_TOOL_NAME = "checkout_code_review"
CODE_REVIEW_READ_TOOL_NAME = "review_read"
CODE_REVIEW_GLOB_TOOL_NAME = "review_glob"
CODE_REVIEW_GREP_TOOL_NAME = "review_grep"
REVIEW_READ_LINE_LIMIT = 2_000
REVIEW_LINE_CHAR_LIMIT = 2_000
REVIEW_GLOB_MATCH_LIMIT = 1_000
REVIEW_GREP_MATCH_LIMIT = 500
BOUNDED_LINES_COMMAND = (
    'character_limit="$1"; line_limit="$2"; shift 2; '
    'git "$@" | cut -c "1-$character_limit" | '
    'sed -n "1,${line_limit}p;${line_limit}q"'
)
CODE_REVIEW_PROMPT = (Path(__file__).parent / "prompts" / "subagent_code_review.md").read_text()


class ExactComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str = Field(description="GitHub repository as owner/name.")
    pull_number: int = Field(gt=0, description="GitHub pull request number.")
    base_sha: str = Field(description="Exact 40-character base commit SHA.")
    head_sha: str = Field(description="Exact 40-character pull request head commit SHA.")

    @field_validator("repository")
    @classmethod
    def validate_repository(cls, value: str) -> str:
        if REPOSITORY_PATTERN.fullmatch(value) is None:
            raise ValueError("repository must be owner/name")
        if any(part in {".", ".."} for part in value.split("/")):
            raise ValueError("repository must be owner/name")
        return value

    @field_validator("base_sha", "head_sha")
    @classmethod
    def validate_sha(cls, value: str) -> str:
        if SHA_PATTERN.fullmatch(value) is None:
            raise ValueError("commit SHA must contain 40 lowercase hexadecimal characters")
        return value


class CheckoutCodeReviewInput(ExactComparison):
    user_description: str = Field(
        description="That you are preparing the exact pull request comparison for review."
    )


class ReviewReadInput(BaseModel):
    file_path: str = Field(description="Repository-relative path or the returned comparison path.")
    offset: int = Field(default=1, ge=1, description="First line to return, starting at one.")
    limit: int = Field(
        default=REVIEW_READ_LINE_LIMIT,
        ge=1,
        le=REVIEW_READ_LINE_LIMIT,
        description="Maximum lines to return.",
    )
    user_description: str = Field(description="Which review file you are reading.")


class ReviewGlobInput(BaseModel):
    pattern: str = Field(description="Glob pattern matched against tracked repository paths.")
    user_description: str = Field(description="Which tracked files you are listing.")


class ReviewGrepInput(BaseModel):
    pattern: str = Field(description="Regex searched across tracked repository text files.")
    glob: str | None = Field(default=None, description="Optional tracked-path glob.")
    ignore_case: bool = False
    head_limit: int = Field(default=200, ge=1, le=REVIEW_GREP_MATCH_LIMIT)
    user_description: str = Field(description="What code pattern you are searching for.")


class CodeReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, description="Affected repository-relative path.")
    line: int = Field(gt=0)
    title: str = Field(min_length=1, description="Concise defect title.")
    trigger: str = Field(
        min_length=1,
        description="Specific supported input or execution path that reaches the defect.",
    )
    failure: str = Field(min_length=1, description="What fails when the trigger is exercised.")
    impact: Literal[
        "security or workspace-boundary breach",
        "data loss, corruption, or wrong-target mutation",
        "production outage, deadlock, or permanently unfinished work",
        "a supported operation fails or cannot complete for valid input",
        "materially incorrect result or state for a supported workflow",
        "substantial availability, reliability, or performance regression",
        "the feature cannot function in its supported production configuration",
        "the code fails to build or breaks required CI",
    ]


class CodeReviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: tuple[CodeReviewFinding, ...] = ()


@dataclass(frozen=True)
class ReviewCheckout:
    path: str
    diff_path: str


class CodeReviewCheckoutResult(ExactComparison):
    path: str
    diff_path: str


@dataclass(frozen=True)
class ExactComparisonCheckout:
    sandbox: SandboxSession
    remote_url: str
    directory: str

    async def run(self, comparison: ExactComparison) -> ReviewCheckout:
        directory = PurePosixPath(workspace_path(self.directory))
        checkout = workspace_path(str(directory / "checkout"))
        diff_path = workspace_path(str(directory / "comparison.diff"))
        created = await self._exec("mkdir", "-p", str(directory))
        if created.exit_code != 0:
            raise RuntimeError("sandbox could not create the review checkout")
        try:
            cloned = await self._exec(
                "git", "clone", "--quiet", "--no-checkout", self.remote_url, checkout
            )
            if cloned.exit_code != 0:
                raise ValueError("comparison does not match repository, pull request, or SHAs")
            await self._fetch(checkout, comparison.base_sha, "refs/ufo/base")
            await self._fetch(
                checkout,
                f"refs/pull/{comparison.pull_number}/head",
                "refs/ufo/head",
            )
            base = await self._revision(checkout, "refs/ufo/base")
            head = await self._revision(checkout, "refs/ufo/head")
            if base != comparison.base_sha or head != comparison.head_sha:
                raise ValueError("comparison does not match repository, pull request, or SHAs")
            remotes = await self._exec("git", "-C", checkout, "remote")
            if remotes.exit_code != 0:
                raise RuntimeError("git could not enumerate review remotes")
            for remote in remotes.stdout.splitlines():
                await self._git(checkout, "remote", "remove", remote)
            await self._git(checkout, "config", "remote.pushDefault", "disabled")
            await self._git(checkout, "checkout", "--quiet", "--detach", "refs/ufo/head")
            rendered = await self._exec(
                "sh",
                "-c",
                'git -C "$1" diff --no-ext-diff --binary --full-index --find-renames '
                'refs/ufo/base...refs/ufo/head > "$2"',
                "sh",
                checkout,
                diff_path,
            )
            if rendered.exit_code != 0:
                raise RuntimeError("git could not render the review comparison")
        except BaseException:
            await self._exec("rm", "-rf", "--", str(directory))
            raise
        return ReviewCheckout(path=checkout, diff_path=diff_path)

    async def remove(self) -> None:
        directory = PurePosixPath(workspace_path(self.directory))
        removed = await self._exec("rm", "-rf", "--", str(directory))
        if removed.exit_code != 0:
            raise RuntimeError("sandbox could not remove the review checkout")

    async def _fetch(self, checkout: str, source: str, target: str) -> None:
        result = await self._exec(
            "git",
            "-C",
            checkout,
            "fetch",
            "--quiet",
            "--no-tags",
            "origin",
            f"{source}:{target}",
        )
        if result.exit_code != 0:
            raise ValueError("comparison does not match repository, pull request, or SHAs")

    async def _revision(self, checkout: str, reference: str) -> str:
        result = await self._exec("git", "-C", checkout, "rev-parse", f"{reference}^{{commit}}")
        if result.exit_code != 0:
            raise ValueError("comparison does not match repository, pull request, or SHAs")
        return result.stdout.strip()

    async def _git(self, checkout: str, *args: str) -> None:
        result = await self._exec("git", "-C", checkout, *args)
        if result.exit_code != 0:
            raise RuntimeError("git could not prepare the review checkout")

    async def _exec(self, *argv: str) -> ExecResult:
        return await self.sandbox.carrier.exec(
            self.sandbox.handle, tuple(argv), timeout_s=REVIEW_CHECKOUT_TIMEOUT_SECONDS
        )


async def checkout_code_review(ctx: ToolContext, args: CheckoutCodeReviewInput) -> ToolResult:
    if ctx.turn.subagent_profile != CODE_REVIEW_PROFILE_NAME:
        raise ValueError("review checkout is available only to the code review candidate")
    expected = ExactComparison.model_validate_json(ctx.turn.inbound)
    comparison = ExactComparison.model_validate(args.model_dump(exclude={"user_description"}))
    if comparison != expected:
        raise ValueError("review checkout input does not match the admitted comparison")
    if ctx.ext is None:
        raise RuntimeError("review checkout requires the coding extension context")
    review_checkout = ExactComparisonCheckout(
        ctx.sandbox,
        f"https://github.com/{comparison.repository}.git",
        _review_paths(ctx)[0],
    )
    checkout = await review_checkout.run(comparison)
    ctx.cleanup.register(review_checkout.remove)
    await record_review_conversation(
        ctx.ext,
        ReviewTarget(
            repository=comparison.repository,
            pull_request_number=comparison.pull_number,
            base_sha=comparison.base_sha,
            head_sha=comparison.head_sha,
        ),
        ctx.turn.conversation_id,
    )
    return ToolResult(
        content=(
            TextContent(
                text=CodeReviewCheckoutResult(
                    **comparison.model_dump(),
                    path=checkout.path,
                    diff_path=checkout.diff_path,
                ).model_dump_json()
            ),
        ),
        untrusted=True,
    )


def _review_paths(ctx: ToolContext) -> tuple[str, str, str]:
    directory = workspace_path(f"{WORKSPACE_DIR}/.ufo-review/{ctx.turn.id.hex}")
    return directory, f"{directory}/checkout", f"{directory}/comparison.diff"


def _review_relative_path(checkout: str, value: str) -> str:
    supplied = PurePosixPath(value)
    if supplied.is_absolute():
        try:
            supplied = PurePosixPath(workspace_path(value)).relative_to(checkout)
        except ValueError as error:
            raise ValueError("review file is outside the verified checkout") from error
    if not supplied.parts or any(part in ("", ".", "..") for part in supplied.parts):
        raise ValueError("review file must be a repository path")
    return str(supplied)


async def _review_exec(ctx: ToolContext, *argv: str) -> ExecResult:
    return await ctx.sandbox.carrier.exec(
        ctx.sandbox.handle, tuple(argv), timeout_s=REVIEW_CHECKOUT_TIMEOUT_SECONDS
    )


def _render_review_lines(lines: list[str], start: int, total: int) -> str:
    rendered = "\n".join(
        f"{number}\t{line[:REVIEW_LINE_CHAR_LIMIT]}"
        for number, line in enumerate(lines, start=start)
    )
    end = start + len(lines) - 1
    footer = f"\n\n[lines {start}-{end} of {total}]"
    if end < total:
        footer += f"; {total - end} more - read with offset={end + 1}"
    return rendered + footer


async def review_read(ctx: ToolContext, args: ReviewReadInput) -> ToolResult:
    _, checkout, diff_path = _review_paths(ctx)
    supplied = workspace_path(args.file_path) if args.file_path.startswith("/") else args.file_path
    if supplied == diff_path or supplied == "comparison.diff":
        diff_result = await ctx.sandbox.run_sbxfs(
            "read", {"path": diff_path, "offset": args.offset, "limit": args.limit}
        )
        content = diff_result.get("content")
        if diff_result.get("is_empty"):
            text = "(file is empty)"
        elif not isinstance(content, str):
            raise RuntimeError("review diff read returned no text")
        else:
            start = diff_result.get("start_line")
            total = diff_result.get("total_lines")
            returned = diff_result.get("lines_returned")
            if not (
                isinstance(start, int) and isinstance(total, int) and isinstance(returned, int)
            ):
                raise RuntimeError("review diff read returned malformed line bounds")
            if returned == 0:
                text = f"(no lines at offset {start}; file has {total} lines)"
            else:
                text = _render_review_lines(content.splitlines(), start, total)
        return ToolResult(content=(TextContent(text=text),), untrusted=True)
    relative = _review_relative_path(checkout, args.file_path)
    tracked = await _review_exec(
        ctx, "git", "-C", checkout, "ls-files", "--stage", "--error-unmatch", "--", relative
    )
    if tracked.exit_code != 0 or tracked.stdout.split(maxsplit=1)[0] not in {"100644", "100755"}:
        raise ValueError("review_read supports tracked text files only")
    try:
        file_result = await ctx.sandbox.run_sbxfs(
            "read",
            {"path": f"{checkout}/{relative}", "offset": args.offset, "limit": args.limit},
        )
    except ValueError as error:
        raise ValueError("review_read supports tracked text files only") from error
    content = file_result.get("content")
    if file_result.get("is_empty"):
        text = "(file is empty)"
    elif not isinstance(content, str):
        raise ValueError("review_read supports tracked text files only")
    else:
        start = file_result.get("start_line")
        total = file_result.get("total_lines")
        returned = file_result.get("lines_returned")
        if not (isinstance(start, int) and isinstance(total, int) and isinstance(returned, int)):
            raise RuntimeError("review file read returned malformed line bounds")
        if returned == 0:
            text = f"(no lines at offset {start}; file has {total} lines)"
        else:
            text = content + f"\n\n[lines {start}-{start + returned - 1} of {total}]"
            remaining = file_result.get("remaining_lines")
            if isinstance(remaining, int) and not isinstance(remaining, bool) and remaining > 0:
                text += f"; {remaining} more - read with offset={file_result.get('next_offset')}"
    return ToolResult(
        content=(TextContent(text=text),),
        untrusted=True,
    )


async def review_glob(ctx: ToolContext, args: ReviewGlobInput) -> ToolResult:
    _, checkout, _ = _review_paths(ctx)
    patterns = (args.pattern, args.pattern.removeprefix("**/"))
    result = await _review_exec(
        ctx,
        "sh",
        "-c",
        BOUNDED_LINES_COMMAND,
        "sh",
        str(REVIEW_LINE_CHAR_LIMIT),
        str(REVIEW_GLOB_MATCH_LIMIT + 1),
        "-C",
        checkout,
        "ls-files",
        "--",
        *(f":(glob){pattern}" for pattern in dict.fromkeys(patterns)),
    )
    if result.exit_code != 0:
        raise RuntimeError("git could not list review files")
    matches = result.stdout.splitlines()
    bounded = matches[:REVIEW_GLOB_MATCH_LIMIT]
    return ToolResult(
        content=(
            TextContent(
                text=json.dumps(
                    {"matches": bounded, "truncated": len(matches) > len(bounded)},
                    separators=(",", ":"),
                )
            ),
        ),
        untrusted=True,
    )


async def review_grep(ctx: ToolContext, args: ReviewGrepInput) -> ToolResult:
    _, checkout, _ = _review_paths(ctx)
    argv = ["git", "-C", checkout, "grep", "--line-number", "-I"]
    if args.ignore_case:
        argv.append("--ignore-case")
    argv.extend(("-e", args.pattern, "HEAD", "--"))
    if args.glob is not None:
        argv.append(f":(glob){args.glob}")
    matched = await _review_exec(ctx, *argv[:4], "--quiet", *argv[4:])
    if matched.exit_code == 1:
        text = "(no matches)"
    elif matched.exit_code != 0:
        raise ValueError("review grep pattern is invalid")
    else:
        result = await _review_exec(
            ctx,
            "sh",
            "-c",
            BOUNDED_LINES_COMMAND,
            "sh",
            str(REVIEW_LINE_CHAR_LIMIT),
            str(args.head_limit + 1),
            *argv[1:],
        )
        if result.exit_code != 0:
            raise RuntimeError("git could not search review files")
        lines = result.stdout.splitlines()
        bounded = [line.removeprefix("HEAD:") for line in lines[: args.head_limit]]
        text = "\n".join(bounded)
        if len(lines) > len(bounded):
            text += "\n[more matches omitted]"
    return ToolResult(content=(TextContent(text=text),), untrusted=True)


CODE_REVIEW_TOOL = ToolDef(
    name=CODE_REVIEW_TOOL_NAME,
    description="Validate and prepare the exact pull request comparison locally.",
    input_model=CheckoutCodeReviewInput,
    handler=checkout_code_review,
    untrusted=True,
    side_effecting=True,
    profile_only=True,
)

CODE_REVIEW_READ_TOOL = ToolDef(
    name=CODE_REVIEW_READ_TOOL_NAME,
    description="Read tracked text or the exact comparison diff from this review checkout.",
    input_model=ReviewReadInput,
    handler=review_read,
    untrusted=True,
    profile_only=True,
)

CODE_REVIEW_GLOB_TOOL = ToolDef(
    name=CODE_REVIEW_GLOB_TOOL_NAME,
    description="List tracked paths in this review checkout by glob.",
    input_model=ReviewGlobInput,
    handler=review_glob,
    untrusted=True,
    profile_only=True,
)

CODE_REVIEW_GREP_TOOL = ToolDef(
    name=CODE_REVIEW_GREP_TOOL_NAME,
    description="Search tracked text in this review checkout by regex.",
    input_model=ReviewGrepInput,
    handler=review_grep,
    untrusted=True,
    profile_only=True,
)

CODE_REVIEW_TOOLS = (
    CODE_REVIEW_TOOL,
    CODE_REVIEW_READ_TOOL,
    CODE_REVIEW_GLOB_TOOL,
    CODE_REVIEW_GREP_TOOL,
)

CODE_REVIEW_PROFILE = SubagentProfile(
    name=CODE_REVIEW_PROFILE_NAME,
    prompt=CODE_REVIEW_PROMPT,
    tool_names=tuple(tool.name for tool in CODE_REVIEW_TOOLS),
    input_model=ExactComparison,
    output_model=CodeReviewOutput,
    untrusted_output=True,
    isolated_tools=True,
)
