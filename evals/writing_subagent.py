"""Writing-subagent cases over short social copy."""

from __future__ import annotations

import re
from dataclasses import dataclass

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    WorkspaceFile,
)
from evals.harness.harness import JsonObject
from evals.harness.scorers import combine, lane_scorer

TWEET_RE = re.compile(r"^\s*[1-3][.)]\s+(.+?)\s*$", re.MULTILINE)
THREAD_POST_RE = re.compile(
    r"^\s*([1-5])/5\s+(.+?)(?=^\s*(?:[1-5]/5|\[)|\Z)", re.MULTILINE | re.DOTALL
)
WORD_RE = re.compile(r"[a-z0-9]+(?:['\u2019][a-z]+)?", re.IGNORECASE)
STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "this",
        "to",
        "up",
        "we",
        "with",
        "you",
        "your",
    }
)
BANNED_PATTERNS = (
    "delve",
    "game changer",
    "paradigm shift",
    "this is huge",
    "this changes everything",
    "it's not just",
    "this isn't just",
    "the detail that makes it work:",
    "we're thrilled",
    "—",
    "!",
    "#",
)
MAX_TWEET_CHARS = 280
MIN_TWEET_WORDS = 8
MIN_CONTENT_DENSITY = 0.45
MIN_LEXICAL_DIVERSITY = 0.4
MAX_PAIRWISE_OVERLAP = 0.75
MIN_OPENING_DIVERSITY = 1.0
MIN_THREAD_CONTENT_DENSITY = 0.5
MIN_THREAD_LEXICAL_DIVERSITY = 0.55
MAX_THREAD_PAIRWISE_OVERLAP = 0.5
LAUNCH_THREAD_PATH = "drafts/ufo-launch-thread.md"
LAUNCH_MEDIA = (
    "[they will never see you coming embed]",
    "[some hud of parallel complexity]",
)
LAUNCH_BANNED_PATTERNS = (
    "every company, project, and person is unique",
    "today we're announcing",
    "today we are announcing",
    "today we announce ufo.ai",
    "today we release ufo.ai",
    "today we ",
)


@dataclass(frozen=True)
class TweetLanguageGrader:
    anchors: tuple[tuple[str, ...], ...]
    required_tools: tuple[str, ...] = ()

    @property
    def grading(self) -> str:
        return (
            "exactly three numbered tweets under 280 characters; required facts present; no banned "
            "AI-writing patterns; sufficient content-word density and lexical diversity; no two "
            "options repeat most of their content words"
        )

    async def __call__(self, output: CapabilityOutput) -> CapabilityVerdict:
        tweets = tuple(TWEET_RE.findall(output.response))
        failures: list[str] = []
        if len(tweets) != 3:
            failures.append(f"found {len(tweets)} numbered tweets")
        missing_tools = [
            name
            for name in self.required_tools
            if not any(call.name == name and call.succeeded for call in output.calls)
        ]
        if missing_tools:
            failures.append("missing successful " + ", ".join(missing_tools))

        lowered = "\n".join(tweets).casefold()
        missing_anchors = [
            "/".join(alternatives)
            for alternatives in self.anchors
            if not any(alternative.casefold() in lowered for alternative in alternatives)
        ]
        if missing_anchors:
            failures.append("missing " + ", ".join(missing_anchors))
        banned = [pattern for pattern in BANNED_PATTERNS if pattern in lowered]
        if banned:
            failures.append("banned " + ", ".join(repr(pattern) for pattern in banned))

        lengths = tuple(len(tweet) for tweet in tweets)
        too_long = [
            str(index) for index, length in enumerate(lengths, 1) if length > MAX_TWEET_CHARS
        ]
        if too_long:
            failures.append("over 280 characters: " + ", ".join(too_long))

        anchor_words = frozenset(
            word
            for alternatives in self.anchors
            for alternative in alternatives
            for word in _words(alternative)
        )
        words, density, diversity, overlap = _language_metrics(tweets, anchor_words)
        too_short = [
            str(index) for index, tokens in enumerate(words, 1) if len(tokens) < MIN_TWEET_WORDS
        ]
        if too_short:
            failures.append("under 8 words: " + ", ".join(too_short))
        openings = {tuple(tokens[:2]) for tokens in words if tokens}
        opening_diversity = len(openings) / len(tweets) if tweets else 0.0
        if density < MIN_CONTENT_DENSITY:
            failures.append(f"content density {density:.2f} below {MIN_CONTENT_DENSITY:.2f}")
        if diversity < MIN_LEXICAL_DIVERSITY:
            failures.append(f"lexical diversity {diversity:.2f} below {MIN_LEXICAL_DIVERSITY:.2f}")
        if overlap > MAX_PAIRWISE_OVERLAP:
            failures.append(f"pairwise overlap {overlap:.2f} above {MAX_PAIRWISE_OVERLAP:.2f}")
        if opening_diversity < MIN_OPENING_DIVERSITY:
            failures.append("tweet openings repeat")

        evidence: JsonObject = {
            "tweetCharacters": list(lengths),
            "contentDensity": round(density, 3),
            "lexicalDiversity": round(diversity, 3),
            "maxPairwiseOverlap": round(overlap, 3),
            "openingDiversity": round(opening_diversity, 3),
        }
        if failures:
            return CapabilityVerdict(False, "; ".join(failures), evidence)
        return CapabilityVerdict(True, "three distinct, information-dense tweets", evidence)


@dataclass(frozen=True)
class LaunchThreadGrader:
    @property
    def grading(self) -> str:
        return (
            "five numbered posts under 280 characters; both media placeholders and every launch "
            "fact preserved; no banned AI-writing patterns; sufficient content-word density and "
            "lexical diversity; bounded repetition between posts"
        )

    async def __call__(self, output: CapabilityOutput) -> CapabilityVerdict:
        matched = tuple(THREAD_POST_RE.findall(output.response))
        posts = tuple(re.sub(r"\s+", " ", text).strip() for _, text in matched)
        failures: list[str] = []
        if tuple(number for number, _ in matched) != ("1", "2", "3", "4", "5"):
            failures.append("missing ordered 1/5 through 5/5 posts")
        if not any(call.name == "read" and call.succeeded for call in output.calls):
            failures.append("missing successful read")
        if not any(call.name in {"edit", "write"} and call.succeeded for call in output.calls):
            failures.append("missing successful edit or write")

        lowered = output.response.casefold().replace("\u2019", "'")
        missing_media = [marker for marker in LAUNCH_MEDIA if marker not in lowered]
        if missing_media:
            failures.append("missing media " + ", ".join(missing_media))
        expected_order = (
            lowered.find("1/5"),
            lowered.find(LAUNCH_MEDIA[0]),
            lowered.find("2/5"),
            lowered.find("3/5"),
            lowered.find("4/5"),
            lowered.find(LAUNCH_MEDIA[1]),
            lowered.find("5/5"),
        )
        if -1 not in expected_order and expected_order != tuple(sorted(expected_order)):
            failures.append("media placeholders moved from their intended posts")
        anchors = (
            ("ufo.ai",),
            ("builds your most important work", "builds important work"),
            ("today",),
            ("frontier",),
            ("multiplayer",),
            ("slack",),
            ("terminal",),
            ("web",),
            ("chatgpt work",),
            ("claude tag",),
            ("managed agents",),
            ("self-aware",),
            ("self-improving",),
            ("connective cognitive layer",),
            ("recruiting",),
            ("marketing",),
            ("system monitoring",),
            ("experiments",),
            ("deploys",),
            ("competitive analysis",),
            ("feature dev", "feature development"),
            ("30m", "$30m", "30 million", "$30 million"),
            ("yc",),
            ("garry tan",),
            ("transpose platform",),
            ("perplexity fund",),
            ("michael ovitz",),
            ("blake byers",),
            ("alex@ufo.ai",),
        )
        missing_anchors = [
            "/".join(alternatives)
            for alternatives in anchors
            if not any(alternative in lowered for alternative in alternatives)
        ]
        if missing_anchors:
            failures.append("missing " + ", ".join(missing_anchors))
        normalized_launch = lowered.replace(",", "")
        banned = [pattern for pattern in BANNED_PATTERNS if pattern in lowered]
        banned.extend(
            pattern
            for pattern in LAUNCH_BANNED_PATTERNS
            if pattern.replace(",", "") in normalized_launch
        )
        if banned:
            failures.append("banned " + ", ".join(repr(pattern) for pattern in banned))

        lengths = tuple(len(post) for post in posts)
        too_long = [
            str(index) for index, length in enumerate(lengths, 1) if length > MAX_TWEET_CHARS
        ]
        if too_long:
            failures.append("over 280 characters: " + ", ".join(too_long))
        _, density, diversity, overlap = _language_metrics(posts)
        if density < MIN_THREAD_CONTENT_DENSITY:
            failures.append(f"content density {density:.2f} below {MIN_THREAD_CONTENT_DENSITY:.2f}")
        if diversity < MIN_THREAD_LEXICAL_DIVERSITY:
            failures.append(
                f"lexical diversity {diversity:.2f} below {MIN_THREAD_LEXICAL_DIVERSITY:.2f}"
            )
        if overlap > MAX_THREAD_PAIRWISE_OVERLAP:
            failures.append(
                f"pairwise overlap {overlap:.2f} above {MAX_THREAD_PAIRWISE_OVERLAP:.2f}"
            )

        evidence: JsonObject = {
            "postCharacters": list(lengths),
            "contentDensity": round(density, 3),
            "lexicalDiversity": round(diversity, 3),
            "maxPairwiseOverlap": round(overlap, 3),
        }
        if failures:
            return CapabilityVerdict(False, "; ".join(failures), evidence)
        return CapabilityVerdict(True, "complete, information-dense five-post thread", evidence)


def _words(text: str) -> tuple[str, ...]:
    return tuple(match.group(0).casefold() for match in WORD_RE.finditer(text))


def _language_metrics(
    passages: tuple[str, ...],
    comparison_exclusions: frozenset[str] = frozenset(),
) -> tuple[tuple[tuple[str, ...], ...], float, float, float]:
    words = tuple(_words(passage) for passage in passages)
    content_sets = tuple(
        frozenset(
            word for word in tokens if word not in STOP_WORDS and word not in comparison_exclusions
        )
        for tokens in words
    )
    all_words = tuple(token for tokens in words for token in tokens)
    density_words = tuple(token for token in all_words if token not in STOP_WORDS)
    comparison_words = tuple(token for token in density_words if token not in comparison_exclusions)
    density = len(density_words) / len(all_words) if all_words else 0.0
    diversity = len(set(comparison_words)) / len(comparison_words) if comparison_words else 0.0
    overlap = max(
        (
            len(left & right) / len(left | right)
            for index, left in enumerate(content_sets)
            for right in content_sets[index + 1 :]
            if left or right
        ),
        default=0.0,
    )
    return words, density, diversity, overlap


def tweet_scorer(
    anchors: tuple[tuple[str, ...], ...], required_tools: tuple[str, ...] = ()
) -> Grader:
    language = TweetLanguageGrader(anchors, required_tools)
    return combine(
        lane_scorer(frozenset({"writing"})),
        DescribedGrader(language.grading, language),
    )


SOURCE_DRAFT = WorkspaceFile(
    "drafts/export-tweets.md",
    (
        b"1. Today's CSV export for Pro isn't just a feature. It's a paradigm shift for teams "
        b"that need up to 100,000 rows.\n"
        b"2. The detail that makes today's Pro CSV export work: up to 100,000 rows at once.\n"
        b"3. We're thrilled to empower Pro teams with robust CSV exports of up to 100,000 rows, "
        b"available today.\n"
    ),
)

LAUNCH_THREAD_DRAFT = WorkspaceFile(
    LAUNCH_THREAD_PATH,
    "\n\n".join(
        (
            "Every company, project, and person is unique. Today we're announcing UFO.ai - an AI "
            "system that builds your most important work.",
            "[they will never see you coming embed]",
            "—",
            "UFO Version 1 released today is a frontier hosted multiplayer AI harness on slack, "
            "terminal, and web. This brings us to parity with products like ChatGPT Work, Claude "
            "Tag, and managed agents.",
            "—",
            "Beyond the frontier, UFO is self-aware and self-improving. Not as an employee to "
            "manage - as the connective cognitive layer between systems. UFO responds to events "
            "and makes decisions that allow you to scale your attention for what matters.",
            "As an example the UFO behind UFO.ai handles our recruiting, marketing, system "
            "monitoring, experiments, deploys, competitive analysis and feature dev.",
            "[some hud of parallel complexity]",
            "—",
            "We're excited to announce our microteam has raised 30M from YC and Garry Tan, "
            "Transpose Platform, Perplexity Fund, Michael Ovitz, and Blake Byers.",
            "Reach out if you'd like to work with us: alex@ufo.ai",
        )
    ).encode(),
)

CASES = (
    CapabilityCase(
        "writing-subagent-draft-tweets",
        "Use the writing subagent to draft three tweet options for my account. We shipped CSV "
        "export today for Pro workspaces, with up to 100,000 rows per export. I sound plainspoken "
        "and skeptical of launch hype. Do not invent results or customer claims. No hashtags. Put "
        "each option on one line prefixed 1., 2., and 3.",
        tweet_scorer((("csv",), ("100,000", "100k"), ("pro",), ("today",))),
        samples=3,
        digest_tag="writing:subagent-draft-tweets",
        rubric=(
            "Across the set, the options accurately preserve CSV export, availability today, Pro "
            "workspaces, and the 100,000-row limit without adding a result, customer claim, or "
            "unsupported feature; an option need not repeat every fact.",
            "The options sound like a plainspoken founder who dislikes launch hype, not polished "
            "marketing copy or an assistant describing the requested voice.",
            "The three options take meaningfully different angles or sentence shapes while keeping "
            "the same facts.",
        ),
    ),
    CapabilityCase(
        "writing-subagent-edit-tweets",
        "Use the writing subagent to edit /workspace/drafts/export-tweets.md in place, then paste "
        "the full revision here. Keep three tweet options and the facts already in the draft. Make "
        "them blunt and human. Each option must still stand alone with CSV export, Pro, up to "
        "100,000 rows, and availability today. Do not add facts or hashtags. Put each option on "
        "one line prefixed 1., 2., and 3.",
        tweet_scorer((("csv",), ("100,000", "100k"), ("pro",), ("today",)), ("read", "edit")),
        samples=3,
        digest_tag="writing:subagent-edit-tweets",
        workspace_files=(SOURCE_DRAFT,),
        rubric=(
            "Every revised option preserves only the source facts: CSV export, Pro teams, up to "
            "100,000 rows, and availability today.",
            "The revision removes the source's binary contrast, fake reveal, generic hype, and "
            "corporate tone while staying blunt and natural.",
            "The three options are usable tweets with distinct angles, not commentary about how "
            "the draft was edited.",
        ),
    ),
)

LAUNCH_CASES = (
    CapabilityCase(
        "writing-subagent-edit-launch-thread",
        f"Use the writing subagent to edit /workspace/{LAUNCH_THREAD_PATH} in place, then paste "
        "the full revision here. It is a five-post launch thread with two media placeholders. "
        "Keep the primary product claim that UFO.ai is an AI system that builds your most "
        "important work, and keep that Version 1 released today. "
        "Preserve every product, capability, company-use, funding, investor, and recruiting claim, "
        "plus both placeholders. Tighten it without sanding off the founder's ambition or turning "
        "it into launch hype. Cut generic setup and repeated announcement language. "
        "Each text post must be at most 280 characters and start with 1/5 through 5/5. Keep each "
        "media placeholder unchanged on its own line: the embed goes between 1/5 and 2/5, and the "
        "HUD goes between 4/5 and 5/5. Use no hashtags, "
        "emoji, exclamation points, or em dashes.",
        combine(
            lane_scorer(frozenset({"writing"})),
            DescribedGrader(LaunchThreadGrader().grading, LaunchThreadGrader()),
        ),
        samples=3,
        digest_tag="writing:subagent-edit-launch-thread",
        workspace_files=(LAUNCH_THREAD_DRAFT,),
        rubric=(
            "The source explicitly says UFO.ai is an AI system that builds important work. "
            "Equivalent wording preserves that claim and is not an addition. The revision also "
            "preserves these supplied claims without adding or strengthening one: Version 1 "
            "released today as a frontier "
            "hosted multiplayer AI harness on Slack, terminal, and web; it claims parity with "
            "ChatGPT Work, Claude Tag, and managed agents; UFO is self-aware and self-improving, a "
            "connective cognitive layer that responds to events and makes decisions; the company "
            "uses it for recruiting, marketing, system monitoring, experiments, deploys, "
            "competitive analysis, and feature development; the microteam raised 30M from every "
            "named investor; alex@ufo.ai is the recruiting contact. The source also contains the "
            "generic opening 'Every company, project, and person is unique,' which may be cut. "
            "Do not grade media markers or formatting in this criterion.",
            "The thread has a coherent launch arc across five standalone posts: what UFO is, the "
            "surfaces and comparison set, the self-improving thesis, concrete internal uses, then "
            "funding and the recruiting invitation.",
            "The writing sounds ambitious, specific, and human while removing repetition, generic "
            "announcement language, and awkward fragments. Do not penalize the supplied terms "
            "'frontier hosted multiplayer AI harness,' 'self-aware,' 'self-improving,' or "
            "'connective cognitive layer' merely for being ambitious product language. Do not "
            "grade media markers, numbering, formatting, or tool use in this criterion.",
        ),
    ),
)
