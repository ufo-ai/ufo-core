"""The skill-loading catalog: member queries spanning every skill the assistant pack carries, two
to three per skill, each expecting one exact first load. A query whose premise names an artifact
stages it as workspace files so the agent routes instead of asking for a missing upload; fixture
content is minimal — the turn is cancelled at the first watched mount, before any file is read.
Forbidden sets name only unambiguous wrong picks, never a legitimate companion load
(`design-foundations` beside a deck build) and never a child's own parent (which mounts with the
child)."""

from __future__ import annotations

import zipfile
from io import BytesIO
from textwrap import dedent

from evals.harness.capability import WorkspaceFile
from evals.skill_loading.runner import SkillLoadCase

OPC_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>\n'
)


def _office_stub() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            zipfile.ZipInfo("[Content_Types].xml", date_time=(1980, 1, 1, 0, 0, 0)),
            OPC_CONTENT_TYPES,
        )
    return buffer.getvalue()


OFFICE_STUB = _office_stub()
PDF_STUB = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n"
    b"%%EOF\n"
)


def _file(path: str, body: str) -> WorkspaceFile:
    return WorkspaceFile(path, dedent(body).strip().encode() + b"\n")


PROPOSAL_NOTES = _file(
    "notes/proposal-notes.md",
    """
    # Proposal notes — Meridian onboarding revamp

    - Problem: onboarding takes 6 weeks; competitors quote 2.
    - Offer: fixed-scope 3-week onboarding at $24k, then $6k/mo managed plan.
    - Scope: data import, SSO setup, two training sessions, launch review.
    - Terms: 50% upfront, net-30 remainder; success = live in production with 20 seats.
    """,
)
CONTRACT_EDITS = _file(
    "notes/edits.md",
    """
    # Requested edits — contract.docx

    1. Section 2.1: change the term from 12 to 24 months.
    2. Section 4.3: cap liability at 12 months of fees, not total contract value.
    3. Section 6: replace the governing state with Delaware.
    4. Add a data-processing addendum reference in section 7.
    """,
)
REPO_WEBHOOK = _file(
    "repo/webhook.py",
    """
    import json

    from dispatch import dispatch_event

    MAX_RETRIES = 3


    def handle_webhook(raw_body: bytes) -> dict:
        event = json.loads(raw_body)
        for attempt in range(MAX_RETRIES):
            try:
                return dispatch_event(event)
            except TimeoutError:
                continue
        return {"status": "dropped", "event_id": event.get("id")}
    """,
)
REPO_DISPATCH = _file(
    "repo/dispatch.py",
    """
    HANDLERS = {}


    def register(kind):
        def wrap(fn):
            HANDLERS[kind] = fn
            return fn

        return wrap


    def dispatch_event(event):
        handler = HANDLERS.get(event["kind"])
        if handler is None:
            raise KeyError(event["kind"])
        return handler(event)
    """,
)
REPO_ISSUE = _file(
    "repo/ISSUE.md",
    """
    # Issue 327 — dropped webhooks are invisible

    When every retry times out, handle_webhook returns {"status": "dropped"} but nothing
    records the drop. Add a durable dead-letter record (a JSON line appended to
    dead_letters.log) with the event id and kind, and include the dead-letter path in the
    response.
    """,
)
REPO_SYNC = _file(
    "repo/sync.py",
    """
    import time


    def wait_for_sync(store, key, timeout_seconds=1.0):
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if store.get(key) is not None:
                return store.get(key)
        raise TimeoutError(key)
    """,
)
REPO_SYNC_TEST = _file(
    "repo/test_sync.py",
    """
    from sync import wait_for_sync


    class SlowStore:
        def __init__(self):
            self.reads = 0

        def get(self, key):
            self.reads += 1
            if self.reads > 200_000:
                return "ready"
            return None


    def test_wait_for_sync_returns_once_the_value_lands():
        assert wait_for_sync(SlowStore(), "job-1") == "ready"
    """,
)
REPORT_PROCESS = _file(
    "notes/weekly-report-process.md",
    """
    # Weekly metrics report — our process

    1. Pull the latest data/metrics.csv export.
    2. Compute week-over-week deltas for signups, activation, and MRR.
    3. Flag any metric that moved more than 10% either way.
    4. Write the summary as: headline, wins, risks, asks — under 200 words.
    """,
)
DRAFT_SKILL = _file(
    "drafts/meeting-notes/SKILL.md",
    """
    ---
    name: meeting-summaries
    description: Summarize meeting transcripts into decisions: owners, deadlines, open questions.
    ---

    # Meeting Summaries

    1. List each decision with its owner and deadline.
    2. End with the open questions.
    """,
)
RELEASE_CHECKLIST = _file(
    "notes/release-checklist.md",
    """
    # Release preflight checklist

    - Changelog entry drafted and dated.
    - Version bumped in pyproject.toml.
    - scripts/preflight.py exits clean.
    - Rollback owner named in the release thread.
    """,
)
PREFLIGHT_SCRIPT = _file(
    "scripts/preflight.py",
    """
    import sys
    from pathlib import Path

    REQUIRED = ["CHANGELOG.md", "pyproject.toml"]

    missing = [name for name in REQUIRED if not Path(name).exists()]
    if missing:
        sys.exit("missing: " + ", ".join(missing))
    print("preflight ok")
    """,
)
CUSTOMERS_CSV = _file(
    "data/customers.csv",
    """
    customer_id,segment,region,signup_date,plan,mrr_usd
    1001,smb,us-east,2025-11-03,starter,49
    1002,enterprise,eu-west,2025-08-19,scale,1990
    1003,smb,us-west,2026-01-22,starter,49
    1004,mid-market,us-east,2025-05-30,growth,490
    1005,enterprise,apac,2026-02-14,scale,2490
    1006,smb,eu-west,2026-03-02,,49
    """,
)
ORDERS_CSV = _file(
    "data/orders.csv",
    """
    order_id,customer_id,ordered_at,amount_usd,status
    9001,1001,2026-01-05T10:12:00Z,120.50,fulfilled
    9002,1002,2026-01-17T08:03:00Z,3400.00,fulfilled
    9003,1002,2026-02-02T16:44:00Z,2100.00,refunded
    9004,1003,2026-02-19T11:27:00Z,89.99,fulfilled
    9005,1042,2026-03-08T09:15:00Z,240.00,fulfilled
    9006,1004,2026-03-21T14:58:00Z,975.25,pending
    """,
)
CATEGORIES_CSV = _file(
    "data/product-categories.csv",
    """
    category_id,parent_id,name
    1,,Hardware
    2,1,Sensors
    3,1,Controllers
    4,,Software
    5,4,Analytics
    6,4,Automations
    7,2,Thermal Sensors
    8,9,Orphaned Widgets
    """,
)
SCHEMA_SQL = _file(
    "data/schema.sql",
    """
    CREATE TABLE customers (
        customer_id INTEGER PRIMARY KEY,
        segment TEXT NOT NULL CHECK (segment IN ('smb', 'mid-market', 'enterprise')),
        region TEXT NOT NULL,
        signup_date DATE NOT NULL,
        plan TEXT CHECK (plan IN ('starter', 'growth', 'scale')),
        mrr_usd NUMERIC NOT NULL CHECK (mrr_usd >= 0)
    );

    CREATE TABLE orders (
        order_id INTEGER PRIMARY KEY,
        customer_id INTEGER NOT NULL REFERENCES customers (customer_id),
        ordered_at TIMESTAMPTZ NOT NULL,
        amount_usd NUMERIC NOT NULL CHECK (amount_usd > 0),
        status TEXT NOT NULL CHECK (status IN ('pending', 'fulfilled', 'refunded'))
    );
    """,
)
SLOW_QUERY_SQL = _file(
    "data/slow-query.sql",
    """
    SELECT c.segment,
           (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.customer_id) AS orders,
           (SELECT SUM(o2.amount_usd) FROM orders o2
             WHERE o2.customer_id = c.customer_id) AS revenue
    FROM customers c
    WHERE c.customer_id IN (SELECT customer_id FROM orders)
    ORDER BY revenue DESC;
    """,
)
EVENTS_CSV = _file(
    "data/events.csv",
    """
    user_id,event,occurred_at
    u1,signup,2026-04-06T09:00:00Z
    u1,activate,2026-04-07T10:30:00Z
    u1,purchase,2026-04-20T12:00:00Z
    u2,signup,2026-04-06T11:15:00Z
    u2,activate,2026-04-09T08:45:00Z
    u3,signup,2026-04-13T15:20:00Z
    u3,signup,2026-04-13T15:20:00Z
    u3,purchase,2026-05-02T17:40:00Z
    """,
)
ACTIVATION_CSV = _file(
    "data/activation.csv",
    """
    user_id,cohort,activated
    u001,control,0
    u002,control,1
    u003,variant,1
    u004,variant,1
    u005,control,0
    u006,variant,0
    u007,control,1
    u008,variant,1
    """,
)
CHURN_CSV = _file(
    "data/churn.csv",
    """
    account_id,tenure_months,seats,support_tickets,nps,churned
    a01,4,3,6,2,1
    a02,26,45,1,9,0
    a03,11,10,3,7,0
    a04,2,2,8,1,1
    a05,18,22,2,8,0
    a06,7,5,5,4,1
    """,
)
REVENUE_CSV = _file(
    "data/revenue-monthly.csv",
    """
    month,revenue_usd
    2025-01,182000
    2025-02,175500
    2025-03,198200
    2025-04,204900
    2025-05,211300
    2025-06,478000
    2025-07,224100
    2025-08,231800
    2025-09,240400
    2025-10,238900
    2025-11,251200
    2025-12,266700
    2026-01,259800
    2026-02,274300
    """,
)
EXPORT_CSV = _file(
    "data/export.csv",
    """
    account_id,email,created_at,seats,plan
    a01,kim@example.com,2026-02-30T09:00:00Z,5,growth
    a02,lee@example.com,2026-03-11T10:00:00Z,-2,starter
    a01,kim@example.com,2026-02-30T09:00:00Z,5,growth
    a04,,2026-04-02T12:30:00Z,12,ultra
    """,
)
ANALYSIS_RESULTS_CSV = _file(
    "data/analysis-results.csv",
    """
    metric,value,expected_range
    total_revenue_usd,10254000,9500000-11000000
    active_accounts,1284,1200-1400
    avg_order_usd,-312.40,50-800
    churn_rate,0.034,0.02-0.06
    activation_rate,1.42,0.3-0.7
    """,
)
MARKET_SHARE_CSV = _file(
    "data/market-share.csv",
    """
    quarter,vendor,share_pct
    2025Q1,acme,34
    2025Q1,borealis,27
    2025Q1,cirrus,21
    2025Q1,others,18
    2025Q2,acme,33
    2025Q2,borealis,29
    2025Q2,cirrus,20
    2025Q2,others,18
    2025Q3,acme,31
    2025Q3,borealis,32
    2025Q3,cirrus,19
    2025Q3,others,18
    """,
)
METRICS_CSV = _file(
    "data/metrics.csv",
    """
    week,signups,activation_rate,mrr_usd,nps,median_session_minutes
    2026-05-04,412,0.44,912000,41,7.9
    2026-05-11,398,0.47,921500,44,8.3
    2026-05-18,455,0.43,930100,39,8.1
    2026-05-25,431,0.49,944800,46,8.6
    2026-06-01,470,0.51,958200,45,9.0
    2026-06-08,462,0.48,969400,47,8.8
    """,
)
RESEARCH_FINDINGS = _file(
    "research/findings.md",
    """
    # Raw findings — vector database market

    - Managed offerings from three incumbents launched within the last year (vendor
      blogs, 2026-03 and 2026-05).
    - Two open-source engines added disk-based indexes; benchmarks claim 10x cost
      reduction (project changelogs).
    - Enterprise buyers cite hybrid search and RBAC as gating features (14 practitioner
      interviews).
    - Pricing converges on per-GB-stored plus per-query; one vendor still prices per pod.
    - Unverified: rumored acquisition of the smallest vendor (single trade-press item).
    """,
)
RESEARCH_COMPANIES = _file(
    "research/companies.md",
    """
    # Research notes — five vendors

    | Company | ARR est. | Focus | Notable |
    |---|---|---|---|
    | Acme AI | $40M | enterprise search | SOC2, 3 Fortune-100 logos |
    | Borealis | $22M | developer API | fastest-growing OSS community |
    | Cirrus Data | $15M | analytics embeddings | strong EU presence |
    | Delphi Labs | $8M | on-prem | defense contracts |
    | Ember | $5M | consumer memory | viral but unmonetized |
    """,
)
RESEARCH_SOURCES = _file(
    "research/sources.md",
    """
    # Collected sources — battery recycling

    1. DOE grant announcement 2026-04: $120M for direct-recycling pilots.
    2. Peer-reviewed study (2026-02): direct cathode recycling retains 95% capacity over
       500 cycles.
    3. Startup press release: claims 70% cost reduction vs smelting (no third-party
       audit).
    4. Trade article: EU regulation mandates 25% recycled content by 2031.
    5. Interview notes: two OEM battery leads skeptical of feedstock supply.
    """,
)
MEMO_SOURCES = _file(
    "docs-in/sources.md",
    """
    # Source materials — Q2 metrics

    - Signed ARR as of 2026-06-30: $4.2M (finance close, final).
    - Q2 new logos: 14 (CRM export 2026-07-01).
    - Gross margin: 71% (finance close).
    - Largest customer: 9% of ARR.
    """,
)
PROCESS_SCRIPT = _file(
    "scripts/process.py",
    """
    import csv
    from pathlib import Path

    ROWS = [
        {"name": "alpha", "value": 3},
        {"name": "beta", "value": 7},
    ]


    def main() -> None:
        out = Path("out/summary.csv")
        out.parent.mkdir(exist_ok=True)
        with out.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["name", "value"])
            writer.writeheader()
            writer.writerows(ROWS)
        print("total:", sum(row["count"] for row in ROWS))


    main()
    """,
)
PROJ_MAIN = _file(
    "proj/main.py",
    """
    def check() -> int
        return 0


    if __name__ == "__main__":
        raise SystemExit(check())
    """,
)
PROJ_README = _file(
    "proj/README.md",
    """
    # proj

    Build check: `python -m compileall .` then `python main.py`.
    """,
)
DECK_STUB = WorkspaceFile("decks/quarterly-deck.pptx", OFFICE_STUB)
TEMPLATE_STUB = WorkspaceFile("decks/template.pptx", OFFICE_STUB)
PROPOSAL_DOCX = WorkspaceFile("docs-in/proposal.docx", OFFICE_STUB)
MEMO_DOCX = WorkspaceFile("docs-in/investment-memo.docx", OFFICE_STUB)
CONTRACT_DOCX = WorkspaceFile("docs-in/contract.docx", OFFICE_STUB)
MODEL_XLSX = WorkspaceFile("models/financial-model.xlsx", OFFICE_STUB)
RAW_DATA_XLSX = WorkspaceFile("models/raw-data.xlsx", OFFICE_STUB)
REPORT_PART1_PDF = WorkspaceFile("pdfs/report-part1.pdf", PDF_STUB)
REPORT_PART2_PDF = WorkspaceFile("pdfs/report-part2.pdf", PDF_STUB)
SCAN_PDF = WorkspaceFile("pdfs/scan.pdf", PDF_STUB)
LOCKED_PDF = WorkspaceFile("pdfs/statement-locked.pdf", PDF_STUB)
CASES: tuple[SkillLoadCase, ...] = (
    SkillLoadCase(
        "daily-brief-review",
        "Prepare my private daily brief from today's work, conversations, pages, and outside "
        "context. Include drafts I can approve later.",
        expected="daily-brief",
        forbidden=("research-report", "task-scheduling"),
    ),
    SkillLoadCase(
        "daily-brief-approval",
        "I approve only the first task draft from this morning's daily brief. Apply that draft "
        "and leave the other drafts unchanged.",
        expected="daily-brief",
        forbidden=("task-scheduling",),
    ),
    SkillLoadCase(
        "coding-trace-webhook",
        "Trace how an incoming webhook event reaches the dispatcher in the code under repo/ and "
        "explain where retries are handled.",
        expected="coding",
        workspace_files=(REPO_WEBHOOK, REPO_DISPATCH),
    ),
    SkillLoadCase(
        "coding-implement-issue",
        "Implement the change described in repo/ISSUE.md and add the focused tests that prove "
        "the new behavior.",
        expected="coding",
        workspace_files=(REPO_ISSUE, REPO_WEBHOOK, REPO_DISPATCH),
    ),
    SkillLoadCase(
        "coding-fix-failing-test",
        "Find the cause of the timeout in repo/test_sync.py, fix it, and run the affected tests.",
        expected="coding",
        workspace_files=(REPO_SYNC, REPO_SYNC_TEST),
    ),
    SkillLoadCase(
        "coding-clone-private-repo",
        "Clone our private repo github.com/metalcraftai/ufo and find where the turn queue retries "
        "a failed step.",
        expected="coding",
    ),
    SkillLoadCase(
        "coding-github-connected-for-clone",
        "Is GitHub hooked up well enough for you to check out our private repo and push a branch?",
        expected="coding",
        forbidden=("create-skill",),
    ),
    SkillLoadCase(
        "coding-github-app-api-identity",
        "Have a coding agent publish a pull-request review as our installed ufo GitHub App.",
        expected="coding",
        forbidden=("create-skill",),
    ),
    SkillLoadCase(
        "application-invoice-inbox",
        "I want a separate app that reads the invoices landing in our shared inbox and files the "
        "totals, so it stays out of this chat.",
        expected="create-application",
        forbidden=("create-skill", "task-scheduling"),
    ),
    SkillLoadCase(
        "application-support-desk",
        "Set the support team up with something of its own that answers the common product "
        "questions and passes anything else to a person.",
        expected="create-application",
        forbidden=("create-skill",),
    ),
    SkillLoadCase(
        "application-private-recruiting",
        "Can I have my own agent just for recruiting, with its own instructions and nobody else "
        "seeing it?",
        expected="create-application",
        forbidden=("create-skill",),
    ),
    SkillLoadCase(
        "createskill-capture-workflow",
        "Capture the weekly-report process described in notes/weekly-report-process.md as a "
        "reusable custom skill for this agent so its future turns produce the report the same way.",
        expected="create-skill",
        forbidden=("research-report", "create-application"),
        workspace_files=(REPORT_PROCESS,),
    ),
    SkillLoadCase(
        "createskill-fix-draft-frontmatter",
        "My draft skill at drafts/meeting-notes/SKILL.md keeps failing to save — fix whatever "
        "is wrong with its frontmatter and save it for this agent.",
        expected="create-skill",
        forbidden=("coding",),
        workspace_files=(DRAFT_SKILL,),
    ),
    SkillLoadCase(
        "createskill-package-checklist",
        "Package the release checklist in notes/release-checklist.md and the helper script "
        "scripts/preflight.py into a skill I can load in later turns.",
        expected="create-skill",
        workspace_files=(RELEASE_CHECKLIST, PREFLIGHT_SCRIPT),
    ),
    SkillLoadCase(
        "createskill-github-review-process",
        "Create a reusable skill from this process: inspect a GitHub pull request, list blocking "
        "findings, and publish the result. Do not review a pull request now.",
        expected="create-skill",
        forbidden=("coding", "create-application"),
    ),
    SkillLoadCase(
        "explore-customers-profile",
        "Profile data/customers.csv before we analyze it: summarize its columns, types, missing "
        "values, distributions, and obvious quality issues.",
        expected="data-exploration",
        workspace_files=(CUSTOMERS_CSV,),
    ),
    SkillLoadCase(
        "explore-customers-relationships",
        "Explore the customer dataset at data/customers.csv for correlations, useful derived "
        "columns, and redundant fields.",
        expected="data-exploration",
        workspace_files=(CUSTOMERS_CSV,),
    ),
    SkillLoadCase(
        "explore-category-hierarchy",
        "Figure out the hierarchy in the product-category records at data/product-categories.csv "
        "and show any structural patterns or anomalies.",
        expected="data-exploration",
        workspace_files=(CATEGORIES_CSV,),
    ),
    SkillLoadCase(
        "sql-monthly-revenue",
        "Write a SQL query against data/schema.sql that joins orders to customers and returns "
        "monthly revenue by customer segment.",
        expected="data-sql-queries",
        workspace_files=(SCHEMA_SQL,),
    ),
    SkillLoadCase(
        "sql-optimize-slow-query",
        "Debug and optimize the slow query in data/slow-query.sql against data/schema.sql "
        "without changing its results.",
        expected="data-sql-queries",
        workspace_files=(SLOW_QUERY_SQL, SCHEMA_SQL),
    ),
    SkillLoadCase(
        "sql-cohort-funnel",
        "Calculate week-eight cohort retention and funnel conversion from the event data in "
        "data/events.csv, deduplicating repeated events first.",
        expected="data-sql-queries",
        workspace_files=(EVENTS_CSV,),
    ),
    SkillLoadCase(
        "stats-onboarding-lift",
        "Using data/activation.csv, test whether the new onboarding flow significantly improved "
        "activation and report the effect size and confidence interval.",
        expected="data-statistical-analysis",
        workspace_files=(ACTIVATION_CSV,),
    ),
    SkillLoadCase(
        "stats-churn-drivers",
        "Choose and run the right regression or correlation analysis on data/churn.csv to "
        "explain which factors are associated with churn.",
        expected="data-statistical-analysis",
        workspace_files=(CHURN_CSV,),
    ),
    SkillLoadCase(
        "stats-revenue-outliers",
        "Detect meaningful outliers in the revenue series at data/revenue-monthly.csv and "
        "estimate the underlying month-over-month growth trend.",
        expected="data-statistical-analysis",
        workspace_files=(REVENUE_CSV,),
    ),
    SkillLoadCase(
        "validate-export-schema",
        "Validate data/export.csv against data/schema.sql and flag invalid types, out-of-range "
        "values, duplicates, and broken foreign keys.",
        expected="data-validation",
        workspace_files=(EXPORT_CSV, SCHEMA_SQL),
    ),
    SkillLoadCase(
        "validate-analysis-qa",
        "Run a pre-delivery QA pass on the analysis results in data/analysis-results.csv and "
        "identify any failed integrity or sanity checks.",
        expected="data-validation",
        workspace_files=(ANALYSIS_RESULTS_CSV,),
    ),
    SkillLoadCase(
        "validate-order-integrity",
        "Check that order IDs are unique, timestamps are valid, and every order in "
        "data/orders.csv references a customer in data/customers.csv.",
        expected="data-validation",
        workspace_files=(ORDERS_CSV, CUSTOMERS_CSV),
    ),
    SkillLoadCase(
        "viz-revenue-trend",
        "Create a clear chart from data/revenue-monthly.csv showing monthly revenue trends and "
        "year-over-year change.",
        expected="data-visualization",
        forbidden=("office-xlsx",),
        workspace_files=(REVENUE_CSV,),
    ),
    SkillLoadCase(
        "viz-market-share",
        "Visualize market share, segment rankings, and how the product mix changed over time "
        "using data/market-share.csv.",
        expected="data-visualization",
        forbidden=("office-xlsx",),
        workspace_files=(MARKET_SHARE_CSV,),
    ),
    SkillLoadCase(
        "viz-kpi-dashboard",
        "Choose appropriate plots for the distributions, correlations, and four headline KPIs in "
        "data/metrics.csv.",
        expected="data-visualization",
        forbidden=("office-xlsx",),
        workspace_files=(METRICS_CSV,),
    ),
    SkillLoadCase(
        "design-investor-pdf",
        "Design a polished investor update PDF using our logo colors — deep navy #0B3D91 with "
        "amber #F2A900 accents; choose complementary typography and a restrained chart palette.",
        expected="design-foundations",
        forbidden=("theme-factory",),
    ),
    SkillLoadCase(
        "design-deck-system",
        "Give the slide deck at decks/quarterly-deck.pptx a coherent visual system even though I "
        "have not provided brand guidelines.",
        expected="design-foundations",
        forbidden=("theme-factory",),
        workspace_files=(DECK_STUB,),
    ),
    SkillLoadCase(
        "design-landing-charts",
        "Design a responsive landing page and charts that clearly distinguish change, "
        "composition, and distribution.",
        expected="design-foundations",
    ),
    SkillLoadCase(
        "docreview-proofread-docx",
        "Proofread the uploaded document at docs-in/proposal.docx and flag spelling errors, "
        "inconsistent numbers, and any confidential information.",
        expected="document-review",
        forbidden=("office-docx",),
        workspace_files=(PROPOSAL_DOCX,),
    ),
    SkillLoadCase(
        "docreview-memo-audit",
        "Audit the investment memo at docs-in/investment-memo.docx against the source materials "
        "in docs-in/sources.md and redline unsupported factual claims.",
        expected="document-review",
        workspace_files=(MEMO_DOCX, MEMO_SOURCES),
    ),
    SkillLoadCase(
        "docreview-model-review",
        "Review the financial-model spreadsheet at models/financial-model.xlsx for formula "
        "inconsistencies, suspicious assumptions, and presentation issues.",
        expected="document-review",
        workspace_files=(MODEL_XLSX,),
    ),
    SkillLoadCase(
        "docx-proposal",
        "Create a polished Word proposal from notes/proposal-notes.md with a title page, table "
        "of contents, and consistent heading styles.",
        expected="office-docx",
        workspace_files=(PROPOSAL_NOTES,),
    ),
    SkillLoadCase(
        "docx-tracked-edits",
        "Apply the edits listed in notes/edits.md to docs-in/contract.docx using tracked "
        "changes, and add comments where the wording is ambiguous.",
        expected="office-docx",
        forbidden=("document-review",),
        workspace_files=(CONTRACT_EDITS, CONTRACT_DOCX),
    ),
    SkillLoadCase(
        "docx-flatten-changes",
        "Flatten the tracked changes in docs-in/contract.docx into final text and export page "
        "images so I can verify the layout.",
        expected="office-docx",
        workspace_files=(CONTRACT_DOCX,),
    ),
    SkillLoadCase(
        "pptx-sustainability-pitch",
        "Create an eight-slide sustainability pitch with charts, speaker notes, and a clear "
        "executive narrative.",
        expected="office-pptx",
    ),
    SkillLoadCase(
        "pptx-restyle-template",
        "Restyle decks/quarterly-deck.pptx using the company template at decks/template.pptx "
        "without changing its content.",
        expected="office-pptx",
        forbidden=("theme-factory",),
        workspace_files=(DECK_STUB, TEMPLATE_STUB),
    ),
    SkillLoadCase(
        "pptx-extract-revise",
        "Extract the text and data from decks/quarterly-deck.pptx, then revise the layouts and "
        "resolve the reviewer comments.",
        expected="office-pptx",
        workspace_files=(DECK_STUB,),
    ),
    SkillLoadCase(
        "xlsx-shift-schedule",
        "Build an editable employee shift schedule in Excel with coverage formulas, role "
        "validation, and a weekly staffing chart.",
        expected="office-xlsx",
        forbidden=("task-scheduling",),
    ),
    SkillLoadCase(
        "xlsx-cleanup-validation",
        "Clean up the workbook at models/raw-data.xlsx, add data validation and formatted "
        "tables, and organize the raw data into a comparison sheet.",
        expected="office-xlsx",
        forbidden=("data-validation",),
        workspace_files=(RAW_DATA_XLSX,),
    ),
    SkillLoadCase(
        "xlsx-repair-formulas",
        "Find and repair the spreadsheet errors in models/financial-model.xlsx, then verify the "
        "formulas and totals.",
        expected="office-xlsx",
        forbidden=("document-review",),
        workspace_files=(MODEL_XLSX,),
    ),
    SkillLoadCase(
        "pdf-merge-rotate",
        "Merge pdfs/report-part1.pdf and pdfs/report-part2.pdf, rotate the sideways pages, and "
        "compress the result without making the text blurry.",
        expected="pdf",
        workspace_files=(REPORT_PART1_PDF, REPORT_PART2_PDF),
    ),
    SkillLoadCase(
        "pdf-ocr-tables",
        "OCR the scanned document at pdfs/scan.pdf and extract its tables into a usable "
        "structured file.",
        expected="pdf",
        workspace_files=(SCAN_PDF,),
    ),
    SkillLoadCase(
        "pdf-repair-decrypt",
        "Repair the corrupted encrypted file at pdfs/statement-locked.pdf, remove the password "
        "using 'q3-review', and convert it to Word.",
        expected="pdf",
        workspace_files=(LOCKED_PDF,),
    ),
    SkillLoadCase(
        "research-browser-vendors",
        "Research the enterprise browser market using multiple current sources and compare the "
        "leading seven vendors in a sourced table.",
        expected="research-assistant",
        forbidden=("research-report",),
    ),
    SkillLoadCase(
        "research-storage-deep-dive",
        "Do an industry deep dive on grid-scale energy storage and build a bottom-up market-size "
        "estimate from primary sources.",
        expected="research-assistant",
        forbidden=("research-report",),
    ),
    SkillLoadCase(
        "research-robotics-rankings",
        "Compile the official timeline and final rankings for this year's international robotics "
        "competition.",
        expected="research-assistant",
        forbidden=("research-report",),
    ),
    SkillLoadCase(
        "report-from-findings",
        "Turn the research findings in research/findings.md into a concise Markdown report with "
        "an executive summary, findings, and cited sources.",
        expected="research-report",
        forbidden=("research-assistant", "create-skill", "daily-brief"),
        workspace_files=(RESEARCH_FINDINGS,),
    ),
    SkillLoadCase(
        "report-company-ranking",
        "Write an investment-style report comparing the five companies profiled in "
        "research/companies.md and rank them against explicit criteria.",
        expected="research-report",
        forbidden=("research-assistant",),
        workspace_files=(RESEARCH_COMPANIES,),
    ),
    SkillLoadCase(
        "report-technical-sources",
        "Turn the sources in research/sources.md into a deep technical report that separates "
        "confirmed facts, open questions, and analysis.",
        expected="research-report",
        forbidden=("research-assistant",),
        workspace_files=(RESEARCH_SOURCES,),
    ),
    SkillLoadCase(
        "sandbox-run-script",
        "Run scripts/process.py, inspect the generated files, and return the corrected output.",
        expected="sandbox",
        workspace_files=(PROCESS_SCRIPT,),
    ),
    SkillLoadCase(
        "sandbox-explore-workspace",
        "Explore the files under /workspace and summarize the dataset schemas you find there.",
        expected="sandbox",
        workspace_files=(
            WorkspaceFile("datasets/customers.csv", CUSTOMERS_CSV.content),
            WorkspaceFile("datasets/orders.csv", ORDERS_CSV.content),
        ),
    ),
    SkillLoadCase(
        "sandbox-build-project",
        "Build the project under proj/, diagnose any compile errors, and save the working "
        "artifact there.",
        expected="sandbox",
        forbidden=("coding",),
        workspace_files=(PROJ_MAIN, PROJ_README),
    ),
    SkillLoadCase(
        "schedule-daily-escalations",
        "Every weekday at 8:30 AM, send me a summary of new high-priority support escalations.",
        expected="task-scheduling",
        forbidden=("daily-brief",),
    ),
    SkillLoadCase(
        "schedule-weekly-pipeline",
        "Change the weekly pipeline report to run on Monday mornings and alert me only when a "
        "deal becomes at risk.",
        expected="task-scheduling",
    ),
    SkillLoadCase(
        "schedule-cancel-check",
        "Cancel the recurring competitor-news check.",
        expected="task-scheduling",
    ),
    SkillLoadCase(
        "schedule-one-time-reminder",
        "Remind me once tomorrow afternoon to submit the permit application.",
        expected="task-scheduling",
    ),
    SkillLoadCase(
        "schedule-morning-price-check",
        "Set something up that checks our competitor's pricing page every morning and messages "
        "me when it changes.",
        expected="task-scheduling",
        forbidden=("create-application",),
    ),
    SkillLoadCase(
        "theme-board-materials",
        "Create a custom visual theme for our board materials and apply it consistently to a "
        "sample slide and a report cover so I can approve it.",
        expected="theme-factory",
        forbidden=("design-foundations",),
    ),
    SkillLoadCase(
        "theme-healthcare-deck",
        "None of the existing themes fit this healthcare presentation; define a calm, accessible "
        "theme and use it throughout.",
        expected="theme-factory",
        forbidden=("design-foundations",),
    ),
    SkillLoadCase(
        "webapp-inventory",
        "Build a full-stack inventory app with authentication, a persistent database, searchable "
        "products, and an interactive admin dashboard.",
        expected="website-building/webapp",
    ),
    SkillLoadCase(
        "webapp-customer-portal",
        "Create a stateful customer portal with a backend API, account routing, saved data, and "
        "live status updates.",
        expected="website-building/webapp",
    ),
    SkillLoadCase(
        "site-portfolio",
        "Build a polished portfolio site with project pages, responsive navigation, and a "
        "contact form, then test every route in Playwright.",
        expected="website-building",
        forbidden=("website-building/webapp",),
    ),
    SkillLoadCase(
        "site-browser-game",
        "Create an interactive browser game with a score system, keyboard controls, and a "
        "replay flow.",
        expected="website-building",
        forbidden=("website-building/webapp",),
    ),
    SkillLoadCase(
        "site-landing-3d",
        "Design and build a product landing page with an animated 3D hero, pricing, "
        "testimonials, and mobile QA.",
        expected="website-building",
        forbidden=("website-building/webapp",),
    ),
)
