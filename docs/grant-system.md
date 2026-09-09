# Grant system proposal

## Goal

Represent workspace, agent, and member capability scope with one access-path abstraction while
keeping ownership, content disclosure, and approval as separate decisions.

## Access path

Every turn executes at one path:

```text
workspace / agent / acting member
```

The acting member is the current speaker, or `on_behalf_of_member_id` for delegated work. A
memberless turn ends at the agent.

A resource binding may stop at any level:

| Binding | Meaning |
|---|---|
| `W` | Every agent and memberless turn in the workspace |
| `W/A` | Agent A, regardless of acting member |
| `W/A/M` | Agent A only while acting for member M |

A binding applies when it is a prefix of the turn's path. Workspace matching is always exact.

```python
@dataclass(frozen=True)
class AccessPath:
    workspace_id: UUID
    agent_id: UUID | None = None
    member_id: UUID | None = None
```

Construction refuses a member without an agent. Matching is positive and additive; absence is
denial. Policy ceilings may narrow access but no negative grant overrides a positive one.

## Existing mappings

| Resource | Binding |
|---|---|
| Workspace credential | `W` |
| Shared connection granted to A | `W/A` |
| Private connection owned by M and granted to A | `W/A/M` |
| Shared source granted to A | `W/A` |
| Private source owned by M and granted to A | `W/A/M` |
| Workspace-visible agent on the web | `W/A` |
| Private agent exposed to M on the web | `W/A/M` |

The mapping preserves the authoritative rows:

- `credential` is workspace-scoped.
- `connector_grant` supplies the agent; `connection.shared` and `owner_member_id` select `W/A` or
  `W/A/M`.
- `source_grant` supplies the agent; `source.subject` and `owner_member_id` select `W/A` or
  `W/A/M`.
- The web extension's audience row supplies `W/A/M`; agent workspace visibility supplies `W/A`.

These relationships are defined in the [core schema](../core/src/ufo/schema/tables.py), connector
selection binds the acting member in [tool context](../core/src/ufo/tools/context.py), and web
audience grants live in the [web extension](../extensions/web/ufo_ext_web/audience.py).

## Separate decisions

Access answers three questions:

```text
use  = capability binding matches the turn path
read = use and the resource audience is readable
edit = live speaker and owner/admin policy permits the change
```

Capability bindings do not replace:

- **Audience:** memory, conversations, artifacts, source pages, rooms, and foreign channels use
  disclosure subjects.
- **Ownership:** `owner_member_id` decides who controls a resource. It does not make that resource
  available to every agent the owner invokes.
- **Approval:** sharing, granting, and revocation require a live speaker. `on_behalf_of` preserves
  use authority but cannot perform a granting act.
- **Policy:** tool allowlists, profile-only tools, public-internet ceilings, seats, and admin roles
  constrain execution; they are not resource grants.
- **Leases:** transcript acknowledgements and signed artifact URLs are expiring disclosure records,
  not durable capability bindings.

The shared ownership gate remains in
[objects.py](../core/src/ufo/objects.py). Source content still requires both a matching capability
binding and a readable disclosure subject.

## Corrections

1. Remove the main-agent source-owner exception in
   [ext/context.py](../core/src/ufo/ext/context.py). A source requires an explicit agent edge.
   Connected sources already create the ordinary main-agent edge.
2. Resolve web audience email claims to member ids before constructing `W/A/M`.
3. Treat credential availability as the explicit workspace binding `W`. A credential requiring
   narrower access needs an agent or member binding rather than another injection exception.
4. Reserve **shared** for content audience. Capability scope uses **workspace**, **agent**, or
   **member**.
5. Rename `GrantStore` around connections; it is not the system-wide authority abstraction.

## Persistence

Keep storage typed. Do not add a polymorphic
`grant(resource_kind, resource_id, agent_id, member_id)` table. Such a table cannot hold foreign
keys to core and extension-owned resources, weakens cascade deletion, and becomes a second object
registry.

Each authoritative store projects its rows into `AccessPath`. One matcher consumes those paths at
tool dispatch, connector selection, sandbox environment construction, egress resolution, source
reads, and web admission. Database queries use the same workspace, agent, and acting-member inputs
without copying resource facts into a generic table.

## Required proof

One table-driven suite covers the complete matrix:

- another workspace never matches;
- another agent never matches `W/A` or `W/A/M`;
- another member never matches `W/A/M`;
- a memberless turn matches `W` and `W/A`, never `W/A/M`;
- `on_behalf_of=M` matches `W/A/M` for use but cannot grant or revoke;
- a foreign conversation may use a matching connector binding but cannot read workspace-shared
  content;
- source reads require both the binding and disclosure checks;
- owner and admin management rules do not imply use authority.
