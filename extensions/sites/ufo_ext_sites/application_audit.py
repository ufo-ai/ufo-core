"""Typed deterministic acceptance for an interactive ufo application."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DESKTOP_WIDTH = 1440
NARROW_WIDTH = 390
SCHEMES: tuple[Literal["light", "dark"], ...] = ("light", "dark")
MEASURED_VIEWS = tuple(
    (scheme, width) for width in (DESKTOP_WIDTH, NARROW_WIDTH) for scheme in SCHEMES
)
AA_BODY = 4.5
AA_LARGE = 3.0
LARGE_PX = 24.0
LARGE_BOLD_PX = 18.66
BOLD_WEIGHT = 700
MIN_CONTROLS = 2
MIN_INTERACTIONS = 2
MAX_ISSUES = 8
MAX_MESSAGE_CHARS = 500
MAX_PRODUCT_QA_CONTROLS = 100
APPLICATION_AUDIT_REQUEST_CONTRACT_KEY = (
    "application-builder/audit-contract/request/{request_sha256}"
)
APPLICATION_AUDIT_TURN_CONTRACT_KEY = "application-builder/audit-contract/turn/{turn_id}"
APPLICATION_AUDIT_ATTEMPT_KEY = "application-builder/audit-attempt/{turn_id}"
APPLICATION_AUDIT_SERVER = b"""from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import sys

os.chdir(Path(sys.argv[1]).resolve())

class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        if path == "/assets" or path.startswith("/assets/"):
            path = "/dist" + path
        return super().translate_path(path)

ThreadingHTTPServer(("0.0.0.0", int(sys.argv[2])), Handler).serve_forever()
"""
AuditTerm = Annotated[str, Field(min_length=1, max_length=200)]
AuditIssueCode = Literal[
    "audit_run",
    "missing_view",
    "empty_view",
    "contrast",
    "overflow",
    "clipping",
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

    text: str = ""
    selector: str
    px: float
    weight: int
    ratio: float


class ApplicationAuditView(BaseModel):
    """The browser measurements for one colour scheme and viewport width."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    scheme: Literal["light", "dark"]
    width: int
    text_checked: int = Field(alias="textChecked", ge=0)
    text: tuple[ApplicationAuditText, ...]
    document_width: int = Field(alias="documentWidth", ge=0)
    clipped: tuple[str, ...]
    console: tuple[str, ...]
    above_fold_text: str = Field(alias="aboveFoldText")


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


def _needed_ratio(px: float, weight: int) -> float:
    if px >= LARGE_PX or (px >= LARGE_BOLD_PX and weight >= BOLD_WEIGHT):
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
    contrast = tuple(
        f"{view.scheme} {view.width}px {item.selector} {item.ratio}:1 needs "
        f"{_needed_ratio(item.px, item.weight)}:1"
        for view in measured
        for item in view.text
        if item.ratio < _needed_ratio(item.px, item.weight)
    )
    if contrast:
        issues.append(_issue("contrast", f"Fix text contrast: {'; '.join(contrast[:4])}."))
    overflow = tuple(
        f"{view.scheme} {view.width}px document is {view.document_width}px"
        for view in measured
        if view.document_width > view.width
    )
    if overflow:
        issues.append(_issue("overflow", f"Fix horizontal overflow: {'; '.join(overflow[:4])}."))
    clipped = tuple(
        f"{view.scheme} {view.width}px {item}" for view in measured for item in view.clipped
    )
    if clipped:
        issues.append(_issue("clipping", f"Fix clipped content: {'; '.join(clipped[:4])}."))
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
