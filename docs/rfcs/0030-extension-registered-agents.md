---
rfc: 0030
title: "Extension-registered agents — an extension ships a worker"
status: proposed
date: 2026-08-13
---

# Extension-registered agents — an extension ships a worker

> An extension can ship every part of a specialist except the specialist. It ships the tools, the
> skills, the subagent profiles, the object kinds, the connectors, and the prompt sections. The
> member must then assemble a worker out of them. This RFC adds one Manifest point that ships the
> worker itself: an extension declares an agent, and activation creates it in the workspace.

## What an extension can ship today

| Part | Manifest point |
|---|---|
| Tools | `tools` |
| Skills | `skills` |
| Subagent profiles | `subagents` |
| Object kinds | `objects` |
| Connectors and credential slots | `connectors`, `credentials` |
| Prompt text for every agent | `prompt_sections` |

Every one of these attaches to the agents a workspace already has. None of them creates an agent.

So an extension that means to deliver a working specialist — a support agent, a competitive
research agent, an incident agent — delivers a box of parts. A member must create an agent, write
its prompt, choose its model, choose its hardware, and know which tools to leave it. Each workspace
does this again, by hand, and each one gets it slightly different.

## The proposal

One Manifest point:

```python
@dataclass(frozen=True)
class AgentProvision:
    name: str
    spec: AgentSpec          # prompt, model, reasoning, internet policy, sandbox size
    tools: tuple[str, ...] | None = None
```

Activation creates the ordinary `agent` row. After that the workspace owns it: a member edits it,
reads it, and runs it like any other agent. The extension does not own it and does not rewrite it.

`tools` is the agent's tool allowlist, enforced when a turn loads. `None` means the ordinary
member-facing set. A name the deploy does not answer is absent rather than an error, so an agent
whose extension is uninstalled keeps working with fewer tools.

## What arrives, and what does not

A shipped agent starts with **no authority**. Creation copies no connector grant, no credential, no
source, and no memory. It has a prompt, a model, a tool list, and nothing to act on.

That is the security position, and it is deliberate. Installing an extension must never hand it the
workspace's connected accounts. A member grants each account to the new agent in chat, as they do
for any agent, and can withdraw it the same way.

## Lifecycle

A shipped agent is identified by the extension that ships it and the name that extension declared,
never by the row's own name. The row records the extension version that created it.

| Event | Result |
|---|---|
| The name is free | The row is created, stamped with the extension, the declared name, and the version |
| The name is taken by an identical row | The row is adopted, unchanged |
| The name is taken by a different row | The other row keeps the name. The shipped agent takes `<name>-<extension>` |
| Two extensions declare one name | Both land. The second takes the suffixed name |
| A member edits the agent | The edit stands, through every restart and every upgrade |
| The extension ships a new spec | Nothing is written. The new spec reaches new workspaces only |
| The extension is removed | The agent, its conversations, its memory, and its spend record all stay |

**A shipped row is written once and never again.** This is deliberate, not an omission. The
alternative — reconciling each row against the shipped spec — overwrites what the member tuned, and
no rule reliably separates a member's edit from a stale default. The cost is that a fix in a later
version does not reach a workspace that already has the agent; the recorded version is what makes
that visible instead of invisible.

Application runs at workspace onboarding, and at the first turn of a workspace that predates the
provision. It never sweeps the fleet at boot, because that would gate turn admission on a write for
every workspace.

## What this does not change

- **Subagents stay subagents.** A subagent profile is the shape of one bounded task — its prompt,
  its tools, its input, its answer, its round limit. An agent is a worker with an identity. They
  answer different questions and both keep their names. This RFC renames nothing.
- **Delegation is unchanged.** A spawn runs under the calling agent, as it does today.
- **No column is dropped, and no member surface is removed.**

## Alternatives

- **Let a member create the agent from a template the extension ships.** Rejected: the member still
  assembles it, and each workspace still drifts.
- **Reconcile the agent with the shipped spec on every upgrade.** Rejected: it overwrites what the
  member tuned.
- **Stop the deploy when two extensions declare one name.** Rejected: two packs that each ship a
  `researcher` are a normal install, not an error. Each lands under its own name.
- **Delete the agent when the extension is removed.** Rejected: removal is not consent to destroy
  conversations, memory, and spend history.
- **Let an extension write agents through a general core-write API.** Rejected: an extension gets no
  write primitive into core tables. It declares, and core applies.

## Open decisions

- How a member moves a workspace to a newer shipped spec, given that a row is never rewritten. The
  version makes a stale row readable; nothing acts on it yet.
- Whether a member can retire a shipped agent they no longer want, given that agents have no delete
  verb today.
- Whether a shipped agent can bind to a surface of its own, which needs an admin-gated chat verb
  and its own OAuth semantics.
