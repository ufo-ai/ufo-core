"""Connector registry: backend slug → `Connector` class.

Explicit, not scanned — a new provider adds a line rather than paying for import-time discovery. The
slug is the connector's `name` and doubles as the source `backend` name a `source` row carries and
the credential slot the direct backend reads its key from; the manifest wraps each in a
`ConnectorBackend` and registers it as a source the sync driver drives."""

from ufo.sdk.sources import Connector
from ufo_ext_sources.activecampaign import ActiveCampaignConnector
from ufo_ext_sources.airtable import AirtableConnector
from ufo_ext_sources.asana import AsanaConnector
from ufo_ext_sources.ashby import AshbyConnector
from ufo_ext_sources.attio import AttioConnector
from ufo_ext_sources.bamboohr import BambooHRConnector
from ufo_ext_sources.brex import BrexConnector
from ufo_ext_sources.calendly import CalendlyConnector
from ufo_ext_sources.chargebee import ChargebeeConnector
from ufo_ext_sources.clickup import ClickUpConnector
from ufo_ext_sources.confluence import ConfluenceConnector
from ufo_ext_sources.deel import DeelConnector
from ufo_ext_sources.facebook_ads import FacebookAdsConnector
from ufo_ext_sources.freshdesk import FreshdeskConnector
from ufo_ext_sources.github import GitHubConnector
from ufo_ext_sources.gmail import GmailConnector
from ufo_ext_sources.google_ads import GoogleAdsConnector
from ufo_ext_sources.google_calendar import GoogleCalendarConnector
from ufo_ext_sources.google_docs import GoogleDocsConnector
from ufo_ext_sources.google_drive import GoogleDriveConnector
from ufo_ext_sources.google_sheets import GoogleSheetsConnector
from ufo_ext_sources.greenhouse import GreenhouseConnector
from ufo_ext_sources.hubspot import HubSpotConnector
from ufo_ext_sources.instagram import InstagramConnector
from ufo_ext_sources.intercom import IntercomConnector
from ufo_ext_sources.jira import JiraConnector
from ufo_ext_sources.klaviyo import KlaviyoConnector
from ufo_ext_sources.linear import LinearConnector
from ufo_ext_sources.mailchimp import MailchimpConnector
from ufo_ext_sources.microsoft_teams import MicrosoftTeamsConnector
from ufo_ext_sources.monday import MondayConnector
from ufo_ext_sources.notion import NotionConnector
from ufo_ext_sources.outlook import OutlookConnector
from ufo_ext_sources.pagerduty import PagerDutyConnector
from ufo_ext_sources.quickbooks import QuickBooksConnector
from ufo_ext_sources.recruitee import RecruiteeConnector
from ufo_ext_sources.recurly import RecurlyConnector
from ufo_ext_sources.rippling import RipplingConnector
from ufo_ext_sources.salesforce import SalesforceConnector
from ufo_ext_sources.sentry import SentryConnector
from ufo_ext_sources.slack import SlackConnector
from ufo_ext_sources.square import SquareConnector
from ufo_ext_sources.stripe import StripeConnector
from ufo_ext_sources.typeform import TypeformConnector
from ufo_ext_sources.wrike import WrikeConnector
from ufo_ext_sources.xero import XeroConnector
from ufo_ext_sources.zendesk import ZendeskConnector

CONNECTORS: dict[str, type[Connector]] = {
    ActiveCampaignConnector.name: ActiveCampaignConnector,
    AirtableConnector.name: AirtableConnector,
    AsanaConnector.name: AsanaConnector,
    AshbyConnector.name: AshbyConnector,
    AttioConnector.name: AttioConnector,
    BambooHRConnector.name: BambooHRConnector,
    BrexConnector.name: BrexConnector,
    CalendlyConnector.name: CalendlyConnector,
    ChargebeeConnector.name: ChargebeeConnector,
    ClickUpConnector.name: ClickUpConnector,
    ConfluenceConnector.name: ConfluenceConnector,
    DeelConnector.name: DeelConnector,
    FacebookAdsConnector.name: FacebookAdsConnector,
    FreshdeskConnector.name: FreshdeskConnector,
    GitHubConnector.name: GitHubConnector,
    GmailConnector.name: GmailConnector,
    GoogleAdsConnector.name: GoogleAdsConnector,
    GoogleCalendarConnector.name: GoogleCalendarConnector,
    GoogleDocsConnector.name: GoogleDocsConnector,
    GoogleDriveConnector.name: GoogleDriveConnector,
    GoogleSheetsConnector.name: GoogleSheetsConnector,
    GreenhouseConnector.name: GreenhouseConnector,
    HubSpotConnector.name: HubSpotConnector,
    InstagramConnector.name: InstagramConnector,
    IntercomConnector.name: IntercomConnector,
    JiraConnector.name: JiraConnector,
    KlaviyoConnector.name: KlaviyoConnector,
    LinearConnector.name: LinearConnector,
    MailchimpConnector.name: MailchimpConnector,
    MicrosoftTeamsConnector.name: MicrosoftTeamsConnector,
    MondayConnector.name: MondayConnector,
    NotionConnector.name: NotionConnector,
    OutlookConnector.name: OutlookConnector,
    PagerDutyConnector.name: PagerDutyConnector,
    QuickBooksConnector.name: QuickBooksConnector,
    RecruiteeConnector.name: RecruiteeConnector,
    RecurlyConnector.name: RecurlyConnector,
    RipplingConnector.name: RipplingConnector,
    SalesforceConnector.name: SalesforceConnector,
    SentryConnector.name: SentryConnector,
    SlackConnector.name: SlackConnector,
    SquareConnector.name: SquareConnector,
    StripeConnector.name: StripeConnector,
    TypeformConnector.name: TypeformConnector,
    WrikeConnector.name: WrikeConnector,
    XeroConnector.name: XeroConnector,
    ZendeskConnector.name: ZendeskConnector,
}
