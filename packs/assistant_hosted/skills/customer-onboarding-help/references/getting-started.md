# Getting Started

## The shape of the flow

A new customer is invited by the UFO team, by email. Signup is command-line first: the customer runs
the install command, signs in with their work email, and lands in their own workspace. The sign-in
page reaches the same workspace, and its signed-in card carries "Open your workspace" — the web
portal, where a member chats with the workspace's main agent in the browser. An admin reaches
every agent there; other agents appear once an admin shares them (see `capabilities.md`).

## Step by step, as the customer experiences it

1. **The team emails them an invitation.** There is nothing in it to retype. The invitation covers
   their whole email domain, so the person who runs the installer does not have to be the person who
   received it.
2. **They run the install command and enter their work email.** A verification code is emailed to
   them, and they enter it. Those two prompts are the whole sign-in.
3. **Their verified email domain opens the workspace.** Nothing else is asked for. The invitation
   works once per domain, and lapses if it goes unused for a couple of weeks.
4. **Joining an existing workspace needs no invitation.** A teammate whose email domain already has a
   workspace signs in and joins it directly.
5. **A workspace admin is offered billing setup at the end.** In the terminal the admin gets a choice
   on the concluding screen; picking it starts a chat with the agent, which returns a link for saving
   a payment method. A joined teammate gets the ordinary prompt instead, and signing in through the
   web page ends on a signed-in card without the menu; "Open your workspace" there opens the web
   portal, where the workspace's main agent already answers them.
6. **Slack comes next.** See `slack-install.md`.

## What to say when asked

"Your invitation covers your company's email domain, so there is nothing to type in. Your teammates
do not need one at all: they sign in with their work email and join the workspace you already have."

## Boundaries

- A refusal ends the session with the reason rather than asking again, because the member holds no
  secret that could change the outcome. Three shapes: the domain has no invitation, so they join the
  waitlist; the invitation lapsed, so they reply to it; the invitation was already used with no
  workspace to show for it, which only the team can sort out. A refusal happens only when the domain
  has no workspace, so in every shape there is nothing for them to sign in to yet.
- The invitation is spent atomically: if signup crashes partway, it is not silently lost and the team
  can confirm where it stands.
- A customer cannot invite another company. Only the UFO team issues invitations.
- One workspace per email domain. A colleague on a domain that already has a workspace joins it. A
  second, separate workspace on the same domain is not something a customer can create today.
- A workspace can have more than one admin, so "the admin" is not necessarily one person.
- Do not name the command the team runs to issue an invitation, or where invitations are recorded.
  See `internal-only.md`.
