# Getting Started

## The shape of the flow

A new customer is invited by the UFO team, by email. The invitation opens the workspace's first-run
page. The customer signs in with their work email, and sign-in continues to that page without
another action. A member chats with the workspace's main agent there. An admin reaches every agent;
every other member reaches the agents open to everyone in the workspace, the agents they created
themselves, and any agent shared with them (see `capabilities.md`). The invitation also gives the
terminal install command.

## Step by step, as the customer experiences it

1. **The team emails them an invitation.** Its link opens first run. There is nothing in it to
   retype. The invitation covers their whole email domain, so the person who opens it does not have
   to be the person who received it.
2. **They enter their work email.** A verification code is emailed to them, and they enter it — or,
   on the same page, they choose Continue with Google and verify through their Google account
   instead, skipping the code. The portal opens automatically after verification. They can use the
   install command in the invitation if they want the terminal client.
3. **Their verified email domain opens the workspace.** Nothing else is asked for. The invitation
   works once per domain, and lapses if it goes unused for a couple of weeks.
4. **Joining an existing workspace needs no invitation.** A teammate whose email domain already has a
   workspace signs in and joins it directly. An admin can also add someone ahead of their first
   sign-in — by asking the agent, or from the portal's Team view — optionally as an admin, and at any
   email domain, so a contractor or an advisor is added the same way as a colleague. The person is
   emailed that they were added, with a link to the ordinary sign-in page — there is no invite code
   and nothing for the admin to pass on, and the verification code arrives when they sign in with
   that work email. An admin asking the agent can ask for no message; the portal's Team view always
   sends it. Signing in opens the one workspace their verified address can enter, or asks them to
   choose when an exact membership and their email domain name different workspaces. An exact
   membership needs no invite.
5. **A workspace admin is offered connecting Slack, then billing setup at the end.** In the terminal
   the admin gets a choice on the concluding screen; picking one starts a chat with the agent, which
   returns either an "Add to Slack" link or a link for saving a payment method. A joined teammate
   gets the ordinary prompt instead. The web page opens the portal directly, where the workspace's
   main agent already answers them.
6. **Slack comes next.** See `slack-install.md`.

## What to say when asked

"Your invitation covers your company's email domain, so there is nothing to type in. Your teammates
do not need one at all: they sign in with their work email and join the workspace you already have.
If you want someone set up before they sign in, ask me to add them by their email — or add them
yourself from the portal's Team view — and I will, optionally as an admin, at any domain, so an
outside contractor works too. They are emailed that they were added, with a link to sign in, and
there is no code for you to pass on. After they verify that address, they choose the workspace when
they can enter more than one."

## Boundaries

- A refusal ends the session with the reason rather than asking again, because the member holds no
  secret that could change the outcome. Three shapes: the domain has no invitation, so they join the
  waitlist; the invitation lapsed, so they reply to it; the invitation was already used with no
  workspace to show for it, which only the team can sort out. A verified exact membership bypasses
  these creation refusals because it already grants one workspace.
- The invitation is spent atomically: if signup crashes partway, it is not silently lost and the team
  can confirm where it stands.
- A customer cannot invite another company. Only the UFO team issues invitations.
- One workspace per email domain. A colleague on a domain that already has a workspace joins it. A
  second, separate workspace on the same domain is not something a customer can create today.
- A workspace can have more than one admin, so "the admin" is not necessarily one person.
- Do not name the command the team runs to issue an invitation, or where invitations are recorded.
  See `internal-only.md`.
