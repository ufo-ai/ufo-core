# Google consent for Composio connectors

Connecting a Google toolkit from chat runs OAuth consent on Composio's shared client. Google tiers
its scopes, and the tier decides whether that consent can succeed:

| Tier | Gmail examples | Consent on Composio's shared client |
| --- | --- | --- |
| non-sensitive | `gmail.labels` | passes |
| sensitive | `gmail.send`, Calendar scopes | passes |
| restricted | `gmail.readonly`, `gmail.compose`, `gmail.modify` | blocked: "This app is blocked — this app tried to access sensitive info in your Google Account" |

Every scope that reads mail or writes drafts is restricted, so no Gmail scope selection avoids the
block. Google lifts it only for a client it has verified for restricted scopes or one the member's
Workspace org has marked trusted.

## Trust the client in Google Admin

A Workspace admin marks Composio's client Trusted; trusted apps may use restricted scopes without
Google verification, for that org's members only.

1. Open <https://admin.google.com/ac/owl/list?tab=configuredApps> (Security → Access and data
   control → API controls → Manage third-party app access).
2. Configure new app → search by client ID and select it:
   `511566560828-23utloam4ek35i5n4grb870kucdva5fu.apps.googleusercontent.com` (Composio's shared
   client — also readable as the `client_id` query param on the blocked consent URL).
3. Scope it to all org units, access level **Trusted**, Finish.
4. Changes usually apply within minutes (up to 24h). Reconnect from chat.

Each member org repeats this with its own admin. The consent screen still shows Composio's
branding, requests Composio's default scope set for the toolkit, and shares Composio's Gmail API
quota. An org that will not trust the shared client can only connect Gmail through an OAuth client
of its own — Google-verified for restricted scopes, or internal to that org.
