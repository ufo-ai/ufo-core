---
name: create-application
description: "Load when a member asks for a new application of their own: an app that watches something, a separate assistant for one job, an agent for the team. Not for setting up an app that already exists, and not for saving a skill."
---
# New application

An application is an `agent` object. Apply with `create_only: true`: a name already taken is
otherwise an update, and would rewrite that application instead of making yours.

## Interview

One `ask_user`, these three questions, `title` `Create new app`, and an `icon` that draws the job.

| Question | What it settles |
|---|---|
| What job is it for? | the prompt |
| What may it do on its own? `Nothing without asking` / `Routine work, asks about the rest` / `Everything inside its job` | how much the prompt lets it decide |
| Who else uses it? `Just me` / `Everyone in the workspace` | `visibility`: `private` or `workspace` |

Ask all three every time, and set `chosen` on each one the member's own words already settle — a
member who described the whole job in one sentence still sees the three answers you drew from it.
Never set `chosen` on who else uses it unless they named an audience: it is the one answer that
publishes, and a member pressing through three questions would publish an app they meant to keep.

Never ask about the model, reasoning, sandbox size, or icon.

## Draft it yourself

Name it for the work, never for a person: `invoice-intake`, not `iris`. A persona name invites the
member to treat it as a colleague instead of checking it.

Write the whole prompt. Address the application in the second person, and cover who it works for,
the job, how it decides, and where it stops and asks. Do not hand the member a blank prompt to fill
in, and do not paste their own sentence back as the prompt.

## Create it

The answered form is the member saying go. Do not ask again, and do not restate the name for
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
when it reads the open web. Omit `icon` and `sandbox_size`.

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
