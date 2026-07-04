"""Connector registry: backend slug → `Connector` class.

Explicit, not scanned — a new provider adds a line rather than paying for import-time discovery. The
slug is the connector's `name` and doubles as the source `backend` name a `source` row carries and
the credential slot the direct backend reads its key from; the manifest wraps each in a
`ConnectorBackend` and registers it as a source the sync driver drives."""

from selfhost_ext_sources.asana import AsanaConnector
from selfhost_ext_sources.connector import Connector
from selfhost_ext_sources.github import GitHubConnector
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
}
