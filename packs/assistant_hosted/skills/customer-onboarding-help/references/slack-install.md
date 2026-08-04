# Connecting Slack

## What to do

Mint the Add to Slack link and give it to the customer rather than explaining the install. The
`slack-app-setup` skill drives the install itself, including the bring-your-own-app fallback for a
deploy without one-click; this file is the customer-facing meaning of what comes back. Only a
workspace admin can mint the link and complete the install, and credentials on the fallback path are
always entered through a private prompt — never ask a customer to paste a token into chat.

## The install states

Checking Slack's status returns one of four states. Translate them plainly:

| State | What it means | What to say |
| --- | --- | --- |
| `not_configured` | Install cannot proceed as asked, for one of several reasons: no one-click app on this deploy, this Slack workspace already belongs to another UFO workspace, an app of their own still missing its two secrets, or a bot token Slack would not accept. The state arrives with a hint naming which. | Translate the hint, never the state. Only the first reason means "we will use the manual app path"; a workspace already taken is one to confirm and raise, and a rejected token is one to collect again. |
| `not_installed` | Nothing installed yet. For an admin the link is ready; for a non-admin it also covers not being allowed to mint one. | To an admin: "Here is your Add to Slack link." To anyone else: "Ask a workspace admin to connect Slack and I will confirm once it lands." |
| `pending` | An app identity exists, but this deploy has not verified a Slack event with the current app credentials. A new install, a manifest setup, or a signing-secret rotation can all land here. | "Slack has not reached this deploy with the current app credentials. Invite the bot to a channel and @mention it, or send it a DM. If it stays pending, ask a workspace admin to raise it with the team." |
| `connected` | Installed and working. | "Slack is connected." |

## The unverified-app warning

Slack warns that the app is not verified by Slack. This is expected: the app is publicly installable
but not listed in Slack's directory. It is safe to proceed. Say so plainly and do not treat it as an
error.

## The shared channel with the UFO team

Separately from the customer's own install, the UFO team shares a Slack Connect channel with each new
customer so the team is reachable. The team sets it up by hand, and when they do, the person who
created the workspace gets a Slack Connect invitation — one channel and one invitation per customer,
never to teammates who join later and never a second time.

Asked where theirs is, say the team will set it up and that you are passing the request on. Nothing
is on its way until they do it, so never promise them an automatic email, never say one has been
sent, and never say anything about why it is not automatic.

## Failure modes

| Symptom | Cause | Remedy |
| --- | --- | --- |
| The link errors after approving in Slack | The install link expired or was tampered with | Mint a fresh link. Links are single-purpose and short-lived, so never reuse one from an old message. |
| "This app is already installed to another workspace" style conflict | The install is being pointed at a different Slack workspace than the one already bound | Confirm which Slack workspace they mean. Do not force it; raise it with the team. |
| The customer declined the permission screen | Nothing was installed, and nothing changed | Mint a fresh link and let them approve. |
| Slack returns a generic failure | The OAuth exchange did not complete | Mint a fresh link and retry once. If it fails again, raise it with the team rather than retrying repeatedly. |
