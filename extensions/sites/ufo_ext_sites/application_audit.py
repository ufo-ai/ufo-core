"""Typed deterministic acceptance for an interactive ufo application: the browser report the
audit script writes, the verdict a deploy refuses or hosts on, and the source and design
validators that run on the bytes before the build.

`deploy_website` is the one caller. A page reaches a member only through it, so this is where
every rule about what an app page may be is true."""

import re
from dataclasses import dataclass
from math import isfinite
from typing import Annotated, Literal
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field, model_validator

DESKTOP_WIDTH = 1440
NARROW_WIDTH = 360
APPLICATION_DESIGN_FOLD = 844
APPLICATION_DESIGN_MAX_HEIGHT = 4_096
SCHEMES: tuple[Literal["light", "dark"], ...] = ("light", "dark")
MEASURED_VIEWS = tuple(
    (scheme, width) for width in (DESKTOP_WIDTH, NARROW_WIDTH) for scheme in SCHEMES
)
AA_BODY = 4.5
AA_LARGE = 3.0
KIT_QUIET_TEXT_MIN = 2.8
KitQuietTextSlot = Literal["badge", "chart-caption", "stat-label"]
LARGE_PX = 24.0
LARGE_BOLD_PX = 18.66
BOLD_WEIGHT = 700
MIN_CONTROLS = 2
MIN_INTERACTIONS = 2
DESIGN_REGION_MIN = 2
DESIGN_REGION_MAX = 6
DESIGN_VISIBLE_TEXT_MAX_CHARS = 72
DESIGN_REGION_SEPARATION_SLOP = 0.02
DESIGN_REGION_FOLD_SLOP = 2
APPLICATION_REGION_MIN_WIDTH = 0.04
APPLICATION_REGION_MIN_HEIGHT = 0.03
APPLICATION_REGION_MIN_AREA = 0.008
MAX_ISSUES = 8
MAX_MESSAGE_CHARS = 500
MAX_PRODUCT_QA_CONTROLS = 100
APPLICATION_DESIGN_MAX_CHARS = 128_000
APPLICATION_SOURCE_MAX_CHARS = 256_000
APPLICATION_DESIGN_WIDTH = NARROW_WIDTH
"""The design lane is the narrow view the fidelity check measures. One width for both is what
lets a region drawn above y=844 be compared with the region rendered above 844 px without a
scale between them."""
APPLICATION_DESIGN_EFFECT_ERROR = (
    "application design native bounds do not support clip, mask, or filter effects"
)
APPLICATION_DESIGN_EFFECT_STYLE = re.compile(
    r"(?:^|[;{])\s*(?:-(?:moz|webkit)-)?(?:clip-path|filter|mask(?:-image)?)\s*:\s*([^;}]+)",
    re.IGNORECASE,
)
SVG_DRAWING_ELEMENTS = frozenset(
    {"circle", "ellipse", "image", "line", "path", "polygon", "polyline", "rect", "text", "use"}
)
AuditTerm = Annotated[str, Field(min_length=1, max_length=200)]
AuditIssueCode = Literal[
    "source",
    "build",
    "lifecycle",
    "missing_view",
    "empty_view",
    "contrast",
    "overflow",
    "clipping",
    "overlap",
    "design",
    "console",
    "controls",
    "interaction",
    "fact",
    "above_fold",
]


class ApplicationAuditFact(BaseModel):
    """One required fact and the equivalent strings that can prove it."""

    model_config = ConfigDict(frozen=True)

    label: AuditTerm
    alternatives: tuple[AuditTerm, ...] = Field(min_length=1, max_length=10)


class ApplicationAuditContract(BaseModel):
    """The fixed facts that a connected application must show."""

    model_config = ConfigDict(frozen=True)

    facts: tuple[ApplicationAuditFact, ...] = Field(default=(), max_length=100)


class ApplicationAuditText(BaseModel):
    """One rendered text style that falls under the strict contrast floor."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(default="", max_length=48)
    selector: str
    px: float
    weight: int
    ratio: float
    colour: str = ""
    background: str = ""
    slot: KitQuietTextSlot | None = None


class ApplicationAuditOverlap(BaseModel):
    """One accidental intersection between independent visible content."""

    model_config = ConfigDict(frozen=True)

    first: str = Field(min_length=1, max_length=160)
    second: str = Field(min_length=1, max_length=160)
    width: float = Field(gt=0, le=10000)
    height: float = Field(gt=0, le=10000)


class ApplicationAuditRegion(BaseModel):
    """One visible semantic region measured in a design or application view."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    name: str = Field(min_length=1, max_length=80)
    left: float = Field(ge=0, le=1)
    top: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    above_fold: bool = Field(default=True, alias="aboveFold")
    visible_text: str = Field(
        default="", max_length=DESIGN_VISIBLE_TEXT_MAX_CHARS, alias="visibleText"
    )


class AcceptedApplicationDesignEvidence(BaseModel):
    """The accepted design digest and its validated rendered regions."""

    model_config = ConfigDict(frozen=True)

    version: Literal[1] = 1
    design_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kit_components: tuple[str, ...] = Field(min_length=1)
    regions: tuple[ApplicationAuditRegion, ...] = Field(
        min_length=DESIGN_REGION_MIN, max_length=DESIGN_REGION_MAX
    )

    @model_validator(mode="after")
    def regions_are_unique(self) -> "AcceptedApplicationDesignEvidence":
        names = tuple(region.name for region in self.regions)
        if len(names) != len(set(names)):
            raise ValueError("accepted application design regions must be unique")
        if len(self.kit_components) != len(set(self.kit_components)):
            raise ValueError("accepted application design Kit components must be unique")
        return self


class ApplicationAuditView(BaseModel):
    """The browser measurements for one colour scheme and viewport width.

    `page_height` is the scrolled page height this view's region fractions divide by.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    scheme: Literal["light", "dark"]
    width: int
    text_checked: int = Field(alias="textChecked", ge=0)
    text: tuple[ApplicationAuditText, ...]
    document_width: int = Field(alias="documentWidth", ge=0)
    page_height: int = Field(default=APPLICATION_DESIGN_FOLD, alias="pageHeight", ge=1)
    clipped: tuple[str, ...]
    overhangs: tuple[AuditTerm, ...] = Field(default=(), max_length=8)
    overlaps: tuple[ApplicationAuditOverlap, ...] = Field(max_length=8)
    console: tuple[str, ...]
    above_fold_text: str = Field(alias="aboveFoldText")
    regions: tuple[ApplicationAuditRegion, ...] = Field(default=(), max_length=20)


class ApplicationAuditControl(BaseModel):
    """One accessible control found or proved by the browser."""

    model_config = ConfigDict(frozen=True)

    selector: str
    name: str


class ApplicationAuditInteraction(BaseModel):
    """Independent browser interaction evidence from fresh page contexts."""

    model_config = ConfigDict(frozen=True)

    controls: tuple[ApplicationAuditControl, ...]
    successes: tuple[ApplicationAuditControl, ...]
    states: tuple[tuple[str, ...], ...] = ()
    console: tuple[str, ...]


class ApplicationAuditReport(BaseModel):
    """The complete deterministic browser report for one staged application."""

    model_config = ConfigDict(frozen=True)

    url: str = ""
    floor: float = AA_BODY
    design_height: int = Field(
        default=APPLICATION_DESIGN_FOLD,
        alias="designHeight",
        ge=APPLICATION_DESIGN_FOLD,
        le=APPLICATION_DESIGN_MAX_HEIGHT,
    )
    design_regions: tuple[ApplicationAuditRegion, ...] = Field(
        default=(), alias="designRegions", max_length=20
    )
    views: tuple[ApplicationAuditView, ...]
    interaction: ApplicationAuditInteraction

    @model_validator(mode="after")
    def views_are_unique(self) -> "ApplicationAuditReport":
        keys = tuple((view.scheme, view.width) for view in self.views)
        if len(keys) != len(set(keys)):
            raise ValueError("application audit views must be unique")
        return self


class ApplicationAuditIssue(BaseModel):
    """One repair item from a fixed product check."""

    model_config = ConfigDict(frozen=True)

    code: AuditIssueCode
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    terms: tuple[AuditTerm, ...] = Field(default=(), max_length=10)


class ApplicationAuditVerdict(BaseModel):
    """One bounded diagnostic batch returned to the active builder."""

    model_config = ConfigDict(frozen=True)

    issues: tuple[ApplicationAuditIssue, ...] = Field(max_length=MAX_ISSUES)

    @property
    def passed(self) -> bool:
        """True when every deterministic check passes."""

        return not self.issues


class ApplicationAuditFeedback(BaseModel):
    """The failed attempt and repair items returned to the active builder."""

    model_config = ConfigDict(frozen=True)

    status: Literal["repair_required"] = "repair_required"
    attempt: int = Field(ge=1)
    attempts_remaining: int = Field(ge=0)
    issues: tuple[ApplicationAuditIssue, ...] = Field(min_length=1, max_length=MAX_ISSUES)


class ApplicationProductQaResult(BaseModel):
    """The deterministic product proof returned to the application worker."""

    model_config = ConfigDict(frozen=True)

    status: Literal["passed"] = "passed"
    views_checked: tuple[str, ...] = Field(min_length=4, max_length=4)
    controls_checked: tuple[str, ...] = Field(
        min_length=MIN_CONTROLS, max_length=MAX_PRODUCT_QA_CONTROLS
    )
    interactions_verified: tuple[str, ...] = Field(
        min_length=MIN_INTERACTIONS, max_length=MAX_PRODUCT_QA_CONTROLS
    )


class ApplicationQaProof(BaseModel):
    """The exact application source accepted by deterministic product QA."""

    model_config = ConfigDict(frozen=True)

    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    browser_batches: int = Field(ge=1, le=3)


@dataclass(frozen=True)
class ApplicationDesign:
    """One validated application design: the regions it names, the kit components it draws, and the
    lane height it was drawn at."""

    regions: tuple[str, ...]
    kit_components: tuple[str, ...]
    height: int


@dataclass(frozen=True)
class ApplicationDesignFidelity:
    """The deterministic score and failures for one accepted SVG implementation."""

    passed: int
    total: int
    failures: tuple[str, ...]


def _needed_ratio(item: ApplicationAuditText) -> float:
    if item.slot is not None:
        return KIT_QUIET_TEXT_MIN
    if item.px >= LARGE_PX or (item.px >= LARGE_BOLD_PX and item.weight >= BOLD_WEIGHT):
        return AA_LARGE
    return AA_BODY


def _issue(
    code: AuditIssueCode, message: str, terms: tuple[str, ...] = ()
) -> ApplicationAuditIssue:
    return ApplicationAuditIssue(
        code=code,
        message=message[:MAX_MESSAGE_CHARS],
        terms=terms[:10],
    )


def application_first_screen_scale(page_height: int) -> float:
    """Return the fraction of one measured page that spans its first 844 px screen.

    A region's top and height are fractions of the whole page, so every vertical threshold is
    written against the 844 px first screen and shrinks by this factor on a taller page. One
    threshold then holds one pixel size at every page height the design gate accepts. Horizontal
    thresholds stay unscaled because the page width is fixed: the design lane and the narrow view
    are both 360 px, and a desktop view is its own measured width.
    """

    return APPLICATION_DESIGN_FOLD / page_height


def application_region_relation(
    first: ApplicationAuditRegion,
    second: ApplicationAuditRegion,
    page_height: int = APPLICATION_DESIGN_FOLD,
) -> tuple[Literal["horizontal", "vertical"], int] | None:
    """Return the rendered separation axis and order for two regions of one measured page.

    Pass the height of the page the two regions were normalized against, so the near-touch
    allowance stays one pixel gap instead of growing with the page.
    """

    vertical_slop = DESIGN_REGION_SEPARATION_SLOP * application_first_screen_scale(page_height)
    if first.top + first.height <= second.top + vertical_slop:
        return ("vertical", -1)
    if second.top + second.height <= first.top + vertical_slop:
        return ("vertical", 1)
    if first.left + first.width <= second.left + DESIGN_REGION_SEPARATION_SLOP:
        return ("horizontal", -1)
    if second.left + second.width <= first.left + DESIGN_REGION_SEPARATION_SLOP:
        return ("horizontal", 1)
    return None


def application_design_region_size_failure(
    regions: tuple[ApplicationAuditRegion, ...],
    page_height: int = APPLICATION_DESIGN_FOLD,
) -> str | None:
    """Return the first accepted design region that is too small to be a useful screen region.

    Region sizes are fractions of the whole design page, so the height and area floors are first
    screen fractions that hold one pixel size at every accepted page height.
    """

    first_screen = application_first_screen_scale(page_height)
    for region in regions:
        if (
            region.width < APPLICATION_REGION_MIN_WIDTH
            or region.height < APPLICATION_REGION_MIN_HEIGHT * first_screen
            or region.width * region.height < APPLICATION_REGION_MIN_AREA * first_screen
        ):
            return f"design region {region.name} is too small"
    return None


def application_design_region_fold_failure(
    regions: tuple[ApplicationAuditRegion, ...],
    page_height: int = APPLICATION_DESIGN_FOLD,
) -> str | None:
    """Return the first accepted design region that paints a band across the first-screen boundary.

    A region that meets the fold row from either side is on one side of it, and the design rule asks
    the builder for that layout. The audit measures a region as the painted pixel rows of the page
    divided by its height, so a rule, a stroke or an antialiased edge near the fold row paints rows
    on both sides of it. Recover those rows from the fractions and allow `DESIGN_REGION_FOLD_SLOP`
    design pixels either side, the width of the hair the design guidance draws with: a region is
    refused only when it paints past that allowance both above and below the fold.
    """

    for region in regions:
        first_row = round(region.top * page_height)
        last_row = round((region.top + region.height) * page_height) - 1
        if (
            first_row < APPLICATION_DESIGN_FOLD - DESIGN_REGION_FOLD_SLOP
            and last_row > APPLICATION_DESIGN_FOLD + DESIGN_REGION_FOLD_SLOP
        ):
            return f"design region {region.name} crosses the first-screen boundary"
    return None


def application_region_failure(
    scheme: str,
    name: str,
    measured: ApplicationAuditRegion | None,
    designed: ApplicationAuditRegion,
) -> str | None:
    """Why the built page does not carry this design region at 360 px, or None.

    A repair treats the two differently, and one message for both left a build guessing: a region
    the page never rendered is written or unhidden, a region below the fold is moved. Nothing can
    tell an absent region from one hidden behind a control that shows a single region at a time —
    neither is measured — so the first names both rather than implying the one."""
    if measured is None:
        return (
            f"{scheme} {NARROW_WIDTH}px measured no {name}: the page renders no such region, or "
            "hides it behind a control that shows one region at a time"
        )
    if designed.above_fold and not measured.above_fold:
        return (
            f"{scheme} {NARROW_WIDTH}px renders {name} below the fold, where the design puts "
            "it above"
        )
    return None


def application_design_fidelity(report: ApplicationAuditReport) -> ApplicationDesignFidelity:
    """Measure named region identity, first-screen membership, and vertical order at 360 px.

    The design is one 360 px lane, so the lane is what it can be held to: the same width, the same
    844 px first screen, no scale between what was drawn and what was rendered. The desktop views
    keep every page check and carry no region verdict — a one-column lane and a 1440 px layout
    cannot share a fold, so comparing them measures nothing the designer chose.
    """

    design = report.design_regions
    design_names = tuple(region.name for region in design)
    if not (
        DESIGN_REGION_MIN <= len(design) <= DESIGN_REGION_MAX
        and len(set(design_names)) == len(design_names)
    ):
        return ApplicationDesignFidelity(
            passed=0,
            total=1,
            failures=(f"design has {len(design)} unique visible named regions",),
        )
    passed = 1
    total = 1
    failures = []
    design_by_name = {region.name: region for region in design}
    if size_failure := application_design_region_size_failure(design, report.design_height):
        return ApplicationDesignFidelity(passed=0, total=1, failures=(size_failure,))
    for first_index, first_name in enumerate(design_names):
        for second_name in design_names[first_index + 1 :]:
            if (
                application_region_relation(
                    design_by_name[first_name],
                    design_by_name[second_name],
                    report.design_height,
                )
                is None
            ):
                return ApplicationDesignFidelity(
                    passed=0,
                    total=1,
                    failures=(f"design regions {first_name} and {second_name} overlap",),
                )
    for scheme in SCHEMES:
        view = next(
            (
                candidate
                for candidate in report.views
                if candidate.scheme == scheme and candidate.width == NARROW_WIDTH
            ),
            None,
        )
        total += 1 + len(design)
        if view is None:
            failures.append(f"{scheme} {NARROW_WIDTH}px has no region measurement")
            continue
        app_names = tuple(region.name for region in view.regions)
        app_by_name = {region.name: region for region in view.regions}
        if len(app_by_name) == len(view.regions) and set(app_names) == set(design_names):
            passed += 1
        else:
            failures.append(f"{scheme} {NARROW_WIDTH}px region names differ")
        for name in design_names:
            failure = application_region_failure(
                scheme, name, app_by_name.get(name), design_by_name[name]
            )
            if failure is None:
                passed += 1
            else:
                failures.append(failure)
        for first_index, first_name in enumerate(design_names):
            for second_name in design_names[first_index + 1 :]:
                expected = application_region_relation(
                    design_by_name[first_name],
                    design_by_name[second_name],
                    report.design_height,
                )
                if expected is None:
                    continue
                total += 1
                first = app_by_name.get(first_name)
                second = app_by_name.get(second_name)
                if (
                    first is not None
                    and second is not None
                    and application_region_relation(first, second, view.page_height) == expected
                ):
                    passed += 1
                else:
                    failures.append(
                        f"{scheme} {NARROW_WIDTH}px changes {expected[0]} order for "
                        f"{first_name} and {second_name}"
                    )
    return ApplicationDesignFidelity(passed=passed, total=total, failures=tuple(failures))


def _contrast_failures(views: tuple[ApplicationAuditView, ...]) -> list[str]:
    failures: list[str] = []
    for view in views:
        for item in view.text:
            needed = _needed_ratio(item)
            if item.ratio >= needed:
                continue
            label = f' "{item.text}"' if item.text else ""
            colours = (
                f" {item.colour} on {item.background}" if item.colour and item.background else ""
            )
            failures.append(
                f"{view.scheme} {view.width}px{label} at {item.selector}{colours} "
                f"is {item.ratio}:1; needs {needed}:1"
            )
    return failures


def audit_application(
    report: ApplicationAuditReport,
    contract: ApplicationAuditContract | None = None,
) -> ApplicationAuditVerdict:
    """Apply the fixed page, interaction, and fact checks to one browser report.

    Fidelity is scored only where a design was measured. A page an app extension ships carries no
    wireframe beside it, and every other check still runs on it: what a design adds is a second
    contract, never the floor."""

    views = {(view.scheme, view.width): view for view in report.views}
    missing = tuple(
        f"{scheme} at {width}px" for scheme, width in MEASURED_VIEWS if (scheme, width) not in views
    )
    issues: list[ApplicationAuditIssue] = []
    if missing:
        issues.append(_issue("missing_view", f"Audit measures no {', '.join(missing)}."))
    measured = tuple(views[key] for key in MEASURED_VIEWS if key in views)
    empty = tuple(f"{view.scheme} {view.width}px" for view in measured if view.text_checked == 0)
    if empty:
        issues.append(_issue("empty_view", f"Audit read no text in {', '.join(empty)}."))
    contrast = _contrast_failures(measured)
    if contrast:
        issues.append(_issue("contrast", f"Fix text contrast: {'; '.join(contrast[:4])}."))
    overflow = tuple(
        (
            *(
                f"{view.scheme} {view.width}px document is {view.document_width}px"
                for view in measured
                if view.document_width > view.width
            ),
            *(
                f"{view.scheme} {view.width}px {item}"
                for view in measured
                for item in view.overhangs
            ),
        )
    )
    if overflow:
        issues.append(
            _issue("overflow", f"Fix horizontal overflow or overhang: {'; '.join(overflow[:4])}.")
        )
    clipped = tuple(
        f"{view.scheme} {view.width}px {item}" for view in measured for item in view.clipped
    )
    if clipped:
        issues.append(_issue("clipping", f"Fix clipped content: {'; '.join(clipped[:4])}."))
    overlaps = tuple(
        f"{view.scheme} {view.width}px {item.first} overlaps {item.second} by "
        f"{item.width:g}x{item.height:g}px"
        for view in measured
        for item in view.overlaps
    )
    if overlaps:
        issues.append(_issue("overlap", f"Fix accidental overlap: {'; '.join(overlaps[:4])}."))
    fidelity = application_design_fidelity(report)
    if report.design_regions and fidelity.failures:
        issues.append(
            _issue(
                "design",
                "Match the accepted design regions: " + "; ".join(fidelity.failures[:4]) + ".",
            )
        )
    console = tuple(
        dict.fromkeys(
            (
                *(problem for view in measured for problem in view.console),
                *report.interaction.console,
            )
        )
    )
    if console:
        issues.append(_issue("console", f"Fix browser errors: {'; '.join(console[:4])}."))
    controls = report.interaction.controls
    if len(controls) < MIN_CONTROLS:
        issues.append(
            _issue(
                "controls",
                f"Application exposes {len(controls)} accessible control(s), needs {MIN_CONTROLS}.",
            )
        )
    selectors = {success.selector for success in report.interaction.successes}
    if len(selectors) < MIN_INTERACTIONS:
        issues.append(
            _issue(
                "interaction",
                f"Application has {len(selectors)} distinct visible state change(s), needs "
                f"{MIN_INTERACTIONS}.",
            )
        )
    facts = () if contract is None else contract.facts
    state_text = " ".join(part for state in report.interaction.states for part in state).casefold()
    absent = tuple(
        fact
        for fact in facts
        if not any(alternative.casefold() in state_text for alternative in fact.alternatives)
    )
    if absent:
        issues.append(
            _issue(
                "fact",
                "Render these facts: " + "; ".join(fact.label for fact in absent[:10]) + ".",
                tuple(alternative for fact in absent for alternative in fact.alternatives),
            )
        )
    desktop = tuple(
        views[(scheme, DESKTOP_WIDTH)] for scheme in SCHEMES if (scheme, DESKTOP_WIDTH) in views
    )
    below_fold = tuple(
        (view.scheme, fact)
        for view in desktop
        for fact in facts
        if not any(
            alternative.casefold() in view.above_fold_text.casefold()
            for alternative in fact.alternatives
        )
    )
    if below_fold:
        issues.append(
            _issue(
                "above_fold",
                "Move these facts above the fold: "
                + "; ".join(f"{scheme} {fact.label}" for scheme, fact in below_fold[:10])
                + ".",
                tuple(alternative for _, fact in below_fold for alternative in fact.alternatives),
            )
        )
    return ApplicationAuditVerdict(issues=tuple(issues[:MAX_ISSUES]))


IMPORT_DECLARATION = re.compile(r"(?m)^[ \t]*import\b")
EXPORT_DECLARATION = re.compile(r"(?m)^[ \t]*export\b")
IMPORT_MODULE = re.compile(r"\bfrom\s*['\"]([^'\"]+)['\"]|\bimport\s*\(\s*['\"]([^'\"]+)['\"]")
SIDE_EFFECT_IMPORT = re.compile(r"(?m)^[ \t]*import\s*['\"]")
NON_NAMED_IMPORT = re.compile(r"(?m)^[ \t]*import\s+(?!type\s*\{|\{)")
SIBLING_MODULE = re.compile(r"\./[\w-]+\.(?:md\?raw|[a-z0-9]+\?url)")
SIBLING_IMPORT = re.compile(
    r"(?m)^[ \t]*import\s+[A-Za-z_$][\w$]*\s*from\s*['\"]"
    r"\./[\w-]+\.(?:md\?raw|[a-z0-9]+\?url)['\"]"
)
"""The one kind of module beside `ufo/kit` a page may name: a file committed next to `app.tsx` and
taken into the bundle at build time by vite's own `?raw` or `?url`. It resolves no package — the
project installs none — and the deploy's config carries every file of the project through the build,
so the document or asset ships with the page rather than being fetched at runtime. The shipped Radar
page reads its tour as `?raw` and the Artifacts page names its logo sheet as `?url`; a member's
redeploy of either page builds the same import again."""
NAMED_KIT_IMPORT = re.compile(
    r"(?ms)^[ \t]*import\s+(?P<type>type\s+)?\{(?P<names>[^{}]*)\}"
    r"\s*from\s*['\"]ufo/kit['\"]\s*;?"
)
KIT_IMPORT_NAME = re.compile(
    r"(?:(?P<type>type)\s+)?(?P<export>[A-Za-z_$][\w$]*)"
    r"(?:\s+as\s+(?P<local>[A-Za-z_$][\w$]*))?"
)
SOURCE_LITERAL_OR_COMMENT = re.compile(
    r"//[^\n]*|/\*.*?\*/|(?<![\w$])'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`",
    re.DOTALL,
)
JSX_COMPONENT = re.compile(r"<\s*([A-Z][A-Za-z0-9_$]*)\b")
LOCAL_NAMED_DECLARATION = re.compile(r"\b(?:class|function|const|let|var)\s+([A-Za-z_$][\w$]*)")
LOCAL_DESTRUCTURED_DECLARATION = re.compile(r"\b(?:const|let|var)\s*\{(?P<bindings>[^{}]*)\}\s*=")
LOCAL_DESTRUCTURED_BINDING = re.compile(
    r"(?:^|,)\s*(?:[A-Za-z_$][\w$]*\s*:\s*)?(?:\.\.\.)?"
    r"([A-Za-z_$][\w$]*)\s*(?=[,}=]|$)"
)
FUNCTION_PARAMETERS = re.compile(
    r"\bfunction\b[^()]*\((?P<function>[^()]*)\)"
    r"|\((?P<arrow>[^()]*)\)\s*=>"
    r"|(?P<single>\b[A-Za-z_$][\w$]*)\s*=>",
    re.DOTALL,
)


def _local_source_bindings(code: str) -> set[str]:
    bindings = set(LOCAL_NAMED_DECLARATION.findall(code))
    for declaration in LOCAL_DESTRUCTURED_DECLARATION.finditer(code):
        bindings.update(LOCAL_DESTRUCTURED_BINDING.findall(declaration.group("bindings")))
    for parameters in FUNCTION_PARAMETERS.finditer(code):
        single = parameters.group("single")
        if single:
            bindings.add(single)
            continue
        values = parameters.group("function") or parameters.group("arrow") or ""
        bindings.update(
            re.findall(
                r"(?:^|,)\s*(?:\.\.\.)?([A-Za-z_$][\w$]*)\s*(?=[:,?=]|$)",
                values,
            )
        )
        bindings.update(re.findall(r"(?:\{|,|:\s)([A-Za-z_$][\w$]*)\s*(?=[,}=])", values))
    return bindings


APPLICATION_KIT_COMPONENTS = frozenset(
    {
        "AgentIcon",
        "AppConversations",
        "ApplicationAction",
        "ArtifactText",
        "Avatar",
        "AvatarFallback",
        "AvatarStack",
        "Badge",
        "BrandMark",
        "Breakdown",
        "BreakdownHeader",
        "BreakdownLabel",
        "BreakdownMark",
        "BreakdownName",
        "BreakdownRow",
        "BreakdownRows",
        "BreakdownValue",
        "Button",
        "Card",
        "CardAction",
        "CardContent",
        "CardDescription",
        "CardFooter",
        "CardGrid",
        "CardHeader",
        "CardTitle",
        "Chart",
        "ChartBars",
        "ChatPane",
        "ConversationDetail",
        "DataTable",
        "Detail",
        "Dialog",
        "DialogTrigger",
        "DropdownMenu",
        "DropdownMenuCheckboxItem",
        "DropdownMenuContent",
        "DropdownMenuItem",
        "DropdownMenuLabel",
        "DropdownMenuRadioGroup",
        "DropdownMenuRadioItem",
        "DropdownMenuSeparator",
        "DropdownMenuTrigger",
        "Empty",
        "Facts",
        "FacetMenu",
        "FileSheet",
        "FoundingChat",
        "Group",
        "Header",
        "IconChevronDown",
        "IconChevronUp",
        "IconDots",
        "IconFilter2",
        "IconWorldWww",
        "IconX",
        "Lede",
        "Legend",
        "LegendItem",
        "Loading",
        "Markdown",
        "MediaIcon",
        "Meter",
        "Moment",
        "ObjectDetail",
        "ObjectPane",
        "Page",
        "PageToolbar",
        "Pager",
        "Pane",
        "PaneNote",
        "Panel",
        "PanelBlank",
        "PanelEmpty",
        "PressRow",
        "RebuildDialog",
        "RowLines",
        "Section",
        "SectionApp",
        "Segmented",
        "Separator",
        "Sheet",
        "Stat",
        "StatDelta",
        "StatDescription",
        "StatHeader",
        "StatLabel",
        "StatMedia",
        "StatValue",
        "SurfaceGlyph",
        "Td",
        "TdFact",
        "ToolbarRule",
        "ViewSwitch",
    }
)
ROOT_MOUNT = re.compile(
    r"\bmountApp\s*\(\s*document\.getElementById\(\s*['\"]root['\"]\s*\)\s*!?\s*,"
)
# The rules a shipped app page is held to by `gates.py`, restated for the one page nothing else
# reads. A shipped page is walked in the repo; a generated page exists only in a member's sandbox,
# so this validator is where the same rules have to be true or they are true of half the product.
# The refusals are worded as the repair the builder should make, because its repair loop reads them.
# A Tailwind arbitrary value is always a utility carrying one — `w-[3px]`, `text-[#fff]` — or an
# arbitrary property, which spells `[prop:value]`. A bare `[...]` is JavaScript: an array of issue
# references or percentages reads exactly like a raw colour or length, and refusing it would block a
# page over its data.
ARBITRARY_VALUE = re.compile(r"[a-z][\w-]*-\[([^\]\n]*)\]")
ARBITRARY_PROPERTY = re.compile(r"\[([a-z-]+:[^\]\n]*)\]")
RAW_CSS_VALUE = re.compile(
    r"#[0-9a-fA-F]|\d+(?:\.\d+)?(?:px|rem|em|ch|ex|vh|vw|vmin|vmax|%)(?![\w-])"
)
COMPOSITION_STEPS = ("hair", "2xs", "sm", "2xl", "6xl", "8xl")
COMPOSITION_GAP = re.compile(r"(?<![\w-])gap-(?:x-|y-)?(\[[^\]]*\]|[\w.]+)")
FRAMED_STAT = re.compile(r"<Stat[\s>][^>]*?(?<![\w-])border(?![\w-])", re.S)
PAGE_CLASS_REFUSALS = (
    (re.compile(r"(?<![\w-])space-[xy]-"), "stack with flex and a gap"),
    (re.compile(r"(?<![\w-])dark:"), "the colour scheme carries itself; write no dark variant"),
    (re.compile(r"overflow-hidden text-ellipsis whitespace-nowrap"), "truncate says this"),
    (re.compile(r"className=\{`"), "compose classes with cn()"),
)
STYLE_TAG = re.compile(r"<style[\s/>]")
DATA_SLOT_ATTRIBUTE = re.compile(r"(?<![\w-])data-slot\s*=")

LITERAL_WHITE_ON_SCHEME_INK = re.compile(
    r"\bstyle\s*=\s*\{\{"
    r"(?=(?:(?!\}\}).)*\bbackground(?:Color)?\s*:(?:(?!\}\}).)*var\(--color-ink\))"
    r"(?=(?:(?!\}\}).)*\bcolor\s*:(?:(?!\}\}).)*['\"](?:#fff(?:fff)?|white)['\"])",
    re.DOTALL | re.IGNORECASE,
)
APPLICATION_DESIGN_REGION = re.compile(r"[a-z][a-z0-9-]{0,79}")
APPLICATION_DESIGN_KIT_COMPONENT = re.compile(r"[A-Z][A-Za-z0-9]*")
APPLICATION_SOURCE_REGION = re.compile(r"""data-app-region\s*=\s*[{\s]*["']([a-z][a-z0-9-]*)["']""")


def _validate_application_imports(source: str) -> None:
    modules = tuple(left or right for left, right in IMPORT_MODULE.findall(source))
    if IMPORT_DECLARATION.search(source) is None or "ufo/kit" not in modules:
        raise ValueError("app.tsx must import its runtime and components from ufo/kit")
    if SIDE_EFFECT_IMPORT.search(source) or any(
        module != "ufo/kit" and SIBLING_MODULE.fullmatch(module) is None for module in modules
    ):
        raise ValueError(
            "app.tsx may import only from ufo/kit and a file beside it, "
            "as ./name.md?raw or ./name.ext?url"
        )
    if NON_NAMED_IMPORT.search(SIBLING_IMPORT.sub("", source)):
        raise ValueError("app.tsx must use named imports from ufo/kit")
    if EXPORT_DECLARATION.search(source):
        raise ValueError("app.tsx must not export declarations")
    if "UfoAppKit" in source:
        raise ValueError("app.tsx must import from ufo/kit instead of using UfoAppKit")


def _rendered_application_components(source: str) -> set[str]:
    if ROOT_MOUNT.search(source) is None:
        raise ValueError("mountApp must receive the root element and a render callback")
    imported_components: dict[str, set[str]] = {}
    for declaration in NAMED_KIT_IMPORT.finditer(source):
        if declaration.group("type"):
            continue
        for value in declaration.group("names").split(","):
            imported = KIT_IMPORT_NAME.fullmatch(value.strip())
            if (
                imported is not None
                and imported.group("type") is None
                and imported.group("export") in APPLICATION_KIT_COMPONENTS
            ):
                imported_components.setdefault(imported.group("export"), set()).add(
                    imported.group("local") or imported.group("export")
                )
    code = SOURCE_LITERAL_OR_COMMENT.sub("", source)
    local_declarations = _local_source_bindings(code)
    rendered_components = set(JSX_COMPONENT.findall(code)) - local_declarations
    rendered_kit_components = {
        exported
        for exported, local_names in imported_components.items()
        if not local_names.isdisjoint(rendered_components)
    }
    if not rendered_kit_components:
        raise ValueError("app.tsx must render at least one UI component imported from ufo/kit")
    return rendered_kit_components


def _validate_designed_components(
    rendered_kit_components: set[str], designed_kit_components: tuple[str, ...]
) -> None:
    missing_designed_components = tuple(
        name for name in designed_kit_components if name not in rendered_kit_components
    )
    if missing_designed_components:
        label = "component" if len(missing_designed_components) == 1 else "components"
        raise ValueError(
            f"app.tsx must directly render designed Kit {label}: "
            f"{', '.join(missing_designed_components)}"
        )


def _validate_designed_regions(source: str, designed_regions: tuple[str, ...]) -> None:
    marked = set(APPLICATION_SOURCE_REGION.findall(source))
    missing = tuple(name for name in designed_regions if name not in marked)
    if missing:
        label = "region" if len(missing) == 1 else "regions"
        raise ValueError(
            f"app.tsx must mark each designed {label} with data-app-region: {', '.join(missing)}"
        )


def _validate_application_styling(source: str) -> None:
    if LITERAL_WHITE_ON_SCHEME_INK.search(source):
        raise ValueError(
            "a --color-ink background must use --color-surface text in both colour schemes"
        )
    if STYLE_TAG.search(source):
        raise ValueError("app.tsx may not emit a <style> tag — the kit's theme is the sheet")
    if DATA_SLOT_ATTRIBUTE.search(source):
        raise ValueError("app.tsx: data-slot is reserved for ufo/kit components")
    for pattern, repair in PAGE_CLASS_REFUSALS:
        found = pattern.search(source)
        if found:
            raise ValueError(f"app.tsx: {found.group(0)!r} — {repair}")
    for pattern in (ARBITRARY_VALUE, ARBITRARY_PROPERTY):
        for segment in pattern.finditer(source):
            if RAW_CSS_VALUE.search(segment.group(1)):
                raise ValueError(
                    f"app.tsx: {segment.group(0)} names a raw value — "
                    "resolve it through a theme token"
                )
    if FRAMED_STAT.search(source):
        raise ValueError(
            "app.tsx: a Stat carries a border — a figure divides by the space around it, and a "
            "tile is what a Stat already is"
        )
    for gap in COMPOSITION_GAP.finditer(source):
        if gap.group(1) not in COMPOSITION_STEPS:
            raise ValueError(
                f"app.tsx: {gap.group(0)!r} is not a composition step — a page spaces its parts "
                f"with {', '.join('gap-' + step for step in COMPOSITION_STEPS)} and nothing else"
            )


def validate_application_source(source: str, design: ApplicationDesign | None = None) -> None:
    """Raise the repair an app page needs before it is worth building.

    Every message is worded as the edit to make, because the builder's repair loop is what reads
    them. The design is optional: a page deployed without one beside it is still held to the kit,
    the mount, and the styling rules."""

    _validate_application_imports(source)
    rendered_kit_components = _rendered_application_components(source)
    if design is not None:
        _validate_designed_components(rendered_kit_components, design.kit_components)
        _validate_designed_regions(source, design.regions)
    _validate_application_styling(source)


def _parse_application_design(source: str) -> tuple[ElementTree.Element, tuple[float, ...]]:
    if "<!DOCTYPE" in source.upper() or "<!ENTITY" in source.upper():
        raise ValueError("application design must not declare XML entities")
    try:
        root = ElementTree.fromstring(source)
    except ElementTree.ParseError as error:
        raise ValueError("application design must be valid SVG") from error
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise ValueError("application design root must be svg")
    try:
        view_box = tuple(
            float(value) for value in re.split(r"[ ,]+", root.attrib["viewBox"].strip())
        )
    except (KeyError, ValueError) as error:
        raise ValueError("application design svg requires a viewBox") from error
    if (
        len(view_box) != 4
        or not all(isfinite(value) for value in view_box)
        or view_box[:3] != (0, 0, APPLICATION_DESIGN_WIDTH)
        or not view_box[3].is_integer()
        or not APPLICATION_DESIGN_FOLD <= view_box[3] <= APPLICATION_DESIGN_MAX_HEIGHT
        or root.attrib.get("width") != str(APPLICATION_DESIGN_WIDTH)
        or root.attrib.get("height") != str(round(view_box[3]))
    ):
        raise ValueError(
            f'application design must use viewBox="0 0 {APPLICATION_DESIGN_WIDTH} H", '
            f'width="{APPLICATION_DESIGN_WIDTH}", and a matching integer height H from '
            f"{APPLICATION_DESIGN_FOLD} through {APPLICATION_DESIGN_MAX_HEIGHT}"
        )
    return root, view_box


def _visible_design_element(
    element: ElementTree.Element, tag: str, attributes: dict[str, str]
) -> bool:
    match tag:
        case "circle":
            return attributes.get("r", "") not in {"", "0", "0.0"}
        case "ellipse":
            return all(attributes.get(name, "") not in {"", "0", "0.0"} for name in ("rx", "ry"))
        case "image" | "rect":
            return all(
                attributes.get(name, "") not in {"", "0", "0.0"} for name in ("width", "height")
            )
        case "line":
            return (
                attributes.get("x1", "") != attributes.get("x2", "")
                or attributes.get("y1", "") != attributes.get("y2", "")
            ) and attributes.get("stroke", "").casefold() not in {"", "none", "transparent"}
        case "path":
            return bool(attributes.get("d"))
        case "polygon" | "polyline":
            return bool(attributes.get("points"))
        case "text":
            return bool("".join(element.itertext()).strip())
        case "use":
            return attributes.get("href", "").startswith("#")
        case _:
            return False


def _validate_design_attributes(element: ElementTree.Element) -> None:
    for name, value in element.attrib.items():
        attribute = name.rsplit("}", 1)[-1].casefold()
        lowered = value.casefold()
        if attribute.startswith("on") or any(
            scheme in lowered for scheme in ("javascript:", "data:", "http:", "https:")
        ):
            raise ValueError("application design must not contain active or external content")


def _validate_design_element(
    element: ElementTree.Element,
    ids: set[str],
    regions: list[ElementTree.Element],
    kit_components: list[str],
) -> bool:
    tag = element.tag.rsplit("}", 1)[-1]
    if tag.casefold() in {"clippath", "filter", "mask"}:
        raise ValueError(APPLICATION_DESIGN_EFFECT_ERROR)
    attributes = {name.rsplit("}", 1)[-1]: value.strip() for name, value in element.attrib.items()}
    if any(
        name.casefold() in {"clip-path", "filter", "mask", "mask-image"}
        and value.casefold() not in {"", "none"}
        for name, value in attributes.items()
    ) or any(
        match.group(1).strip().casefold() != "none"
        for value in (
            attributes.get("style", ""),
            "".join(element.itertext()) if tag == "style" else "",
        )
        for match in APPLICATION_DESIGN_EFFECT_STYLE.finditer(value)
    ):
        raise ValueError(APPLICATION_DESIGN_EFFECT_ERROR)
    element_id = element.attrib.get("id", "").strip()
    if element_id:
        if element_id in ids:
            raise ValueError("application design SVG ids must be unique")
        ids.add(element_id)
    if tag in {"script", "foreignObject"}:
        raise ValueError("application design must contain SVG drawing elements only")
    region = element.attrib.get("data-app-region")
    if region is not None:
        if tag != "g" or APPLICATION_DESIGN_REGION.fullmatch(region) is None:
            raise ValueError("application design regions must be lowercase slugs on SVG g elements")
        regions.append(element)
    kit_component = element.attrib.get("data-kit-component")
    if kit_component is not None:
        if tag != "g":
            raise ValueError("application design data-kit-component must be on an SVG g element")
        if not kit_component.strip():
            raise ValueError(
                "application design data-kit-component must name one visual ufo/kit export"
            )
        if APPLICATION_DESIGN_KIT_COMPONENT.fullmatch(kit_component) is None:
            raise ValueError("application design data-kit-component must be one ComponentName")
        if kit_component not in APPLICATION_KIT_COMPONENTS:
            raise ValueError(
                f"application design data-kit-component {kit_component!r} is not a visual "
                "ufo/kit export"
            )
        if kit_component not in kit_components:
            kit_components.append(kit_component)
    _validate_design_attributes(element)
    return tag in SVG_DRAWING_ELEMENTS and _visible_design_element(element, tag, attributes)


def validate_application_design(source: str) -> ApplicationDesign:
    """The regions, kit components, and height of one SVG that meets the design contract."""

    root, view_box = _parse_application_design(source)
    regions: list[ElementTree.Element] = []
    kit_components: list[str] = []
    ids: set[str] = set()
    drawing_elements = sum(
        _validate_design_element(element, ids, regions, kit_components) for element in root.iter()
    )
    if drawing_elements == 0:
        raise ValueError("application design must contain SVG drawing elements only")
    names = tuple(element.attrib["data-app-region"] for element in regions)
    if not DESIGN_REGION_MIN <= len(names) <= DESIGN_REGION_MAX or len(set(names)) != len(names):
        raise ValueError(
            f"application design requires {DESIGN_REGION_MIN} to {DESIGN_REGION_MAX} unique regions"
        )
    if any(
        descendant is not region and descendant.attrib.get("data-app-region") is not None
        for region in regions
        for descendant in region.iter()
    ):
        raise ValueError("application design regions must not be nested")
    if not kit_components:
        raise ValueError(
            "application design requires data-kit-component on at least one SVG g element"
        )
    return ApplicationDesign(names, tuple(kit_components), int(view_box[3]))
