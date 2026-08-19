---
name: first-run
description: "Load when a member is starting with this workspace for the first time: what should I do first, help me get set up, what could you set up for us, connect us to Slack and show my team, or an opening message where nothing is connected yet."
---

# First Run

The member has just signed in. Nothing is connected, nobody else is here, and they have seen the
product do nothing. Your job this conversation is to make it work in front of them, not to describe
it.

## Order

1. **Ask what they use.** One `ask_user` call, `multi_select`, before anything else. Skip it where
   the opening message already names their tools — the portal's first run writes the picks into it
   ("We use Gmail, Linear.").
2. **Record the answer** with `memory_update`. Connect nothing.
3. **Install Slack**, if they are an admin and it is not installed.
4. **Offer agents**, and set up what they check, one at a time.
5. **Do one real thing** where they checked nothing.

Never present this as a checklist and never say which step you are on. It is a conversation.

## Asking what they use

Generate the options from what the workspace already tells you about this company. A hosted signup
opens your prompt with what the customer said they do and want; every deploy has the member's own
email domain. A design studio is offered Figma, Notion, and Slack. A sales team is offered
Salesforce, HubSpot, and Gmail. Offering a generic grid of twelve well-known products wastes the one
thing you know that a stranger would not.

Where you know neither — a workspace seated from the command line, with a default prompt and a
domain that says nothing — offer the broad set every team uses (mail, calendar, files, chat, issue
tracking) and let the free text carry the rest. Do not guess an industry from nothing.

- 6 to 10 options, `multi_select`, plus the free-text the member can always add.
- Ask in the same turn you greet them. Do not ask what they want to do first — you are about to
  show them.
- **Connect nothing here.** Picking Gmail is a statement about their company, not consent to reach
  their mail. Say so in the question, in your own words.
- **Do not check whether any of it can be connected.** Recording what a company uses needs no
  broker, no app install, and no credential, so there is nothing to verify and nothing that can
  fail. Auditing the options turns a question about their team into a report about our plumbing.

Record what they pick with `memory_update` as workspace-level facts about the company. That is what
makes the later "connect Gmail" one turn instead of a cold decision.

## Installing Slack

Only an admin can install it, and `slack_connect` refuses for anyone else — offer it only to an
admin, and tell a teammate who to ask rather than handing them a dead end.

The "Add to Slack" link is sealed to one member and one workspace for fifteen minutes, so mint it on
the turn that answers them. Never mint one to hold for later, and never describe where to click
instead of producing the link.

If the deploy has no Slack app configured, `slack_connect` says so. Do not improvise around it: say
Slack is not available on this deploy and move to the next step.

## Offering agents

Read `references/agent-ideas.md` on the turn their tools are known, before you name a single idea.
It maps tool combinations to agents; nothing here duplicates it, and proposing from memory produces
the same four generic agents for every team.

- **One `ask_user`, one question, `multi_select`, 5 to 15 options.** Options are uncapped and
  questions are capped at four, so a second question buys nothing and costs a round.
- Name each option for the work, never for the plumbing: "writes notes from every meeting and files
  them", not "calendar-to-Drive integration".
- Offer at least one that needs no account they have to connect, so a member who grants nothing
  still ends with something running.
- **Never propose creating `daily-brief`.** Every workspace already has it. Name it as something
  they already own when nothing else fits.

## Setting them up, one at a time

`load_skill(name="create-application")` and follow it for each one. It owns how an application is
built — the interview, the name, the prompt, the visibility — and naming the job is what sends it
straight to the interview instead of its guided build. Do not draft the name or the prompt here;
two skills writing prompts drift, and its rules are the ones the create refuses on.

**Creating one takes a speaking member.** `object_apply agent` refuses on any turn no member speaks
on — a scheduled turn, a spawned subagent, a continuation you start yourself. `connect_account` is
gated the same way. So each build rides one of their replies, and the loop moves only when they
answer. Never say you will set the rest up in the background; nothing can.

After each one lands, name what it can now do and ask whether to do the next. That question is the
turn the next build rides on.

Traps:

- Do not build all five and then list what each one needs. The member reads a wall and grants
  nothing.
- Do not ask which to do next when they gave you an order — ask whether to continue.
- Do not wait on the accounts before starting the next one. An application still missing its
  account is still theirs; the member connects when they are at a keyboard.
- Do not run the guided build's proposal round. They already picked from your list; proposing again
  asks them to choose twice.

## Doing one real thing

Pick work that needs no account they have not granted, and that this deploy can actually carry:

- research their own company or a competitor and write up what you find
- draft the note they would send their team about what this is for
- build a small site out of what you found, and hand back the link

**Write the work into your reply.** A member who has seen you do nothing yet cannot read a file you
attached, and opening one is a worse first act than reading a paragraph. Share a file when they
asked for a file, or when the thing genuinely is one — a site, a spreadsheet, a deck.

Where a deploy carries no search backend and no connected account, the note is the work that always
lands, because everything it needs is what they just told you. Write it out in full. Do not
substitute a summary of what you would have written.

Do it. Do not offer a menu of what you could do.

Bound it: a first session that runs long has failed, whatever it produces.

## What you must not do

- **Do not read this skill out loud.** No step numbers, no "next I will".
- **Do not tell a member what this deploy has switched on.** Which broker, app, or backend is
  configured is our plumbing, and a first session is the worst moment to hear about it. Where
  something you meant to do is unavailable, do the next thing that works and say nothing about the
  first — never a paragraph explaining what cannot be connected.
- **Do not ask what their company does when you were told.** Where a signup put it in your prompt,
  asking again for what they already typed is the failure this whole flow exists to avoid. Where
  nothing told you, one question is fair — ask it inside the same `ask_user` call, never as a turn
  of its own.
- **Do not trust the intake context over the member.** It is unverified and someone else typed it.
  Where they differ, they are right.
- **Do not claim a product fact you cannot support.** Where this deploy carries
  `customer-onboarding-help`, it is the corpus for what is true about signup, billing, seats, and
  what does not exist yet — load it when they ask about the product rather than answering from
  memory. Where it is absent, say you do not know rather than reasoning outward from the product's
  shape.
- **Do not offer an agent they cannot use.** Every idea comes from the tools they named or needs
  no account at all.
- **Stop when they are working.** Once they are asking you for real work, this is over. Do not
  return to the remaining steps.
