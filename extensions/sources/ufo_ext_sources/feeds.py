"""Each provider a sources feed syncs from a key the workspace holds: the credential slots that hold
the key, the hosts the proxy may present it on, whether the host of the workspace's keyed connection
takes it too, and the headers it rides in when the provider takes several keys. Core holds this
list as data and never asks the sources service for its providers."""

from collections.abc import Mapping
from dataclasses import dataclass

from ufo.sdk.manifest import CredentialSlot, FeedRelease


@dataclass(frozen=True)
class Feed:
    """One provider's keyed feed. `headers` pairs with `slots` in order and is empty for a provider
    taking one key."""

    provider: str
    slots: tuple[CredentialSlot, ...]
    hosts: tuple[str, ...]
    tenant: bool
    headers: tuple[str, ...]


def _keyed(provider: str, hosts: tuple[str, ...], *, tenant: bool = False) -> Feed:
    return Feed(
        provider=provider,
        slots=(
            CredentialSlot(
                name=provider,
                description=f"BYOK API key for {provider} feed-sync via the direct auth backend.",
                feed=FeedRelease(provider, hosts, tenant),
            ),
        ),
        hosts=hosts,
        tenant=tenant,
        headers=(),
    )


_DATADOG = Feed(
    provider="datadog",
    slots=tuple(
        CredentialSlot(
            name=slot,
            description=f"{slot}, read host-side for datadog feed-sync.",
            feed=FeedRelease("datadog", (), True),
        )
        for slot in ("datadog_feed_api_key", "datadog_feed_application_key")
    ),
    hosts=(),
    tenant=True,
    headers=("dd-api-key", "dd-application-key"),
)

FEEDS: Mapping[str, Feed] = {
    feed.provider: feed
    for feed in (
        _keyed("active_campaign", (), tenant=True),
        _keyed("airtable", ("api.airtable.com",)),
        _keyed("apollo", ("api.apollo.io",)),
        _keyed("asana", ("app.asana.com",)),
        _keyed("ashby", ("api.ashbyhq.com",)),
        _keyed("attio", ("api.attio.com",)),
        _keyed("bamboohr", (), tenant=True),
        _keyed("brex", ("platform.brexapis.com",)),
        _keyed("calendly", ("api.calendly.com",)),
        _keyed("chargebee", (), tenant=True),
        _keyed("clickup", ("api.clickup.com",)),
        _keyed("confluence", ("api.atlassian.com",)),
        _DATADOG,
        _keyed("deel", ("api.letsdeel.com",)),
        _keyed("docusign", ("account.docusign.com",)),
        _keyed("facebook_ads", ("graph.facebook.com",)),
        _keyed("freshdesk", (), tenant=True),
        _keyed("github", ("api.github.com",)),
        _keyed("gmail", ("gmail.googleapis.com",)),
        _keyed("googleads", ("googleads.googleapis.com",)),
        _keyed("googlecalendar", ("www.googleapis.com",)),
        _keyed("googledocs", ("www.googleapis.com", "docs.googleapis.com")),
        _keyed("googledrive", ("www.googleapis.com",)),
        _keyed("googlemeet", ("meet.googleapis.com", "docs.googleapis.com")),
        _keyed("googlesheets", ("www.googleapis.com", "sheets.googleapis.com")),
        _keyed("granola_mcp", ()),
        _keyed("greenhouse", ("harvest.greenhouse.io",)),
        _keyed("hubspot", ("api.hubapi.com",)),
        _keyed("instagram", ("graph.facebook.com",)),
        _keyed("intercom", ("api.intercom.io",)),
        _keyed("jira", ("api.atlassian.com",)),
        _keyed("klaviyo", ("a.klaviyo.com",)),
        _keyed("linear", ("api.linear.app",)),
        _keyed("mailchimp", (), tenant=True),
        _keyed("mercury", ("api.mercury.com",)),
        _keyed("microsoft_teams", ("graph.microsoft.com",)),
        _keyed("monday", ("api.monday.com",)),
        _keyed("notion", ("api.notion.com",)),
        _keyed("outlook", ("graph.microsoft.com",)),
        _keyed("pagerduty", ("api.pagerduty.com",)),
        _keyed("pandadoc", ("api.pandadoc.com",)),
        _keyed("quickbooks", (), tenant=True),
        _keyed("ramp", ("api.ramp.com",)),
        _keyed("recruitee", (), tenant=True),
        _keyed("recurly", ("v3.recurly.com",)),
        _keyed("rippling", ("rest.ripplingapis.com",)),
        _keyed("salesforce", (), tenant=True),
        _keyed("sentry", ("sentry.io",)),
        _keyed("slack", ("slack.com",)),
        _keyed("square", ("connect.squareup.com",)),
        _keyed("stripe", ("api.stripe.com",)),
        _keyed("typeform", ("api.typeform.com",)),
        _keyed("wrike", ("www.wrike.com",)),
        _keyed("xero", ("api.xero.com",)),
        _keyed("zendesk", (), tenant=True),
    )
}

FEED_SLOTS = frozenset(slot.name for feed in FEEDS.values() for slot in feed.slots)
