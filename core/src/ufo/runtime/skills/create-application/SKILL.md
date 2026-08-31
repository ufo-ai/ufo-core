---
name: create-application
description: Load when a member asks to create a new application of their own, including an underspecified app, a separate assistant for one job, or an app that watches and acts. Not for a page, website, dashboard, or browser app.
---
# New application

An application is an `agent` object. Apply with `create_only: true`: a name already taken is
otherwise an update, and would rewrite that application instead of making yours.

## Two ways in

A member who named the job gets the interview straight away, then the design of the app's
homepage, then the create. A member who did not — `Build me a new app.` is the portal's
`New application` act opening a conversation on their behalf — gets a guided build: the same run
with a proposal in front of it. The portal draws a progress bar over the todo board a guided build
keeps.

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
proposal and picks one is a round ahead of a member asked "what would you like to build?".

## Interview

One `ask_user`, these two questions, `title` `Create new app`, and an `icon` that draws the job.
That is the ask's own mark, any tabler outline name — not the app's icon, which comes from the
portal's own pack.

| Question | What it settles |
|---|---|
| What job is it for? | the prompt |
| Who else uses it? `Just me` / `Everyone in the workspace` | `visibility`: `private` or `workspace` |

Ask both on the one form, every time — the portal opens it on the first question you left
unanswered, so a form holding an answer you prefilled costs the member nothing, and a question held
back for a round of its own costs them a whole exchange. Set `chosen` on the job when the member's
own words already settle it — a member who described the whole job in one sentence still sees the
answer you drew from it, and a proposal they picked `Build that` on settles it. Never set `chosen`
on who else uses it unless they named an audience: it is the answer that publishes, and a member
pressing through the form would publish an app they meant to keep.

A member's ask for a new application is the go-ahead to open the interview: never ask whether to
proceed, and never confirm a plan the conversation already holds. The interview and the design are
the conversation's two asks before the create, and a guided build's proposal is its third; nothing
else is asked ahead of the form. A detail you still need — where the work lives, which account it
reads — and a blocker the job raises — a dependency not yet cleared, a source to work from in the
meantime — each ride the form as one more question, never a round of their own.

Never ask about the model, reasoning, sandbox size, icon, when it runs, or how far it acts alone.

## Draft it yourself

Name it for the work, never for a person: `invoice-intake`, not `iris`. A persona name invites the
member to treat it as a colleague instead of checking it. Check the name against the rule before
you say it: lowercase letters, digits, and inner hyphens only, 64 characters at most, and not a
name `object_list(kind="agent")` already returned — a name the create refuses is a step the member
watches fail for a reason they did not cause.

Write the whole prompt. Address the application in the second person, and cover who it works for,
the job, how it decides, where it stops and asks — and what its homepage reports, so the page the
application builds and keeps fresh is the page the member confirmed. Do not hand the member a
blank prompt to fill in, and do not paste their own sentence back as the prompt.

How it decides is settled for you: it works when the job needs it, takes the routine work of that
job on its own, and asks before anything unusual. Write that boundary into the prompt in the
application's own terms — what counts as routine here, what counts as unusual. A narrower one
(nothing without asking) or a wider one (everything inside the job) lands only when the member's
own words asked for it. Nobody picked that boundary, so the reply carrying the form states it in a
line: what the application settles itself, what it brings to a person.

## Design the homepage

Every application builds a homepage: the page members open on the Apps screen, where it states
what it is for, what it watches, its recent work, and what it needs. The design pass settles what
this app's page reports and how it is laid out. Finish the name and complete application prompt,
then call the `site` collection's `design_ufo_application` action with that exact
`application_name` and `application_prompt`. Omit `revision` on the first call.

The ufo application builder draws one SVG wireframe in the house style. The action shares that
exact SVG in the conversation and holds it for the named application's build. Do not load a design
skill, inspect the SVG, or share it again. After the action returns `ready`, end with `ask_user`:
`Build it`, `Change the design`.

On `Change the design`, call the same action once for the same name. Use the current complete prompt
and put the member's requested change in `revision`. A successful revision shares its new SVG before
it replaces the stored design. One page and at most two wireframes are the whole design pass. Create
the application under the accepted wireframe's name. Its first homepage gives the app builder that
SVG and digest to implement before QA, deployment, and binding.

## Create it

`Build it` on the design is the member saying go. Do not ask again, and do not restate the name for
approval — say what you are making in the reply that carries the form, so the words they are
agreeing to are on screen when they submit it. Then:

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
- Do not front-run the form with questions of your own: a member who answered a quiz and then the
  interview answered twice. What you still need is one more question on the form.
- Do not restate a phase you already delivered. The transcript holds it.
