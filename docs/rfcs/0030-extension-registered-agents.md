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
    spec: AgentSpec          # prompt, model, reasoning, internet policy, sandbox size, visibility
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

So a shipped agent is inert until a member wires it. For a pull-request reviewer that is three acts,
each keyed on the agent: a source registered against a GitHub account, that source granted to the
agent, and a connector connection granted to the agent. None of them can be shipped, because each is
an act of consent over an account a person owns.

**A provision declares a setup slot: what it needs, and what to do about it.**

```python
setup: AgentSetup = AgentSetup(
    connectors=("github",),
    instructions="Connect the GitHub account this agent publishes through.",
)
```

The slot lands on the row at creation, beside the prompt and the model. `connectors` names the
grants; `instructions` is what to do to obtain them. A connector names a *kind* of authority, never
an instance: any connection of that provider answers it, so which account stays the member's choice.

**A need must be settleable by the agent it is declared for**, which is why source feeds are absent.
`object_apply source` writes no grant when the workspace already holds that binding — it returns on
the existing one — and no verb attaches an existing source to a second agent the way
`GrantStore.attach` does for a connection. Declaring a source need would state a requirement a
member cannot always satisfy.

**The slot renders as a loadable skill on the agent that needs it.** Every grant binds to the
agent whose conversation it is made in — `connect_account` writes to the turn's own agent, and the
`source` kind takes no agent target at all — so the setup can only happen in a conversation with
that agent. It is told what it is missing and how to ask for it.

How a member reaches that agent from a surface bound only to the main agent (Slack, the CLI) is
open. RFC 0033 lets the main agent `spawn` a workspace agent, but the granting acts deliberately do
not travel: `connect_account` and `request_credentials` stay speaker-gated and refuse in a spawned
turn. So a member opens a conversation with the agent — the portal today — and the surface question
stands on its own.

It is a skill and not a prompt section because it is a task, not a capability: the index carries one
line, and the instructions reach the model only on the turn a member actually asks. It reaches
only a turn a member is speaking on, because the acts it names are speaker-gated — a spawn, a
schedule, or a source arrival cannot make a grant and has nobody to ask. A standing block
would tax every turn with work nobody requested, and would have to tell the agent not to act on it.

Nothing is pushed at a member and no conversation is opened on their behalf. A member asks the main
agent to finish the setup; the agent already knows what is outstanding and how to do it.

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
- **Let a provision create its own grants, so the agent installs ready to work.** Rejected on the
  same rule: a grant is consent over an account a person owns, and installing an extension is not
  that consent. The needs declaration is how far an extension may go.
- **Push the setup request at an admin when the agent lands.** Rejected: an agent-opened
  conversation reaches no member's rail, and the portal refuses a reply into one that is not the
  member's own chat with that agent, so the announcement would be unanswerable. Making it
  answerable means changing the web surface's access rule, which is a large change to buy a
  notification. The setup slot reaches the agent that can act instead, and costs no new surface.
- **Write the setup instruction into the main agent's stored prompt at install.** Rejected: that
  prompt is the member's, an install that edits it is the drift this RFC refuses everywhere else,
  and nothing would ever remove the instruction once the grants land. The slot is derived from the
  grants on every turn, so it is correct by construction and erases itself.
- **Contribute it as a prompt section instead of a skill.** Rejected: a section is standing context
  on every turn for a task nobody asked for. The skill index costs one line and the instructions
  load on demand.

## Open decisions

- How a member moves a workspace to a newer shipped spec, given that a row is never rewritten. The
  version makes a stale row readable; nothing acts on it yet.
- Whether a member can retire a shipped agent they no longer want, given that agents have no delete
  verb today.
- An attach verb for sources, mirroring `GrantStore.attach` for connections. Without it a shipped
  agent cannot be granted a feed the workspace already registered, so the setup slot cannot name
  one.
- Whether a shipped agent can bind to a surface of its own, which needs an admin-gated chat verb
  and its own OAuth semantics.
