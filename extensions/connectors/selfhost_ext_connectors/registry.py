"""Connector registry: backend slug → `Connector` class.

Explicit, not scanned — one connector this wave (GitHub), so a new provider adds a line rather than
paying for import-time discovery. The slug is the connector's `name` and doubles as the source
`backend` name a `source` row carries; the manifest wraps each in a `ConnectorBackend` and registers
it as a source the sync driver drives."""

from selfhost_ext_connectors.connector import Connector
from selfhost_ext_connectors.github import GitHubConnector

CONNECTORS: dict[str, type[Connector]] = {GitHubConnector.name: GitHubConnector}
