---
name: create-application
description: "Load when a member asks for a new application of their own: an app that watches something, a separate assistant for one job, an agent for the team. Not for setting up an app that already exists, and not for saving a skill."
---
# New application

An application is an `agent` object. Apply with `create_only: true`: a name already taken is
otherwise an update, and would rewrite that application instead of making yours.

## Two ways in

A member who named the job gets the interview straight away. A member who did not — `Build me a
new app.` is the portal's `New application` act opening a conversation on their behalf — gets a
guided build: propose first, then the interview, then the design of the app's homepage, then the
create. The portal draws a progress bar over the todo board a guided build keeps.

## The board (guided build)

Before anything else in a guided build, call `update_todo_list` — when it is among your tools —
with the title `App Builder` and one task per phase this run will take, in order:

1. `Propose what the app should do`
2. `Ask what the build needs`
3. `Design the homepage`
4. `Create the app`

Mark a phase `in_progress` when you start it and `completed` in the same round as the act that
finished it, before you write the reply. The member's progress bar reads nothing else, and a phase
you finish without marking leaves that bar standing still through work that is already done.

## Propose (guided build)

You open speaking: the portal sent the first message, not the member. So lead with what you already
know about their business — `memory_search` for the workspace's own work, and read the apps that
exist with `object_list(kind="agent")` so you never propose one twice. Then state one concrete
proposal: what the app does, who runs it, what it answers with.

End with `ask_user`: one question, options `Build that`, `Something else`. A member who reads a
proposal and picks one is three questions ahead of a member asked "what would you like to build?".

## Interview

One `ask_user`, these three questions, `title` `Create new app`, and an `icon` that draws the job.
That is the ask's own mark, any tabler outline name — not the app's icon, which comes from the
portal's own pack.

| Question | What it settles |
|---|---|
| What job is it for? | the prompt |
| What may it do on its own? `Nothing without asking` / `Routine work, asks about the rest` / `Everything inside its job` | how much the prompt lets it decide |
| Who else uses it? `Just me` / `Everyone in the workspace` | `visibility`: `private` or `workspace` |

Ask all three on the one form, every time — the portal opens it on the first question you left
unanswered, so a form holding answers you prefilled costs the member nothing, and a question held
back for a round of its own costs them a whole exchange. Set `chosen` on each one the member's own
words already settle — a member who described the whole job in one sentence still sees the three
answers you drew from it, and a proposal they picked `Build that` on settles the first. Never set
`chosen` on who else uses it unless they named an audience: it is the one answer that publishes,
and a member pressing through three questions would publish an app they meant to keep.

A member's ask for a new application is the go-ahead to open the interview: never ask whether to
proceed, and never confirm a plan the conversation already holds. A blocker the job raises — a
dependency not yet cleared, a source to work from in the meantime — rides the same form as one
more question, never a round of its own.

Never ask about the model, reasoning, sandbox size, or icon.

## Draft it yourself

Name it for the work, never for a person: `invoice-intake`, not `iris`. A persona name invites the
member to treat it as a colleague instead of checking it. Check the name against the rule before
you say it: lowercase letters, digits, and inner hyphens only, 64 characters at most, and not a
name `object_list(kind="agent")` already returned — a name the create refuses is a step the member
watches fail for a reason they did not cause.

Write the whole prompt. Address the application in the second person, and cover who it works for,
the job, how it decides, where it stops and asks — and what its homepage reports, when a guided
build designed one, so the page the application builds and keeps fresh is the page the member
confirmed. Do not hand the member a blank prompt to fill in, and do not paste their own sentence
back as the prompt.

## Design the homepage (guided build)

Every application builds a homepage: the page members open on the Apps screen, where it states
what it is for, what it watches, its recent work, and what it needs. The design pass settles what
this app's page reports and how it is laid out — draw it as a mock the member can react to, not as
prose describing it. `load_skill(name="website-building")` for the design system, build the page
as static HTML in the sandbox, screenshot it at 1280×800 with the Playwright REPL, and share the
PNG with `share_file`. The chat draws shared pictures inline, so the mock lands in the
conversation itself.

One page, at most two variants, is the whole design pass. End with `ask_user`: `Build it`,
`Change the design`. You do not build the live page from here: the application builds and binds
its own homepage on its first homepage turn, and the prompt you draft is what carries the design
to it.

## Create it

The answered interview is the member saying go — in a guided build with a design pass, `Build it`
is. Do not ask again, and do not restate the name for approval — say what you are making in the
reply that carries the form, so the words they are agreeing to are on screen when they submit it.
Then:

```yaml
kind: agent
name: invoice-intake
spec:
  model: auto
  reasoning: auto
  visibility: private
  internet_access_allowed: true
  prompt: |
    You handle invoices for the finance team.
```

Decide `internet_access_allowed` yourself: false when the job stays inside connected accounts, true
when it reads the open web. Omit `icon` and `sandbox_size`: the app's mark is dealt from the
portal's pack by the app's own name.

A refusal is yours to fix and say plainly — a name already taken takes a new name, not a retry of
the same one.

## Say what it holds nothing of

Nothing else will ever tell the member the new application reaches nothing, so close the turn with
what it still needs and the one repair you can make from here: attach an account the workspace
already holds by applying `connector_grant` with `agent:` set to the new name, which asks for no
new sign-in. That repair runs on the workspace's main agent alone.

Skills and sources attach only from inside the new application's own conversation. Send the member
there instead of trying from this one.

## Traps

- A turn nobody speaks on cannot create an application. A scheduled fire or a subagent is refused;
  the member has to ask in their own words.
- Never offer to delete it or undo the create.
- Do not create the app before the member's go-ahead, however clear their first message was. The
  interview is where they see what they are agreeing to.
- Do not restate a phase you already delivered. The transcript holds it.
