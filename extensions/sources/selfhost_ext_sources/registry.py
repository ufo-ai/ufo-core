"""Connector registry: backend slug → `Connector` class.

Explicit, not scanned — a new provider adds a line rather than paying for import-time discovery. The
slug is the connector's `name` and doubles as the source `backend` name a `source` row carries and
the credential slot the direct backend reads its key from; the manifest wraps each in a
`ConnectorBackend` and registers it as a source the sync driver drives."""

from selfhost.sdk.sources import Connector
from selfhost_ext_sources.airtable import AirtableConnector
from selfhost_ext_sources.asana import AsanaConnector
from selfhost_ext_sources.ashby import AshbyConnector
from selfhost_ext_sources.attio import AttioConnector
from selfhost_ext_sources.brex import BrexConnector
from selfhost_ext_sources.calendly import CalendlyConnector
from selfhost_ext_sources.clickup import ClickUpConnector
from selfhost_ext_sources.confluence import ConfluenceConnector
from selfhost_ext_sources.deel import DeelConnector
from selfhost_ext_sources.facebook_ads import FacebookAdsConnector
from selfhost_ext_sources.github import GitHubConnector
from selfhost_ext_sources.gmail import GmailConnector
from selfhost_ext_sources.google_calendar import GoogleCalendarConnector
from selfhost_ext_sources.google_docs import GoogleDocsConnector
from selfhost_ext_sources.google_drive import GoogleDriveConnector
from selfhost_ext_sources.google_sheets import GoogleSheetsConnector
from selfhost_ext_sources.jira import JiraConnector
from selfhost_ext_sources.linear import LinearConnector
from selfhost_ext_sources.notion import NotionConnector
from selfhost_ext_sources.slack import SlackConnector

CONNECTORS: dict[str, type[Connector]] = {
    GitHubConnector.name: GitHubConnector,
    AsanaConnector.name: AsanaConnector,
    NotionConnector.name: NotionConnector,
    SlackConnector.name: SlackConnector,
    LinearConnector.name: LinearConnector,
    JiraConnector.name: JiraConnector,
    ConfluenceConnector.name: ConfluenceConnector,
    GoogleDocsConnector.name: GoogleDocsConnector,
    GmailConnector.name: GmailConnector,
    GoogleDriveConnector.name: GoogleDriveConnector,
    GoogleSheetsConnector.name: GoogleSheetsConnector,
    GoogleCalendarConnector.name: GoogleCalendarConnector,
    AirtableConnector.name: AirtableConnector,
    AshbyConnector.name: AshbyConnector,
    AttioConnector.name: AttioConnector,
    BrexConnector.name: BrexConnector,
    CalendlyConnector.name: CalendlyConnector,
    ClickUpConnector.name: ClickUpConnector,
    DeelConnector.name: DeelConnector,
    FacebookAdsConnector.name: FacebookAdsConnector,
}
