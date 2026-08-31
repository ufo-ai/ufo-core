from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from evals.harness.capability import (
    CapabilityOutput,
    CapabilityVerdict,
    Grader,
    ToolInvocation,
    grading_statement,
)
from evals.harness.harness import JsonObject
from ufo.host.tools.builtins import SPAWN_DETACHED_LEAD, SPAWN_MOVED_LEAD

CODING_LANE = "coding"
BACKGROUND_ACKS = (SPAWN_DETACHED_LEAD, SPAWN_MOVED_LEAD)
CLONE_VERBS = ("git clone", "gh repo clone")
SHELL_SEPARATORS = re.compile(r"&&|\|\||;|\n")
ROUTE_ARGUMENTS = ("command", "code", "url", "tool_name")
FETCH_VERB = "fetch"
MIN_SHA_PREFIX = 7
REDIRECTION = re.compile(r"\d*[<>].*")
SPAWN_TOOL = "spawn"


@dataclass(frozen=True)
class AllOf:
    """Every gate, in order, with each reason kept — a failure names the gate that failed and the
    evidence of the gates that passed.

    A gate the harness could not put its question to carries its exclusion out: dropping it here
    would score the harness's own inability to run as the candidate's failure."""

    graders: tuple[Grader, ...]

    @property
    def grading(self) -> str:
        return "; ".join(
            statement for statement in map(grading_statement, self.graders) if statement
        )

    async def __call__(self, output: CapabilityOutput) -> CapabilityVerdict:
        evidence: JsonObject = {}
        reasons: list[str] = []
        passed = True
        excluded = False
        for grader in self.graders:
            verdict = await grader(output)
            evidence |= verdict.evidence
            reasons.append(verdict.reason)
            passed = passed and verdict.passed
            excluded = excluded or verdict.excluded
        return CapabilityVerdict(passed, "; ".join(reasons), evidence, excluded)


@dataclass(frozen=True)
class PinnedRepositoryRoute:
    """The lane and the route: the work went to a coding child that returned a result, that child
    fetched the pinned commit, and nothing reached the repository by a route yielding no history.

    Only `ROUTE_ARGUMENTS` are read, never the prose an agent writes beside them: a relayed
    envelope, a note quoting a forbidden URL, and a search for the phrase reach nothing. A clone is
    refused where one shell segment names both the verb and this repository as the source, which
    carries every commit after the pin; a clone from a local path carries only what that path holds,
    which is the pinned checkout the coding skill copies for a second child. A refusal reads what a
    call attempted; the pin is credited when a fetch succeeds or a later Git command proves HEAD is
    at that commit. Any coding spawn may be the one that delivered, since a first child can fail and
    a second succeed, and a spawn that runs in the background — asked for or moved there by an
    arriving message — returns an acknowledgement naming the spawn it reaches rather than anything
    the child did."""

    repository_slug: str
    base_sha: str

    @property
    def grading(self) -> str:
        return (
            f"delegates to the {CODING_LANE!r} subagent, works at commit {self.base_sha[:12]}, and "
            "reaches the repository by no archive or contents-API route"
        )

    async def __call__(self, output: CapabilityOutput) -> CapabilityVerdict:
        spawns = tuple(call for call in output.calls if call.name == SPAWN_TOOL)
        if not spawns:
            return CapabilityVerdict(False, "did not delegate")
        lanes = tuple(
            str(
                call.input.get("target") or call.input.get("subagent_type") or "subagent"
            ).removeprefix("profile:")
            for call in spawns
        )
        if CODING_LANE not in lanes:
            return CapabilityVerdict(False, f"delegated to {', '.join(lanes)}, not {CODING_LANE!r}")
        coding = tuple(
            call for call, lane in zip(spawns, lanes, strict=True) if lane == CODING_LANE
        )
        delivered = tuple(
            call
            for call in coding
            if call.succeeded and not call.result.startswith(BACKGROUND_ACKS)
        )
        if not delivered:
            last = coding[-1]
            detail = last.result[:160] if last.has_result else "no result"
            return CapabilityVerdict(False, f"no coding delegation returned a result: {detail}")
        acting = tuple(
            (
                call,
                " ".join(str(call.input[name]) for name in ROUTE_ARGUMENTS if name in call.input),
            )
            for call in output.calls
        )
        forbidden_routes = (
            f"{self.repository_slug}/zipball",
            f"{self.repository_slug}/tarball",
            f"{self.repository_slug}/archive/",
            f"{self.repository_slug}/contents/",
            f"codeload.github.com/{self.repository_slug}",
            f"raw.githubusercontent.com/{self.repository_slug}",
            "REPOSITORY_ARCHIVE",
            "RAW_REPOSITORY_CONTENT",
        )
        forbidden = sorted(
            {route for route in forbidden_routes for _, target in acting if route in target}
        )
        if forbidden:
            return CapabilityVerdict(
                False, f"reached the repository by a historyless route: {', '.join(forbidden)}"
            )
        if any(self._clones_this_repository(target) for _, target in acting):
            return CapabilityVerdict(
                False,
                "cloned the repository, which carries the commits after the pin in its history",
            )
        if not any(
            call.succeeded
            and (
                (FETCH_VERB in target and self.base_sha in target)
                or self._proves_head(call, target)
            )
            for call, target in acting
        ):
            return CapabilityVerdict(
                False, f"no call fetches or proves the pinned commit {self.base_sha[:12]}"
            )
        return CapabilityVerdict(
            True,
            f"delegated to {CODING_LANE!r} at {self.base_sha[:12]}",
            {"lanes": list(lanes), "codingSpawns": len(coding), "delivered": len(delivered)},
        )

    def _clones_this_repository(self, target: str) -> bool:
        clone_sources = (
            f"github.com/{self.repository_slug}",
            f"github.com:{self.repository_slug}",
            f"clone {self.repository_slug}",
        )
        return any(
            any(verb in segment for verb in CLONE_VERBS)
            and any(source in segment for source in clone_sources)
            for segment in SHELL_SEPARATORS.split(target)
        )

    def _proves_head(self, call: ToolInvocation, target: str) -> bool:
        commands = tuple(
            segment.strip()
            for segment in SHELL_SEPARATORS.split(target)
            if segment.strip() and not segment.strip().startswith("cd ")
        )
        if not commands or self.base_sha in commands[0]:
            return False
        try:
            command = shlex.split(commands[0].split("|", maxsplit=1)[0])
        except ValueError:
            return False
        if len(command) > 2 and command[1] == "-C":
            command = [command[0], *command[3:]]
        if len(command) < 2 or command[0] != "git":
            return False
        args = tuple(word for word in command[2:] if REDIRECTION.fullmatch(word) is None)
        match command[1]:
            case "rev-parse":
                proves_head = args in (("HEAD",), ("--verify", "HEAD"))
            case "log":
                one_commit = "-1" in args or "--max-count=1" in args
                max_count = args.index("--max-count") if "--max-count" in args else -1
                one_commit = one_commit or (
                    max_count >= 0 and len(args) > max_count + 1 and args[max_count + 1] == "1"
                )
                revisions = tuple(
                    word
                    for index, word in enumerate(args)
                    if not word.startswith("-")
                    and not (index > 0 and args[index - 1] == "--max-count")
                )
                proves_head = one_commit and revisions in ((), ("HEAD",))
            case "show":
                revisions = tuple(word for word in args if not word.startswith("-"))
                proves_head = revisions == ("HEAD",)
            case _:
                return False
        if not proves_head:
            return False
        words = call.result.split(maxsplit=1)
        if not words:
            return False
        first = words[0].lower()
        return (
            len(first) >= MIN_SHA_PREFIX
            and re.fullmatch(r"[0-9a-f]{7,40}", first) is not None
            and self.base_sha.startswith(first)
        )
