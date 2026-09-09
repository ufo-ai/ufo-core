# ufo — architectural mission

ufo is a shared, workspace-scoped runtime for agents. It brokers execution, authority, tools,
data, and durable state across chat, automation, and extension-provided surfaces.

The workspace is the tenant boundary. An agent belongs to one workspace and may also have a member
owner. It defines its model, prompt, tools, network policy, and sandbox size. A conversation is
permanently bound to an agent, and each turn runs as that agent. Member authority begins with the
person who starts the inbound turn; internal work may carry an explicit `on_behalf_of` member. Tool
calls preserve that identity rather than inheriting unrestricted workspace authority. These
relationships are encoded in the [core schema](../core/src/ufo/schema/tables.py) and
[turn engine](../core/src/ufo/loop/engine.py).

## Authority model

ufo separates five kinds of scope:

| Concept | Binding |
|---|---|
| Extension, connector, surface, carrier, or provider declaration | Deployment-wide through the active extension pack |
| Agent, credential, connection, source, and stored object | Workspace |
| Private connection or source | Owned by a member |
| Connector or source grant | Attaches a workspace resource to an agent |
| Effective access | Agent grants plus the current member, audience, and resource policy |

A connector is not itself bound to an agent or workspace. It is a deployment capability supplied
by an extension. A **connection** is a member-owned external account stored inside a workspace. A
**connector grant** permits an agent to use that connection. At execution time, a private
connection is available only when the acting member owns it; a shared connection is available to
any authorized member using the granted agent. The broker enforces both the agent grant and member
ownership checks in [tool context](../core/src/ufo/tools/context.py).

Credentials use a different model. A credential occupies a workspace slot and has no per-agent
grant. Extensions may read only the slots they declare, while sandbox injection exposes eligible
workspace credentials through controlled egress. Connections represent member-owned accounts;
credentials represent workspace configuration.

Connected account ingestion is separate from live connector access. A **source** is a workspace
dataset with an owner and disclosure subject. A **source grant** lets an agent use that dataset.
One source can be synchronized once and granted to several agents without duplicating its data.
Reading it still requires both an agent grant and compatible member or conversation visibility.

Memory and object access are not workspace-wide by default. Memory is workspace data partitioned
by disclosure subject, not data owned by an agent. Object visibility is limited to shared objects,
objects owned by the acting member, and narrowly defined administrative cases. Cross-agent
operations are allowed only for verbs that explicitly support an agent target and only when the
requesting member can see that agent. These rules live at the object boundary in
[objects.py](../core/src/ufo/objects.py).

## Execution model

Every conversation has one agent and one conversation sandbox. The selected sandbox carrier is
deployment configuration; the sandbox handle belongs to the conversation. The agent supplies the
initial size and network policy. Profile subagents can share the parent conversation sandbox,
while independently spawned workspace agents receive their own conversation and sandbox.

Member chat, automation, jobs, and extension invocations converge on the same durable turn
representation and execution engine. Most external work enters through admission; subagents have
a specialized admission path but still create the same conversations and turns and use the same
queue and engine. A turn records its transcript, tool execution, usage, terminal result, and
created object references. Generic object mutations are journaled, but ufo is not a universal
ledger for every external side effect.

## Core and extensions

Core owns the invariants that must be uniform: workspace isolation, turn execution, identity
propagation, authorization, durable state, accounting, extension loading, and failure handling.

Extensions supply product capability: tools, object kinds, connectors, sources, surfaces, jobs,
prompt sections, models, sandbox carriers, and provider backends. The active extension pack is
selected for a deployment, not separately by each workspace. Extension handlers receive
workspace-scoped contexts and cannot import core internals. The manifest and loader define this
boundary in [manifest.py](../core/src/ufo/ext/manifest.py) and
[loader.py](../core/src/ufo/ext/loader.py).

## Experimentation

ufo evaluates agents locally or against a remote deployment through the real turn engine. Eval
runs preserve inputs, outputs, tool traces, artifacts, costs, timing, grading evidence, and runtime
identity. Text ablations run isolated control and variant stacks. The self-improvement extension
uses recorded trajectories to propose and evaluate prompt changes before producing a governed
proposal.

Experimentation covers prompt and model changes. Feature flags provide workspace-targeted rollout,
and extension development uses a separate disposable-workspace workflow. It does not provide a
general online A/B system for extensions, providers, or sandbox backends.

ufo provides one durable agent runtime while keeping deployment capabilities, workspace data,
member authority, agent grants, and experimental evidence explicit and independently enforceable.
