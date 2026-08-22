"""Connector registry: backend slug → `Connector` class.

Explicit, not scanned — a new provider adds a line rather than paying for import-time discovery. The
slug is the connector's `name` and doubles as the source `backend` name a `source` row carries and
the credential slot the direct backend reads its key from; the manifest wraps each in a
`ConnectorBackend` and registers it as a source the sync driver drives. A binding's object name
derives from `ufo.sdk.sources.binding_name` — the `source` object kind, the `page` kind, and the
portal's per-row actions all name a binding through that one rule."""

from ufo.sdk.sources import Connector
from ufo_ext_sources.providers.active_campaign import ActiveCampaignConnector
from ufo_ext_sources.providers.airtable import AirtableConnector
from ufo_ext_sources.providers.asana import AsanaConnector
from ufo_ext_sources.providers.ashby import AshbyConnector
from ufo_ext_sources.providers.attio import AttioConnector
from ufo_ext_sources.providers.bamboohr import BambooHRConnector
from ufo_ext_sources.providers.brex import BrexConnector
from ufo_ext_sources.providers.calendly import CalendlyConnector
from ufo_ext_sources.providers.chargebee import ChargebeeConnector
from ufo_ext_sources.providers.clickup import ClickUpConnector
from ufo_ext_sources.providers.confluence import ConfluenceConnector
from ufo_ext_sources.providers.deel import DeelConnector
from ufo_ext_sources.providers.facebook_ads import FacebookAdsConnector
from ufo_ext_sources.providers.freshdesk import FreshdeskConnector
from ufo_ext_sources.providers.github import GitHubConnector
from ufo_ext_sources.providers.gmail import GmailConnector
from ufo_ext_sources.providers.googleads import GoogleAdsConnector
from ufo_ext_sources.providers.googlecalendar import GoogleCalendarConnector
from ufo_ext_sources.providers.googledocs import GoogleDocsConnector
from ufo_ext_sources.providers.googledrive import GoogleDriveConnector
from ufo_ext_sources.providers.googlemeet import GoogleMeetConnector
from ufo_ext_sources.providers.googlesheets import GoogleSheetsConnector
from ufo_ext_sources.providers.greenhouse import GreenhouseConnector
from ufo_ext_sources.providers.hubspot import HubSpotConnector
from ufo_ext_sources.providers.instagram import InstagramConnector
from ufo_ext_sources.providers.intercom import IntercomConnector
from ufo_ext_sources.providers.jira import JiraConnector
from ufo_ext_sources.providers.klaviyo import KlaviyoConnector
from ufo_ext_sources.providers.linear import LinearConnector
from ufo_ext_sources.providers.mailchimp import MailchimpConnector
from ufo_ext_sources.providers.microsoft_teams import MicrosoftTeamsConnector
from ufo_ext_sources.providers.monday import MondayConnector
from ufo_ext_sources.providers.notion import NotionConnector
from ufo_ext_sources.providers.outlook import OutlookConnector
from ufo_ext_sources.providers.pagerduty import PagerDutyConnector
from ufo_ext_sources.providers.quickbooks import QuickBooksConnector
from ufo_ext_sources.providers.recruitee import RecruiteeConnector
from ufo_ext_sources.providers.recurly import RecurlyConnector
from ufo_ext_sources.providers.rippling import RipplingConnector
from ufo_ext_sources.providers.salesforce import SalesforceConnector
from ufo_ext_sources.providers.sentry import SentryConnector
from ufo_ext_sources.providers.slack import SlackConnector
from ufo_ext_sources.providers.square import SquareConnector
from ufo_ext_sources.providers.stripe import StripeConnector
from ufo_ext_sources.providers.typeform import TypeformConnector
from ufo_ext_sources.providers.wrike import WrikeConnector
from ufo_ext_sources.providers.xero import XeroConnector
from ufo_ext_sources.providers.zendesk import ZendeskConnector


def _connector_registry(
    connector_types: tuple[type[Connector], ...],
) -> dict[str, type[Connector]]:
    connectors: dict[str, type[Connector]] = {}
    for connector_type in connector_types:
        if connector_type.name in connectors:
            raise ValueError(f"duplicate source connector name {connector_type.name!r}")
        connectors[connector_type.name] = connector_type
    return connectors


SOURCE_KIND = "source"

CONNECTORS = _connector_registry(
    (
        ActiveCampaignConnector,
        AirtableConnector,
        AsanaConnector,
        AshbyConnector,
        AttioConnector,
        BambooHRConnector,
        BrexConnector,
        CalendlyConnector,
        ChargebeeConnector,
        ClickUpConnector,
        ConfluenceConnector,
        DeelConnector,
        FacebookAdsConnector,
        FreshdeskConnector,
        GitHubConnector,
        GmailConnector,
        GoogleAdsConnector,
        GoogleCalendarConnector,
        GoogleDocsConnector,
        GoogleDriveConnector,
        GoogleMeetConnector,
        GoogleSheetsConnector,
        GreenhouseConnector,
        HubSpotConnector,
        InstagramConnector,
        IntercomConnector,
        JiraConnector,
        KlaviyoConnector,
        LinearConnector,
        MailchimpConnector,
        MicrosoftTeamsConnector,
        MondayConnector,
        NotionConnector,
        OutlookConnector,
        PagerDutyConnector,
        QuickBooksConnector,
        RecruiteeConnector,
        RecurlyConnector,
        RipplingConnector,
        SalesforceConnector,
        SentryConnector,
        SlackConnector,
        SquareConnector,
        StripeConnector,
        TypeformConnector,
        WrikeConnector,
        XeroConnector,
        ZendeskConnector,
    )
)
