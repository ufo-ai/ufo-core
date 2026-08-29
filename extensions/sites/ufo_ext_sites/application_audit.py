"""Typed deterministic acceptance for an interactive ufo application."""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DESKTOP_WIDTH = 1440
NARROW_WIDTH = 305
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
APPLICATION_REGION_MIN_WIDTH = 0.04
APPLICATION_REGION_MIN_HEIGHT = 0.03
APPLICATION_REGION_MIN_AREA = 0.008
MAX_ISSUES = 8
MAX_MESSAGE_CHARS = 500
MAX_PRODUCT_QA_CONTROLS = 100
APPLICATION_AUDIT_REQUEST_CONTRACT_KEY = (
    "application-builder/audit-contract/request/{request_sha256}"
)
APPLICATION_AUDIT_TURN_CONTRACT_KEY = "application-builder/audit-contract/turn/{turn_id}"
APPLICATION_AUDIT_ATTEMPT_KEY = "application-builder/audit-attempt/{turn_id}"
AuditTerm = Annotated[str, Field(min_length=1, max_length=200)]
AuditIssueCode = Literal[
    "audit_run",
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
    thresholds stay unscaled because the page width is fixed: 305 px for the design lane and one
    measured viewport width for an application view.
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


def application_design_fidelity(report: ApplicationAuditReport) -> ApplicationDesignFidelity:
    """Measure named region identity, first-screen visibility, and relative desktop order."""

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
                if candidate.scheme == scheme and candidate.width == DESKTOP_WIDTH
            ),
            None,
        )
        total += 1 + len(design)
        if view is None:
            failures.append(f"{scheme} desktop has no region measurement")
            continue
        app_names = tuple(region.name for region in view.regions)
        app_by_name = {region.name: region for region in view.regions}
        if len(app_by_name) == len(view.regions) and set(app_names) == set(design_names):
            passed += 1
        else:
            failures.append(f"{scheme} desktop region names differ")
        for name in design_names:
            region = app_by_name.get(name)
            if region is not None and (not design_by_name[name].above_fold or region.above_fold):
                passed += 1
            else:
                failures.append(f"{scheme} desktop lacks visible {name}")
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
                        f"{scheme} desktop changes {expected[0]} order for "
                        f"{first_name} and {second_name}"
                    )
    return ApplicationDesignFidelity(passed=passed, total=total, failures=tuple(failures))


def audit_application(
    report: ApplicationAuditReport,
    contract: ApplicationAuditContract | None = None,
) -> ApplicationAuditVerdict:
    """Apply the fixed page, interaction, and fact checks to one browser report."""

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
    contrast = []
    for view in measured:
        for item in view.text:
            needed = _needed_ratio(item)
            if item.ratio >= needed:
                continue
            label = f' "{item.text}"' if item.text else ""
            colours = (
                f" {item.colour} on {item.background}" if item.colour and item.background else ""
            )
            contrast.append(
                f"{view.scheme} {view.width}px{label} at {item.selector}{colours} "
                f"is {item.ratio}:1; needs {needed}:1"
            )
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
    if fidelity.failures:
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
