"""The fixture corpus behind `issue_recall`: one repository's issues, pull requests, and comments —
three clusters of genuinely related work plus a bank of distractors, several of them deliberately
near-topic — and the issue-filing prompts graded against them.

Records are GitHub API shaped and turned into page bodies by the real `GitHubConnector`, through the
same `flatten` then `render` pair the connector source adapter applies, so a fixture page body is
what a live GitHub sync would land. That includes the `repo_full_name` the connector's repo fan-out
stamps onto every record it yields, which `flatten` scopes the record id to — so a fixture page is
keyed `<stream>/<repo>/<id>` exactly as a synced one is. A case names its related pages by key;
every other page in the corpus is that case's distractor.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from ufo_ext_sources.providers.github import GitHubConnector

REPO = "northwind/atlas"
ISSUE_ID_BASE = 2_400_000
COMMENT_ID_BASE = 3_900_000
ISSUES = "issues"
PULL_REQUESTS = "pull_requests"
COMMENTS = "comments"


@dataclass(frozen=True)
class IssueThread:
    """One issue or pull request: the fields a GitHub list read returns for it."""

    key: str
    number: int
    stream: str
    title: str
    body: str
    state: str
    author: str
    labels: tuple[str, ...]
    created_at: str
    updated_at: str

    @property
    def source_ref(self) -> str:
        return f"{self.stream}/{REPO}/{ISSUE_ID_BASE + self.number}"

    def record(self) -> dict[str, Any]:
        path = "pull" if self.stream == PULL_REQUESTS else "issues"
        record: dict[str, Any] = {
            "id": ISSUE_ID_BASE + self.number,
            "repo_full_name": REPO,
            "number": self.number,
            "title": self.title,
            "body": self.body,
            "state": self.state,
            "user": {"login": self.author},
            "labels": [{"name": label} for label in self.labels],
            "html_url": f"https://github.com/{REPO}/{path}/{self.number}",
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.stream == PULL_REQUESTS:
            record |= {
                "draft": False,
                "head": {"ref": f"fix/{self.number}", "repo": {"full_name": REPO}},
                "base": {"ref": "main", "repo": {"full_name": REPO}},
            }
        return record


@dataclass(frozen=True)
class Comment:
    """One issue comment: the fields a GitHub comment list read returns for it."""

    key: str
    identifier: int
    thread: int
    body: str
    author: str
    created_at: str

    @property
    def stream(self) -> str:
        return COMMENTS

    @property
    def source_ref(self) -> str:
        return f"{COMMENTS}/{REPO}/{COMMENT_ID_BASE + self.identifier}"

    def record(self) -> dict[str, Any]:
        return {
            "id": COMMENT_ID_BASE + self.identifier,
            "repo_full_name": REPO,
            "issue_url": f"https://api.github.com/repos/{REPO}/issues/{self.thread}",
            "html_url": (
                f"https://github.com/{REPO}/issues/{self.thread}#issuecomment-{self.identifier}"
            ),
            "body": self.body,
            "user": {"login": self.author},
            "created_at": self.created_at,
            "updated_at": self.created_at,
        }


type FixturePage = IssueThread | Comment


TOKEN_CLUSTER: tuple[FixturePage, ...] = (
    IssueThread(
        key="issue-412",
        number=412,
        stream=ISSUES,
        title="atlas sync hangs indefinitely when the OAuth token expires mid-run",
        body=(
            "Our nightly `atlas sync` never returns when the access token expires partway through "
            "the run. The refresh call blocks with no timeout and no error, so the job sits idle "
            "until an operator kills it. Reproduced on 2.14.0 against a tenant with a 15-minute "
            "token lifetime: any sync that crosses the expiry wedges. Expected the refresh to be "
            "bounded and the run to fail loudly instead of hanging."
        ),
        state="open",
        author="priya-n",
        labels=("bug", "sync", "auth"),
        created_at="2026-05-11T09:14:00Z",
        updated_at="2026-06-02T17:31:00Z",
    ),
    IssueThread(
        key="issue-388",
        number=388,
        stream=ISSUES,
        title="Token refresh retries forever after a 401 from the auth endpoint",
        body=(
            "When the auth endpoint answers a refresh with 401, `atlas sync` retries the token "
            "call in a tight loop instead of failing the run. There is no attempt cap and no "
            "backoff ceiling, so the sync never finishes and the logs fill with identical refresh "
            "attempts. Same wedged-sync symptom operators report when a token expires mid-run."
        ),
        state="closed",
        author="dmitri-k",
        labels=("bug", "auth"),
        created_at="2026-03-22T14:02:00Z",
        updated_at="2026-05-30T11:45:00Z",
    ),
    IssueThread(
        key="pull-431",
        number=431,
        stream=PULL_REQUESTS,
        title="Bound the sync token refresh with a deadline",
        body=(
            "Gives `refresh_access_token` a hard deadline and a bounded retry budget, so a sync "
            "whose OAuth token expires mid-run fails with a clear error instead of hanging "
            "forever. Fixes #412 and subsumes the retry loop reported in #388."
        ),
        state="open",
        author="priya-n",
        labels=("sync", "auth"),
        created_at="2026-06-01T08:20:00Z",
        updated_at="2026-06-03T19:05:00Z",
    ),
    Comment(
        key="comment-412",
        identifier=412,
        thread=412,
        body=(
            "Confirmed on our side: `refresh_access_token` has no deadline, so an expired OAuth "
            "token wedges the entire sync run rather than failing it. Tracking the fix in #431; "
            "#388 is the same root cause from the retry side."
        ),
        author="marta-oyelaran",
        created_at="2026-06-02T17:31:00Z",
    ),
)

WEBHOOK_CLUSTER: tuple[FixturePage, ...] = (
    IssueThread(
        key="issue-355",
        number=355,
        stream=ISSUES,
        title="Duplicate webhook deliveries create duplicate order rows",
        body=(
            "When the payment provider retries a webhook delivery, we insert the order a second "
            "time: two rows with the same external reference and the same total. Retries are "
            "expected at-least-once, so the handler needs to be idempotent on the delivery "
            "identifier rather than trusting a single delivery."
        ),
        state="open",
        author="dmitri-k",
        labels=("bug", "webhooks"),
        created_at="2026-02-18T10:05:00Z",
        updated_at="2026-05-19T16:22:00Z",
    ),
    IssueThread(
        key="pull-402",
        number=402,
        stream=PULL_REQUESTS,
        title="Dedupe webhook deliveries by delivery id",
        body=(
            "Records each delivery id on receipt and short-circuits a repeat, so a retried "
            "delivery can no longer write a second order row. Fixes #355."
        ),
        state="open",
        author="lin-chao",
        labels=("webhooks",),
        created_at="2026-05-14T12:41:00Z",
        updated_at="2026-05-19T16:22:00Z",
    ),
    Comment(
        key="comment-355",
        identifier=355,
        thread=355,
        body=(
            "We see the duplicate order rows on every provider retry storm. The delivery id is "
            "already on the payload, so deduping on it is the fix — see #402."
        ),
        author="priya-n",
        created_at="2026-05-19T16:22:00Z",
    ),
)

EXPORT_CLUSTER: tuple[FixturePage, ...] = (
    IssueThread(
        key="issue-297",
        number=297,
        stream=ISSUES,
        title="CSV export silently truncates at 10,000 rows",
        body=(
            "The order CSV export stops at exactly 10,000 rows with no warning in the file, the "
            "UI, or the logs. Accounts with more history get a file that simply ends mid-history, "
            "and there is no pagination or streaming path for a larger export."
        ),
        state="open",
        author="marta-oyelaran",
        labels=("bug", "exports"),
        created_at="2026-01-09T13:30:00Z",
        updated_at="2026-04-27T09:12:00Z",
    ),
    Comment(
        key="comment-297",
        identifier=297,
        thread=297,
        body=(
            "The 10,000-row cap is the exporter's hard page size; anything past it is dropped "
            "silently. Any fix has to stream the export and tell the member when a file is partial."
        ),
        author="lin-chao",
        created_at="2026-04-27T09:12:00Z",
    ),
)

DISTRACTORS: tuple[FixturePage, ...] = (
    IssueThread(
        key="issue-466",
        number=466,
        stream=ISSUES,
        title="atlas sync is slow on catalogs over 500k SKUs",
        body=(
            "A full `atlas sync` of a 500k-SKU catalog takes over four hours, most of it in the "
            "per-SKU price lookup. The run completes correctly; it is throughput we need, ideally "
            "batched lookups and a wider fetch concurrency."
        ),
        state="open",
        author="lin-chao",
        labels=("performance", "sync"),
        created_at="2026-06-04T07:55:00Z",
        updated_at="2026-06-14T15:10:00Z",
    ),
    IssueThread(
        key="issue-372",
        number=372,
        stream=ISSUES,
        title="Add SSO via Okta for dashboard sign-in",
        body=(
            "Enterprise tenants want SAML single sign-on through Okta for dashboard sign-in, with "
            "group-to-role mapping on first login. Today every seat is a local password account."
        ),
        state="open",
        author="dmitri-k",
        labels=("feature", "auth"),
        created_at="2026-03-02T11:11:00Z",
        updated_at="2026-04-08T08:40:00Z",
    ),
    IssueThread(
        key="issue-360",
        number=360,
        stream=ISSUES,
        title="Webhook signature docs use the old header name",
        body=(
            "The webhook verification page still documents `X-Atlas-Sign`, renamed to "
            "`X-Atlas-Signature` in 2.9. Integrators following the docs verify against a header "
            "that is never sent and reject every delivery."
        ),
        state="closed",
        author="marta-oyelaran",
        labels=("docs", "webhooks"),
        created_at="2026-02-25T09:00:00Z",
        updated_at="2026-03-04T10:15:00Z",
    ),
    IssueThread(
        key="issue-501",
        number=501,
        stream=ISSUES,
        title="Docs: quickstart links to a removed page",
        body=(
            "The quickstart's second step links to /guides/legacy-setup, which 404s since the "
            "docs restructure. New tenants hit it on their first read."
        ),
        state="open",
        author="lin-chao",
        labels=("docs",),
        created_at="2026-06-21T16:44:00Z",
        updated_at="2026-06-22T09:02:00Z",
    ),
    IssueThread(
        key="issue-478",
        number=478,
        stream=ISSUES,
        title="Dark mode for the dashboard",
        body=(
            "Operators watching the dashboard overnight want a dark theme that follows the system "
            "preference, including the charts and the order table."
        ),
        state="open",
        author="priya-n",
        labels=("feature", "ui"),
        created_at="2026-06-12T20:18:00Z",
        updated_at="2026-06-13T07:30:00Z",
    ),
    IssueThread(
        key="issue-455",
        number=455,
        stream=ISSUES,
        title="Windows CI job flakes on the integration suite",
        body=(
            "The Windows integration job fails roughly one run in four on a temporary-directory "
            "cleanup error. Reruns pass, which is masking real failures in the same job."
        ),
        state="open",
        author="dmitri-k",
        labels=("ci", "flaky"),
        created_at="2026-05-28T13:07:00Z",
        updated_at="2026-06-09T18:52:00Z",
    ),
    IssueThread(
        key="issue-440",
        number=440,
        stream=ISSUES,
        title="Billing dashboard p95 latency regressed after the June deploy",
        body=(
            "Billing dashboard p95 went from 380ms to 2.1s right after the June 5 deploy. The "
            "invoice aggregate query is the hot spot; nothing else in that release touched it."
        ),
        state="open",
        author="marta-oyelaran",
        labels=("performance", "billing"),
        created_at="2026-06-06T09:36:00Z",
        updated_at="2026-06-11T14:20:00Z",
    ),
    IssueThread(
        key="issue-421",
        number=421,
        stream=ISSUES,
        title="Mobile layout overlaps the order total",
        body=(
            "On viewports under 380px the order total sits on top of the tax line, so the amount "
            "due is unreadable on a phone."
        ),
        state="closed",
        author="lin-chao",
        labels=("ui", "mobile"),
        created_at="2026-05-04T10:48:00Z",
        updated_at="2026-05-16T12:00:00Z",
    ),
    IssueThread(
        key="issue-390",
        number=390,
        stream=ISSUES,
        title="Release notes for 2.13 omit the migration step",
        body=(
            "The 2.13 notes never mention the required `atlas migrate` run, so upgrades land on a "
            "schema the release expects to have changed."
        ),
        state="closed",
        author="priya-n",
        labels=("docs", "release"),
        created_at="2026-03-19T08:25:00Z",
        updated_at="2026-03-21T09:41:00Z",
    ),
    IssueThread(
        key="pull-522",
        number=522,
        stream=PULL_REQUESTS,
        title="Bump pydantic to 2.11 and regenerate the lockfile",
        body=(
            "Routine dependency bump: pydantic 2.10 to 2.11, lockfile regenerated, no runtime "
            "code touched."
        ),
        state="open",
        author="lin-chao",
        labels=("dependencies",),
        created_at="2026-06-24T11:02:00Z",
        updated_at="2026-06-24T11:02:00Z",
    ),
    IssueThread(
        key="pull-509",
        number=509,
        stream=PULL_REQUESTS,
        title="Add a --verbose flag to the CLI",
        body=(
            "Adds `--verbose` to every CLI verb, promoting the per-request debug logs from the "
            "environment variable to a flag."
        ),
        state="open",
        author="dmitri-k",
        labels=("cli",),
        created_at="2026-06-18T15:33:00Z",
        updated_at="2026-06-20T08:14:00Z",
    ),
    IssueThread(
        key="pull-498",
        number=498,
        stream=PULL_REQUESTS,
        title="Translate the onboarding emails to French",
        body=(
            "Adds fr-FR copy for the four onboarding emails and selects it from the member's "
            "locale."
        ),
        state="closed",
        author="marta-oyelaran",
        labels=("i18n",),
        created_at="2026-06-15T09:27:00Z",
        updated_at="2026-06-17T16:48:00Z",
    ),
    IssueThread(
        key="pull-487",
        number=487,
        stream=PULL_REQUESTS,
        title="Cache the CI dependency layer",
        body=(
            "Caches the resolved dependency layer between CI runs, cutting about six minutes off "
            "a green build."
        ),
        state="open",
        author="priya-n",
        labels=("ci",),
        created_at="2026-06-10T12:19:00Z",
        updated_at="2026-06-10T17:05:00Z",
    ),
    Comment(
        key="comment-466",
        identifier=466,
        thread=466,
        body=(
            "Profiled a 500k-SKU run: 78% of wall clock is the per-SKU price lookup. Batching it "
            "1,000 at a time takes the run under an hour in a local test."
        ),
        author="dmitri-k",
        created_at="2026-06-14T15:10:00Z",
    ),
)

PAGES: tuple[FixturePage, ...] = (
    *TOKEN_CLUSTER,
    *WEBHOOK_CLUSTER,
    *EXPORT_CLUSTER,
    *DISTRACTORS,
)


@dataclass(frozen=True)
class Ambient:
    """One ambient workspace memory: durable memory a real member's workspace holds that no fixture
    page produced. The bank exists to make the pool realistic — recall injects a fixed number of
    memories, so a corpus of only the graded evidence hands retrieval a third of itself and a
    coverage bar becomes near-chance. The hand-written entries are near-topic on purpose: they carry
    the filing topics' vocabulary — tokens, refreshes, webhook retries, ten-thousand-row caps —
    about other systems entirely, so discrimination is tested rather than topic detection."""

    ref: str
    body: str


HARD_NEGATIVES: tuple[Ambient, ...] = (
    Ambient(
        "auth-okta-lifetime",
        "The Okta session token for dashboard sign-in lives twelve hours and refreshes silently.",
    ),
    Ambient(
        "auth-warehouse-rotation",
        "Rotating the warehouse service account token needs a support ticket; it is not "
        "self-serve.",
    ),
    Ambient(
        "auth-mobile-refresh",
        "The mobile app's refresh-token flow was rewritten in March and shares no code with the "
        "CLI.",
    ),
    Ambient(
        "auth-media-retry",
        "A token expiring mid-upload in the media pipeline is retried by the uploader itself, "
        "under a bound.",
    ),
    Ambient(
        "auth-issuance-owner",
        "The auth team owns token issuance; the sync service only ever consumes a token.",
    ),
    Ambient(
        "auth-token-audit",
        "Every token refresh is audited to the security log with the requesting service and the "
        "grant.",
    ),
    Ambient(
        "auth-cli-device-code",
        "The CLI signs in with the device-code grant, so it never holds a password.",
    ),
    Ambient(
        "auth-scope-review",
        "Quarterly scope review trims unused OAuth scopes from every first-party client.",
    ),
    Ambient(
        "auth-expired-cert",
        "An expired client certificate on the partner gateway looks like an auth failure but is a "
        "TLS one.",
    ),
    Ambient(
        "auth-service-mesh",
        "Service-to-service calls inside the mesh authenticate with SPIFFE identities, not tokens.",
    ),
    Ambient(
        "sync-catalog-schedule",
        "The catalog sync and the price sync are separate jobs on separate schedules.",
    ),
    Ambient(
        "sync-ledger-nightly",
        "The ledger sync runs at 02:00 UTC and is expected to finish inside twenty minutes.",
    ),
    Ambient(
        "sync-partner-sftp",
        "Partner inventory arrives over SFTP nightly and is reconciled by a separate job.",
    ),
    Ambient(
        "sync-dry-run", "`atlas sync --dry-run` reports what would change without writing anything."
    ),
    Ambient(
        "sync-backfill-window",
        "A sync backfill wider than thirty days has to be approved by the data team.",
    ),
    Ambient(
        "sync-cursor-storage",
        "Sync cursors live in the jobs database, never in the application tables.",
    ),
    Ambient(
        "sync-hang-runbook",
        "The runbook for a stalled job says capture a thread dump before restarting it.",
    ),
    Ambient(
        "sync-metrics-lag",
        "Sync lag is the dashboard's headline metric; anything past fifteen minutes pages on-call.",
    ),
    Ambient(
        "webhook-stripe-secret",
        "The Stripe webhook signing secret rotates every ninety days and lives in the platform "
        "vault.",
    ),
    Ambient(
        "webhook-slack-retries",
        "Slack caps webhook delivery retries at three; that bound is Slack's, not ours.",
    ),
    Ambient(
        "webhook-gateway-move",
        "The billing webhook endpoint moved behind the gateway in June and the old host still "
        "resolves.",
    ),
    Ambient(
        "webhook-replay-tool",
        "Support can replay a webhook delivery from the admin tool, which re-sends the original "
        "payload.",
    ),
    Ambient(
        "webhook-order-of-arrival",
        "Webhook deliveries arrive out of order under load, so handlers must not assume sequence.",
    ),
    Ambient(
        "webhook-timeout-budget",
        "A webhook handler has five seconds to answer before the provider counts it a failure.",
    ),
    Ambient(
        "webhook-signature-clock",
        "Webhook signature checks fail when the host clock drifts more than five minutes.",
    ),
    Ambient(
        "webhook-duplicate-emails",
        "Duplicate welcome emails last quarter came from a queue redelivery, not from webhooks.",
    ),
    Ambient(
        "export-warehouse-owner",
        "The finance CSV export is produced nightly by the data warehouse, not by the application.",
    ),
    Ambient(
        "export-starter-plan",
        "Members on the starter plan cannot export at all; the button is hidden for them.",
    ),
    Ambient(
        "export-audit-page-size",
        "The audit-log viewer pages at ten thousand rows, which is a UI page size and not an "
        "export limit.",
    ),
    Ambient(
        "export-xlsx-cap",
        "The XLSX export is capped at one million rows by the spreadsheet format itself.",
    ),
    Ambient(
        "export-async-delivery",
        "Any export over fifty megabytes is delivered as an emailed link rather than a download.",
    ),
    Ambient(
        "export-timezone",
        "Export timestamps render in the workspace timezone, which trips up teams reading them in "
        "UTC.",
    ),
    Ambient(
        "export-pii-redaction",
        "Customer exports redact payment instrument numbers before the file is written.",
    ),
    Ambient(
        "export-scheduled-reports",
        "Scheduled reports are a separate feature from exports and share none of the code.",
    ),
    Ambient(
        "truncation-log-tail",
        "The log viewer truncates a line past four kilobytes and marks it with an ellipsis.",
    ),
    Ambient(
        "truncation-api-page",
        "List endpoints page at one hundred records and never silently drop the remainder.",
    ),
    Ambient(
        "duplicate-idempotency-keys",
        "Payment intents carry an idempotency key so a retried charge cannot double-bill.",
    ),
    Ambient(
        "duplicate-dedupe-window",
        "The events pipeline dedupes on message id inside a fifteen-minute window.",
    ),
    Ambient(
        "hang-deadlock-postmortem",
        "The February incident was a database deadlock, not a hang, and the postmortem says so.",
    ),
    Ambient(
        "hang-thread-pool",
        "A saturated thread pool presents as a hang; the pool's queue depth is the tell.",
    ),
)

SIBLING_REPOS: tuple[str, ...] = (
    "northwind/ledger",
    "northwind/storefront",
    "northwind/warehouse",
    "northwind/notify",
    "northwind/gateway",
)

ISSUE_SHAPED: tuple[Ambient, ...] = (
    Ambient(
        "ledger-88",
        "northwind/ledger issue #88 (open, filed 2026-04-02 by dmitri-k) reports the nightly "
        "reconciliation job stalling whenever the FX rate feed publishes late, with no timeout on "
        "the feed read.",
    ),
    Ambient(
        "ledger-91",
        "northwind/ledger issue #91 (open) reports invoice PDFs rendering blank in Safari 17 while "
        "Chrome and Firefox render them correctly.",
    ),
    Ambient(
        "ledger-104",
        "northwind/ledger PR #104 by lin-chao (merged 2026-05-08) moves tax rounding to banker's "
        "rounding so cent-level totals stop drifting from the provider's figures.",
    ),
    Ambient(
        "ledger-117",
        "northwind/ledger issue #117 (closed) reported the journal export omitting the closing "
        "balance row when a period ended on a weekend.",
    ),
    Ambient(
        "ledger-125",
        "northwind/ledger issue #125 (open) tracks an N+1 query on the invoice list that adds a "
        "second per hundred rows.",
    ),
    Ambient(
        "ledger-133",
        "northwind/ledger PR #133 by priya-n (open) adds an idempotency key to refund submission "
        "so a retried refund cannot double-credit an account.",
    ),
    Ambient(
        "ledger-140",
        "northwind/ledger issue #140 (open) reports the period-close job holding a table lock for "
        "eleven minutes, blocking invoice writes for its duration.",
    ),
    Ambient(
        "ledger-146",
        "northwind/ledger issue #146 (closed) reported coupon stacking allowing two percentage "
        "discounts on one order line.",
    ),
    Ambient(
        "ledger-152",
        "northwind/ledger issue #152 (open) reports the currency selector defaulting to USD for "
        "European accounts after the settings rewrite.",
    ),
    Ambient(
        "ledger-158",
        "northwind/ledger PR #158 by marta-oyelaran (merged) backfills missing statement "
        "descriptors on historical charges.",
    ),
    Ambient(
        "storefront-212",
        "northwind/storefront issue #212 (open, filed 2026-03-19 by lin-chao) reports the cart "
        "badge keeping a stale count until a hard refresh because the cache is never invalidated "
        "on removal.",
    ),
    Ambient(
        "storefront-219",
        "northwind/storefront issue #219 (open) reports product search returning nothing for terms "
        "with an apostrophe, which the analyzer strips before indexing.",
    ),
    Ambient(
        "storefront-224",
        "northwind/storefront PR #224 by dmitri-k (merged 2026-04-30) lazy-loads gallery "
        "thumbnails and cuts first paint on the product page by 900ms.",
    ),
    Ambient(
        "storefront-231",
        "northwind/storefront issue #231 (closed) reported the address form rejecting valid Irish "
        "Eircodes because the postcode pattern assumed a numeric format.",
    ),
    Ambient(
        "storefront-238",
        "northwind/storefront issue #238 (open) reports the checkout button double-submitting on "
        "slow connections, which the payment provider's idempotency key absorbs.",
    ),
    Ambient(
        "storefront-244",
        "northwind/storefront issue #244 (open) tracks a memory leak in the recommendation widget "
        "that grows the tab to 1.4GB over an hour of browsing.",
    ),
    Ambient(
        "storefront-250",
        "northwind/storefront PR #250 by priya-n (open) replaces the hand-rolled image resizer "
        "with the platform thumbnail service.",
    ),
    Ambient(
        "storefront-257",
        "northwind/storefront issue #257 (closed) reported the size chart modal trapping keyboard "
        "focus so screen-reader users could not dismiss it.",
    ),
    Ambient(
        "storefront-263",
        "northwind/storefront issue #263 (open) reports variant thumbnails loading at full "
        "resolution on mobile, costing about 4MB per product view.",
    ),
    Ambient(
        "storefront-270",
        "northwind/storefront issue #270 (open) reports the promo banner flashing the untranslated "
        "string for a moment before hydration replaces it.",
    ),
    Ambient(
        "warehouse-305",
        "northwind/warehouse issue #305 (open, filed 2026-02-27 by marta-oyelaran) reports the "
        "nightly stock count hanging when the label printer queue is offline, since the printer "
        "call has no deadline.",
    ),
    Ambient(
        "warehouse-311",
        "northwind/warehouse issue #311 (open) reports inventory reservations leaking when a pick "
        "is cancelled mid-flight, so stock stays reserved until the sweeper runs.",
    ),
    Ambient(
        "warehouse-318",
        "northwind/warehouse PR #318 by lin-chao (merged 2026-05-21) batches bin-location lookups "
        "a thousand at a time and takes a full recount under an hour.",
    ),
    Ambient(
        "warehouse-324",
        "northwind/warehouse issue #324 (closed) reported the pick list printing in bin order "
        "rather than route order, adding minutes to every wave.",
    ),
    Ambient(
        "warehouse-330",
        "northwind/warehouse issue #330 (open) reports the barcode scanner app losing its session "
        "after fifteen minutes of inactivity and dropping the in-progress count.",
    ),
    Ambient(
        "warehouse-337",
        "northwind/warehouse issue #337 (open) tracks receiving accepting a negative quantity, "
        "which then propagates into the reconciliation report.",
    ),
    Ambient(
        "warehouse-343",
        "northwind/warehouse PR #343 by dmitri-k (open) adds a cycle-count schedule per zone "
        "rather than one global nightly pass.",
    ),
    Ambient(
        "warehouse-350",
        "northwind/warehouse issue #350 (open) reports the pallet label PDF cutting off the last "
        "character of long SKU codes.",
    ),
    Ambient(
        "warehouse-356",
        "northwind/warehouse issue #356 (closed) reported the dock-door dashboard showing "
        "yesterday totals until the shift rolled over.",
    ),
    Ambient(
        "warehouse-362",
        "northwind/warehouse issue #362 (open) reports the transfer job retrying forever against a "
        "decommissioned site code instead of failing the transfer.",
    ),
    Ambient(
        "notify-401",
        "northwind/notify issue #401 (open, filed 2026-03-08 by priya-n) reports the daily digest "
        "email sending twice on the spring DST boundary because the scheduler evaluates the local "
        "hour.",
    ),
    Ambient(
        "notify-408",
        "northwind/notify issue #408 (open) reports push notifications arriving for muted threads "
        "when the mute is set from the mobile client.",
    ),
    Ambient(
        "notify-414",
        "northwind/notify PR #414 by marta-oyelaran (merged 2026-04-17) collapses per-comment "
        "emails into one thread summary after the third message.",
    ),
    Ambient(
        "notify-420",
        "northwind/notify issue #420 (closed) reported unsubscribe links expiring after seven "
        "days, so an old newsletter could not be unsubscribed from at all.",
    ),
    Ambient(
        "notify-427",
        "northwind/notify issue #427 (open) reports the SMS fallback firing before the push has "
        "had a chance to deliver, so members get both.",
    ),
    Ambient(
        "notify-433",
        "northwind/notify issue #433 (open) tracks template rendering dropping emoji from subject "
        "lines on the SES transport.",
    ),
    Ambient(
        "notify-440",
        "northwind/notify PR #440 by lin-chao (open) moves digest assembly off the request path "
        "and onto the scheduled worker.",
    ),
    Ambient(
        "notify-446",
        "northwind/notify issue #446 (open) reports quiet hours being ignored for members whose "
        "profile timezone is unset, defaulting them to UTC.",
    ),
    Ambient(
        "notify-452",
        "northwind/notify issue #452 (closed) reported the digest counting archived items in its "
        "unread total.",
    ),
    Ambient(
        "notify-459",
        "northwind/notify issue #459 (open) reports a bounce for one recipient marking the whole "
        "batch failed in the delivery log.",
    ),
    Ambient(
        "gateway-503",
        "northwind/gateway issue #503 (open, filed 2026-05-02 by dmitri-k) reports the rate "
        "limiter counting preflight OPTIONS requests against a member's quota.",
    ),
    Ambient(
        "gateway-509",
        "northwind/gateway issue #509 (open) reports upstream connection pools saturating under a "
        "traffic spike and presenting as a hang rather than a shed load.",
    ),
    Ambient(
        "gateway-516",
        "northwind/gateway PR #516 by priya-n (merged 2026-05-25) pins the TLS cipher suite list "
        "so older partner clients stop failing the handshake.",
    ),
    Ambient(
        "gateway-522",
        "northwind/gateway issue #522 (closed) reported request ids not propagating to upstreams, "
        "which broke trace stitching across services.",
    ),
    Ambient(
        "gateway-528",
        "northwind/gateway issue #528 (open) reports the health probe passing while the upstream "
        "pool is empty, so a dead replica keeps taking traffic.",
    ),
    Ambient(
        "gateway-535",
        "northwind/gateway issue #535 (open) tracks a 30-second read timeout being applied to "
        "streaming responses, which cuts long downloads.",
    ),
    Ambient(
        "gateway-541",
        "northwind/gateway PR #541 by marta-oyelaran (open) adds per-route concurrency limits "
        "ahead of the shared worker pool.",
    ),
    Ambient(
        "gateway-548",
        "northwind/gateway issue #548 (open) reports gzip being applied twice to "
        "already-compressed payloads from one upstream.",
    ),
    Ambient(
        "gateway-554",
        "northwind/gateway issue #554 (closed) reported the access log recording the proxy address "
        "rather than the client's forwarded address.",
    ),
    Ambient(
        "gateway-561",
        "northwind/gateway issue #561 (open) reports a stale DNS answer being held for an hour "
        "after an upstream moved, long past its TTL.",
    ),
    Ambient(
        "ledger-165",
        "northwind/ledger issue #165 (open) reports the statement job producing an empty file when "
        "an account has exactly one transaction in the period.",
    ),
    Ambient(
        "storefront-277",
        "northwind/storefront issue #277 (open) reports the wishlist silently capping at 200 items "
        "with no message to the member.",
    ),
    Ambient(
        "warehouse-369",
        "northwind/warehouse issue #369 (open) reports the audit trail keeping only 90 days, which "
        "is short of the contractual retention.",
    ),
    Ambient(
        "notify-465",
        "northwind/notify issue #465 (open) reports the weekly report attaching a CSV that stops "
        "at 5,000 rows without saying so.",
    ),
    Ambient(
        "gateway-567",
        "northwind/gateway issue #567 (open) reports a partner integration re-sending the same "
        "callback three times when our acknowledgement is slow.",
    ),
    Ambient(
        "ledger-172",
        "northwind/ledger issue #172 (open) reports the settlement fetch retrying without a "
        "ceiling when the bank endpoint answers 503.",
    ),
    Ambient(
        "storefront-284",
        "northwind/storefront issue #284 (closed) reported session tokens for the storefront being "
        "refreshed on every request rather than near expiry.",
    ),
    Ambient(
        "warehouse-375",
        "northwind/warehouse issue #375 (open) reports the supplier feed sync finishing while "
        "silently skipping rows whose unit of measure is unrecognised.",
    ),
    Ambient(
        "notify-471",
        "northwind/notify issue #471 (open) reports webhook callbacks to customer endpoints being "
        "retried five times with no backoff.",
    ),
    Ambient(
        "gateway-573",
        "northwind/gateway issue #573 (open) reports the OAuth introspection call blocking the "
        "request thread when the auth service is slow to answer.",
    ),
)


OPS_LEDGER_HEAD = (
    "Atlas operations review ledger — reviews already run (canonical ledger). This is the SINGLE "
    "canonical ledger; update it in place each run (overwrite, never append a second copy). "
    "Reviews post to the operations channel after each session.\n"
)

OPS_LEDGER_ENTRIES: tuple[str, ...] = (
    "(01) 2026-06-16 — 'OAuth refresh path' (walked the sync worker's token refresh under an "
    "expiring credential: deadline behavior, retry budget, and what an operator sees when a "
    "refresh stalls; services/sync/auth.py, runbooks/token-refresh.md).",
    "(02) 2026-06-19 — 'webhook intake' (at-least-once delivery from the payment provider, the "
    "provider-owned retry schedule, and the idempotency expectations on the order writer; "
    "services/orders/webhooks.py, runbooks/webhook-replay.md).",
    "(03) 2026-06-23 — 'export pipeline' (the exporter's hard page size, replica lag while a "
    "bulk import runs, and when a CSV file counts as partial; services/exports/csv.py, "
    "runbooks/export-rerun.md).",
    "(04) 2026-06-26 — 'catalog sync throughput' (per-SKU price lookups, batched reads, and the "
    "fetch concurrency ceiling; services/sync/catalog.py).",
    "(05) 2026-06-30 — 'sync cursor storage' (cursors live in the jobs database, replaying a "
    "window, and who approves a wide backfill; services/sync/cursors.py).",
    "(06) 2026-07-03 — 'dashboard metrics' (sync lag as the headline metric, paging thresholds, "
    "and the on-call rotation that owns them; ops/dashboards/sync.json).",
    "(07) 2026-07-07 — 'partner SFTP intake' (the nightly inventory drop, the reconciliation "
    "job, and late-file handling; services/intake/sftp.py).",
    "(08) 2026-07-10 — 'thread-pool saturation' (queue depth as the tell for a hang, and the "
    "dump-before-restart rule for a stalled job; runbooks/stalled-job.md).",
    "(09) 2026-07-14 — 'token audit trail' (who requested a refresh, under which grant, and "
    "where the security log keeps it; services/auth/audit.py).",
    "(10) 2026-07-17 — 'duplicate suppression' (the message-id dedupe window in the events "
    "pipeline and what falls outside it; services/events/dedupe.py).",
    "(11) 2026-07-21 — 'export streaming plan' (a streaming writer for large accounts and "
    "telling the member when a file is partial; services/exports/stream.py).",
    "(12) 2026-07-24 — 'webhook replay tooling' (support-side replay of a delivery and what the "
    "original payload means on a second receipt; tools/admin/replay.py).",
    "(13) 2026-07-28 — 'token lifetime matrix' (which tenant tiers run fifteen-minute access "
    "tokens versus hour-long ones, and what each means for a long sync run crossing an expiry; "
    "services/auth/lifetimes.py, docs/tenancy.md).",
    "(14) 2026-07-31 — 'order reconciliation' (how the orders table is reconciled against the "
    "provider's settlement report, and which mismatches page a human versus queue for review; "
    "services/orders/reconcile.py, runbooks/settlement-mismatch.md).",
    "(15) 2026-08-04 — 'export surfaces' (every place a member can start an export — the UI, "
    "the reporting API, the scheduled weekly report — and which of them share the exporter's "
    "page size; services/exports/api.py, web/reports/export.tsx).",
    "(16) 2026-08-07 — 'retry backoff policy' (the house rule that retries against an external "
    "party carry exponential backoff and a ceiling, and the audit of call sites that predate "
    "it; docs/retry-policy.md).",
    "(17) 2026-08-11 — 'escalation ladder' (when a stalled nightly job pages the secondary, "
    "when it wakes the service owner, and the fifteen-minute acknowledgement bar; "
    "ops/escalation.md).",
    "(18) 2026-08-14 — 'credential rotation drill' (rotated the warehouse service account and "
    "the payment provider's signing secret in staging, timing every dependent restart; "
    "runbooks/rotation-drill.md).",
)

OPS_LEDGER_GUIDANCE = (
    "\nRECALL GUIDANCE (learned the hard way): a generic memory search for this ledger returns "
    "STALE copies stopping at entries 05-07, and even a targeted search may return a stale copy "
    "first — always scan every returned copy for the HIGHEST entry number before trusting one. "
    "When the ledger is already present in the turn's recalled-memory block with the highest "
    "entry number, that copy is current and no search is needed."
)

OPS_LEDGER_COPIES: tuple[Ambient, ...] = (
    Ambient(
        "ops-ledger-current",
        OPS_LEDGER_HEAD + "\n".join(OPS_LEDGER_ENTRIES) + "\nSTILL UNREVIEWED candidates: "
        "'auth service mesh identities', 'nightly reconciliation', 'billing gateway move'."
        + OPS_LEDGER_GUIDANCE,
    ),
    Ambient(
        "ops-ledger-stale",
        OPS_LEDGER_HEAD + "\n".join(OPS_LEDGER_ENTRIES[:5]) + "\nSTILL UNREVIEWED candidates: "
        "'dashboard metrics', 'partner SFTP intake'.",
    ),
)

STALE_SNAPSHOTS: tuple[Ambient, ...] = (
    Ambient(
        "snapshot-triage-july",
        "As of 2026-07-20 the atlas triage board counted 114 open items: 61 sync, 29 webhooks, "
        "24 exports; the oldest open item dated to January.",
    ),
    Ambient(
        "snapshot-triage-august",
        "As of 2026-08-08 the atlas triage board counted 131 open items: 66 sync, 33 webhooks, "
        "32 exports; the oldest open item dated to January.",
    ),
)

DUPLICATE_FAMILIES: tuple[tuple[Ambient, ...], ...] = (
    (
        Ambient(
            "dupe-refresh-owner-1",
            "The sync worker refreshes its own OAuth token in process; there is no standalone "
            "token-refresh service to restart when a refresh goes bad.",
        ),
        Ambient(
            "dupe-refresh-owner-2",
            "Re-confirmed 2026-08-14 against the deploy manifest: still no standalone "
            "token-refresh service — the sync worker refreshes its OAuth token in process.",
        ),
        Ambient(
            "dupe-refresh-owner-3",
            "There is no token-refresh sidecar in the atlas deploy (checked the manifest again), "
            "so a bad token is fixed inside the sync worker, never by restarting a service.",
        ),
    ),
    (
        Ambient(
            "dupe-retry-owner-1",
            "The payment provider owns the webhook retry schedule; deliveries repeat "
            "at-least-once and nothing on our side can turn a retry off.",
        ),
        Ambient(
            "dupe-retry-owner-2",
            "Re-confirmed 2026-08-11 with the provider's support: webhook delivery retries are "
            "the provider's, at-least-once, and not configurable from our side.",
        ),
        Ambient(
            "dupe-retry-owner-3",
            "Webhook retries come from the payment provider on the provider's own schedule "
            "(at-least-once), so turning them off is not an option a handler has.",
        ),
    ),
    (
        Ambient(
            "dupe-export-replica-1",
            "The order export reads from the reporting replica, which lags the primary by up to "
            "ten minutes during a bulk import.",
        ),
        Ambient(
            "dupe-export-replica-2",
            "Re-confirmed 2026-08-09: the order export still reads the reporting replica, up to "
            "ten minutes behind the primary while a bulk import runs.",
        ),
        Ambient(
            "dupe-export-replica-3",
            "The export path reads the reporting replica (lag up to ten minutes under bulk "
            "import), never the primary.",
        ),
    ),
    OPS_LEDGER_COPIES,
    STALE_SNAPSHOTS,
)


AMBIENT_SUBJECTS: tuple[str, ...] = (
    "Loomcart",
    "Trellis",
    "Pinecrest",
    "Halyard",
    "Brightsill",
    "Quarrymark",
    "Fenwood",
    "Oxbow",
    "Saltgate",
    "Marlowe",
    "Ridgepole",
    "Cobbleway",
    "Thistledown",
    "Everwick",
    "Granby",
)

AMBIENT_CLAIMS: tuple[str, ...] = (
    "{subject} runs its production workload in eu-west-1 with a warm standby in us-east-1.",
    "{subject} is invoiced net-30 on the first business day of the month by the finance team.",
    "The {subject} account is a design partner and sees release notes a week before general "
    "availability.",
    "{subject} asked for SOC 2 evidence in the last security review and received the current "
    "report.",
    "The {subject} integration is maintained by the partnerships team, not by platform.",
    "{subject} renewed for two years in the last quarter at a negotiated discount.",
    "Support escalations from {subject} route to the named account engineer rather than the queue.",
    "{subject} runs an on-premise connector, so their upgrade window is coordinated by hand.",
    "The {subject} pilot measured activation rate as its single success metric.",
    "{subject} keeps their own status page and expects incident notes within an hour.",
    "Legal reviewed the {subject} data-processing addendum and it is countersigned.",
    "{subject} has a hard requirement that data never leaves the European Union.",
    "The quarterly business review with {subject} is owned by the account team and runs each "
    "January.",
    "{subject} was migrated off the legacy billing plan and is now metered per seat.",
    "The {subject} sandbox is refreshed from production weekly with customer data scrubbed.",
    "{subject} reports through the shared analytics workspace rather than their own dashboards.",
    "Two engineers from {subject} sit in the shared Slack Connect channel for escalations.",
    "The {subject} rollout was staged by region and finished ahead of the planned date.",
    "{subject} declined the beta program and stays on the general availability track.",
    "Procurement at {subject} requires a purchase order number on every invoice.",
    "{subject} runs their own identity provider and federates in through SAML.",
    "The {subject} onboarding took three weeks, most of it waiting on their security "
    "questionnaire.",
    "{subject} has a standing request for a monthly usage summary in their own template.",
    "The {subject} contract caps annual price increases at five percent.",
)


def ambient_memories() -> tuple[Ambient, ...]:
    """The haystack in stable order: the near-topic hard negatives, the duplicate families — live
    copies of one evolving fact, the shape a production memory store accretes when the same fact is
    re-written across sessions (triplicated restatements, a giant update-in-place ledger and its
    stale earlier copy, a point-in-time snapshot beside its revision) — the issue-shaped bank from
    the sibling repositories, then the generated ambient bank."""
    generated = tuple(
        Ambient(
            ref=f"ambient/{index:03d}",
            body=claim.format(subject=subject),
        )
        for index, (claim, subject) in enumerate(
            (claim, subject) for claim in AMBIENT_CLAIMS for subject in AMBIENT_SUBJECTS
        )
    )
    duplicated = tuple(memory for family in DUPLICATE_FAMILIES for memory in family)
    memories = HARD_NEGATIVES + duplicated + ISSUE_SHAPED + generated
    refs = tuple(memory.ref for memory in memories)
    if len(set(refs)) != len(refs):
        raise ValueError("issue_recall ambient memories share a ref")
    bodies = tuple(memory.body for memory in memories)
    if len(set(bodies)) != len(bodies):
        raise ValueError("issue_recall ambient memories share a body")
    return memories


@dataclass(frozen=True)
class MidThread:
    """The same topic reached mid-conversation: four turns of ordinary technical Q&A the member is
    already in, then the terse ask that is the graded turn. The recall hook embeds only the graded
    turn's inbound, so the thread above it contributes nothing to retrieval — that one ask is the
    whole query, and it names the subject in the member's own words rather than the corpus's."""

    prior_messages: tuple[str, ...]
    ask: str


@dataclass(frozen=True)
class Filing:
    """One filing topic: the symptom a member reports in their own words, the corpus pages a default
    recall must surface for it, and the case's bar — the majority of that related set, so a set of
    four may lose at most one page. Every page outside `related` is a distractor for this topic,
    including the other topics' clusters. The same bar grades the topic's mid-conversation ask and
    its source-alert ask, where `alert_page` names the related page the alert reports changed."""

    slug: str
    symptom: str
    related: tuple[str, ...]
    min_coverage: float
    mid_thread: MidThread
    alert_page: str


FILINGS: tuple[Filing, ...] = (
    Filing(
        slug="sync-token-hang",
        symptom=(
            "our nightly `atlas sync` wedges forever whenever the OAuth token expires partway "
            "through the run — no timeout, no error, the job just sits there until we kill it"
        ),
        related=("issue-412", "issue-388", "pull-431", "comment-412"),
        min_coverage=0.75,
        mid_thread=MidThread(
            prior_messages=(
                "what does `asyncio.wait_for` actually do to the coroutine when the timeout fires?",
                "It requests cancellation and waits for it: `CancelledError` is thrown into the "
                "awaited coroutine at its next suspension point, and then `TimeoutError` is raised "
                "to you. Nothing is interrupted mid-statement — the coroutine has to reach an "
                "`await` for the cancellation to land.",
                "so if the inner call is a blocking socket read on a worker thread, `wait_for` "
                "never really stops it?",
                "Right. A blocking read inside `to_thread` keeps running past the timeout: you "
                "get the `TimeoutError`, the thread stays parked on the socket, and the only real "
                "bound is a deadline on the socket or the client itself.",
            ),
            ask="ok, file an issue for the nightly job stalling on the auth refresh",
        ),
        alert_page="pull-431",
    ),
    Filing(
        slug="webhook-duplicate-orders",
        symptom=(
            "when our payment provider retries a webhook delivery we end up with two identical "
            "order rows for one payment"
        ),
        related=("issue-355", "pull-402", "comment-355"),
        min_coverage=0.66,
        mid_thread=MidThread(
            prior_messages=(
                "in Python, does `except Exception` catch a `KeyboardInterrupt`?",
                "No. `KeyboardInterrupt` and `SystemExit` derive from `BaseException`, not "
                "`Exception`, so a bare `except Exception` lets them through — that separation is "
                "the point of the split.",
                "and if the `finally` block raises while an exception is already propagating, "
                "which one comes out?",
                "The one from `finally`. It replaces the original, which survives only as its "
                "`__context__`, so a cleanup that fails hides whatever actually broke unless you "
                "read the chained context.",
            ),
            ask="ok, file an issue for the payment retries double-booking an order",
        ),
        alert_page="pull-402",
    ),
    Filing(
        slug="csv-export-truncation",
        symptom=(
            "our CSV export cuts off — accounts with more than ten thousand orders get a file that "
            "just stops, with no warning anywhere"
        ),
        related=("issue-297", "comment-297"),
        min_coverage=0.5,
        mid_thread=MidThread(
            prior_messages=(
                "what does `|=` do on a dict?",
                "It is the in-place merge: `a |= b` updates `a` from `b`, and the right-hand side "
                "wins on a shared key. Same effect as `a.update(b)`, and any mapping works on the "
                "right.",
                "so if `b` comes back empty from a helper, `a` just keeps whatever it already had, "
                "no error?",
                "Correct — merging an empty mapping is a silent no-op. If an empty return is meant "
                "to say 'no override', that reads exactly the same as the helper having failed.",
            ),
            ask="ok, file an issue for the order export cutting off the tail of a big account",
        ),
        alert_page="issue-297",
    ),
)


@dataclass(frozen=True)
class Phrasing:
    """How the member asks for the filing. The whole inbound is the recall query the hook embeds, so
    each phrasing is a distinct retrieval query over one topic: `named-repo` states the repository
    and asks for a drafted title and body, and the rest are the terse asks a member actually sends,
    naming no repository at all."""

    slug: str
    template: str


PHRASINGS: tuple[Phrasing, ...] = (
    Phrasing(
        slug="named-repo",
        template=(
            "File a GitHub issue on northwind/atlas for me: {symptom}. Give me the issue title and "
            "body you would submit."
        ),
    ),
    Phrasing(slug="create-an-issue", template="Create an issue for this: {symptom}."),
    Phrasing(slug="file-issue", template="file issue: {symptom}"),
    Phrasing(slug="make-github-issue", template="Make a GitHub issue about this: {symptom}."),
)


@dataclass(frozen=True)
class AbsentFiling:
    """A filing ask on a topic the corpus holds nothing about — the control for form priming.

    The haystack is issue-shaped on purpose, and with 83 issue-shaped memories in the pool an
    injection of 8 carries one about four times in five whatever the query. That exposure is an
    alternative explanation for the agent citing existing issues in the graded cases: it might be
    reaching for "check what is already filed" because context looks like an issue tracker, not
    because the relevant issues were recalled. These asks have no match to find, so a reply that
    still attributes them to an existing issue shows the reflex rather than the recall."""

    slug: str
    message: str


ABSENT_FILINGS: tuple[AbsentFiling, ...] = (
    AbsentFiling(
        slug="desktop-cold-start",
        message=(
            "file issue: the desktop app crashes on cold start whenever the laptop has no network "
            "at all — it never reaches the sign-in screen."
        ),
    ),
    AbsentFiling(
        slug="org-chart-deactivated",
        message=(
            "Create an issue for this: the org chart still shows deactivated teammates as active, "
            "so headcount reads high."
        ),
    ),
    AbsentFiling(
        slug="print-stylesheet-footer",
        message=(
            "Make a GitHub issue about this: the print stylesheet drops the page footer whenever a "
            "page is landscape."
        ),
    ),
)


@dataclass(frozen=True)
class MachineControl:
    """An inbound no member wrote: machine boilerplate — ids, a status, no topic — the inbound
    shape internal admissions put through the recall hook verbatim. There is nothing to find, so
    the graded bar is the absent-topic one: whatever recall injects for a query of pure
    boilerplate, the reply must not attribute the turn to a corpus record."""

    slug: str
    message: str


MACHINE_CONTROLS: tuple[MachineControl, ...] = (
    MachineControl(
        slug="subagent-cancelled",
        message=(
            '<subagent_result profile="coding" '
            'subagent_id="7c2f5a90-4b1e-4c8a-9d3f-2e6b8a1c5d40" status="cancelled">\n\n'
            "</subagent_result>"
        ),
    ),
)


@dataclass(frozen=True)
class FilingCase:
    """One filing topic asked one way: the graded turn, and the conversation it lands in."""

    name: str
    message: str
    related: tuple[str, ...]
    min_coverage: float
    prior_messages: tuple[str, ...] = ()


MID_THREAD = "mid-thread"
SOURCE_ALERT = "source-alert"
ALERT_SOURCE_NAME = "github-9c41d2ae"
ALERT_CONNECTION_ID = "ca_mVtR-qwkoblf"


def source_alert(filing: Filing) -> str:
    """The topic reached the way scheduled admissions reach it: a source-change alert whose only
    topical signal is one related page's title, wrapped in the machine boilerplate — source and
    connection ids, a stream name, a page uuid — that the recall hook embeds verbatim as the
    query."""
    page = next(page for page in PAGES if page.key == filing.alert_page)
    if not isinstance(page, IssueThread):
        raise ValueError(f"issue_recall alert page {filing.alert_page!r} carries no title")
    page_uuid = UUID(
        bytes=hashlib.sha256(f"issue-recall/alert/{filing.alert_page}".encode()).digest()[:16],
        version=4,
    )
    return (
        f"The source '{ALERT_SOURCE_NAME}' (github ({ALERT_CONNECTION_ID}): {page.stream}) you "
        f"watch changed — {page.stream}: 1 updated. Changed pages (object_get each to read what "
        f"changed): page/{page_uuid} ({page.title}). Then tell the member what is new and why it "
        "matters."
    )


CASES: tuple[FilingCase, ...] = (
    *(
        FilingCase(
            name=f"{filing.slug}:{phrasing.slug}",
            message=phrasing.template.format(symptom=filing.symptom),
            related=filing.related,
            min_coverage=filing.min_coverage,
        )
        for filing in FILINGS
        for phrasing in PHRASINGS
    ),
    *(
        FilingCase(
            name=f"{filing.slug}:{MID_THREAD}",
            message=filing.mid_thread.ask,
            related=filing.related,
            min_coverage=filing.min_coverage,
            prior_messages=filing.mid_thread.prior_messages,
        )
        for filing in FILINGS
    ),
    *(
        FilingCase(
            name=f"{filing.slug}:{SOURCE_ALERT}",
            message=source_alert(filing),
            related=filing.related,
            min_coverage=filing.min_coverage,
        )
        for filing in FILINGS
    ),
)


@dataclass(frozen=True)
class RenderedPage:
    """One fixture page as the sync driver would land it: the connector's own key, title, and body,
    with the digest the driver derives from that body."""

    key: str
    source_ref: str
    stream: str
    title: str
    body: str
    digest: str


def rendered_pages() -> tuple[RenderedPage, ...]:
    """Every fixture page rendered through the real GitHub connector, in declaration order."""
    connector = GitHubConnector()
    streams = {stream.name: stream for stream in connector.streams()}
    pages: list[RenderedPage] = []
    for page in PAGES:
        stream = streams[page.stream]
        title, body = connector.render(connector.flatten(page.record(), stream), stream)
        pages.append(
            RenderedPage(
                key=page.key,
                source_ref=page.source_ref,
                stream=page.stream,
                title=title,
                body=body,
                digest="sha256:" + hashlib.sha256(body.encode()).hexdigest(),
            )
        )
    refs = tuple(rendered.source_ref for rendered in pages)
    if len(set(refs)) != len(refs):
        raise ValueError("issue_recall fixture pages share a source_ref")
    keys = tuple(page.key for page in PAGES)
    if len(set(keys)) != len(keys):
        raise ValueError("issue_recall fixture pages share a key")
    return tuple(pages)


def related_refs(case: FilingCase) -> tuple[str, ...]:
    """The case's related pages as source_refs, failing loud on a key the corpus does not hold."""
    by_key = {page.key: page.source_ref for page in rendered_pages()}
    missing = tuple(key for key in case.related if key not in by_key)
    if missing:
        raise ValueError(f"issue_recall case {case.name!r} names unknown pages: {missing}")
    if not case.related:
        raise ValueError(f"issue_recall case {case.name!r} names no related page")
    return tuple(by_key[key] for key in case.related)


def corpus_issue_numbers() -> frozenset[int]:
    """Every issue or pull request number the corpus holds, across the graded repository and the
    sibling repositories the haystack borrows. Numbers collide between repositories — #509 is an
    Atlas pull request and a gateway issue — and the set flattens them deliberately: it answers "is
    this a number the corpus uses", and every record behind every one of them is about something
    other than an absent topic. So an absent-topic reply citing any of them attributes its report to
    a record that cannot be about it, whichever repository the agent meant."""
    numbers = {page.number for page in PAGES if isinstance(page, IssueThread)}
    numbers |= {page.thread for page in PAGES if isinstance(page, Comment)}
    numbers |= {int(memory.ref.rpartition("-")[2]) for memory in ISSUE_SHAPED}
    return frozenset(numbers)


def corpus_digest() -> str:
    """The fixture's content identity: every page and every ambient memory, order-independent."""
    records = [[page.source_ref, page.digest] for page in rendered_pages()]
    records += [
        [memory.ref, "sha256:" + hashlib.sha256(memory.body.encode()).hexdigest()]
        for memory in ambient_memories()
    ]
    payload = json.dumps(sorted(records), separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
