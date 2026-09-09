"""Connector registry: backend slug → `Connector` class.

Explicit, not scanned — a new provider adds a line rather than paying for import-time discovery. The
slug is the connector's `name` and doubles as the source `backend` name a `source` row carries and
the credential slot the direct backend reads its key from; the manifest wraps each in a
`ConnectorBackend` and registers it as a source the sync driver drives."""

from ufo.sdk.sources import Connector
from ufo_ext_sources.providers.active_campaign import ActiveCampaignConnector
from ufo_ext_sources.providers.airtable import AirtableConnector
from ufo_ext_sources.providers.apollo import ApolloConnector
from ufo_ext_sources.providers.asana import AsanaConnector
from ufo_ext_sources.providers.ashby import AshbyConnector
from ufo_ext_sources.providers.attio import AttioConnector
from ufo_ext_sources.providers.bamboohr import BambooHRConnector
from ufo_ext_sources.providers.brex import BrexConnector
from ufo_ext_sources.providers.calendly import CalendlyConnector
from ufo_ext_sources.providers.chargebee import ChargebeeConnector
from ufo_ext_sources.providers.clickup import ClickUpConnector
from ufo_ext_sources.providers.confluence import ConfluenceConnector
from ufo_ext_sources.providers.datadog import DatadogConnector
from ufo_ext_sources.providers.deel import DeelConnector
from ufo_ext_sources.providers.docusign import DocuSignConnector
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
from ufo_ext_sources.providers.granola import GranolaConnector
from ufo_ext_sources.providers.greenhouse import GreenhouseConnector
from ufo_ext_sources.providers.hubspot import HubSpotConnector
from ufo_ext_sources.providers.instagram import InstagramConnector
from ufo_ext_sources.providers.intercom import IntercomConnector
from ufo_ext_sources.providers.jira import JiraConnector
from ufo_ext_sources.providers.klaviyo import KlaviyoConnector
from ufo_ext_sources.providers.linear import LinearConnector
from ufo_ext_sources.providers.mailchimp import MailchimpConnector
from ufo_ext_sources.providers.mercury import MercuryConnector
from ufo_ext_sources.providers.microsoft_teams import MicrosoftTeamsConnector
from ufo_ext_sources.providers.monday import MondayConnector
from ufo_ext_sources.providers.notion import NotionConnector
from ufo_ext_sources.providers.outlook import OutlookConnector
from ufo_ext_sources.providers.pagerduty import PagerDutyConnector
from ufo_ext_sources.providers.pandadoc import PandaDocConnector
from ufo_ext_sources.providers.quickbooks import QuickBooksConnector
from ufo_ext_sources.providers.ramp import RampConnector
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


CONNECTORS = _connector_registry(
    (
        ActiveCampaignConnector,
        AirtableConnector,
        ApolloConnector,
        AsanaConnector,
        AshbyConnector,
        AttioConnector,
        BambooHRConnector,
        BrexConnector,
        CalendlyConnector,
        ChargebeeConnector,
        ClickUpConnector,
        ConfluenceConnector,
        DatadogConnector,
        DeelConnector,
        DocuSignConnector,
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
        GranolaConnector,
        GreenhouseConnector,
        HubSpotConnector,
        InstagramConnector,
        IntercomConnector,
        JiraConnector,
        KlaviyoConnector,
        LinearConnector,
        MailchimpConnector,
        MercuryConnector,
        MicrosoftTeamsConnector,
        MondayConnector,
        NotionConnector,
        OutlookConnector,
        PagerDutyConnector,
        PandaDocConnector,
        QuickBooksConnector,
        RampConnector,
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


def direct_slots(connector: type[Connector]) -> tuple[str, ...]:
    """The credential slots the direct auth backend spends for one connector: the slot behind each
    header a `key_headers` connector authenticates with, else the one bearer slot named for the
    connector itself. Three readers share this one rule — what the manifest declares, what the
    registrar reads to decide a keyed feed's lifecycle, and what its job filters candidates on — so
    a provider demanding two keys is not a provider whose feed silently never starts."""
    if not connector.key_headers:
        return (connector.name,)
    return tuple(connector.key_headers.values())
