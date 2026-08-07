---
rfc: 0024
title: "Conversation slots — extension details beside a thread"
status: proposed
date: 2026-08-06
---

# Conversation slots — extension details beside a thread

> Replace the chat header's coding-specific Changes link with one right panel whose available views
> come from the conversation. Extensions declare typed, read-only slot providers; the web surface
> authorizes the conversation, lists the providers that have content, and lazily renders the selected
> one. One conversation may therefore expose any combination of Changes, Files, Sources,
> Artifacts, and Tasks, or none.

## Current state

`ChatPane` renders one Changes button (`extensions/web/frontend/src/views/ChatPane.tsx:28`), routing
has one `changes` variant (`extensions/web/frontend/src/lib/route.ts:36`), and `App` replaces chat
with `ChangesPane` (`extensions/web/frontend/src/App.tsx:333`). The projection is not coding-owned:
the web surface reads core `ufo.file_change` results from the durable transcript
(`extensions/web/ufo_ext_web/surface.py:1207`).

Other details have different sources of truth. Workspace files are live sandbox state; artifacts
are durable conversation records; research results are typed tool output but have no durable
conversation-keyed source record. A single Changes route cannot represent that matrix.

## Proposal

An extension may register a read-only provider:

```python
@dataclass(frozen=True)
class ConversationSlotProvider:
    id: str
    label: str
    icon: PortalIcon
    content: type[ConversationSlotPayload]
    summarize: ConversationSlotSummary
    read: ConversationSlotRead


@dataclass(frozen=True)
class ConversationSlotContext:
    ext: ExtensionContext
    conversation_id: UUID
    agent_id: UUID
    audience: Audience
    messages: tuple[Message, ...]
    compacted: bool
    projection: ConversationSlotPayload | None = None
```

`Manifest.conversation_slots` is empty by default. Core fails boot on a duplicate id, invalid label
or icon, unsupported payload type, or missing callback, then binds each callback to its declaring
extension's scoped context.

The web surface first applies the existing conversation-read gate, including the spawned-child
check. It then supplies the current durable messages and whether earlier messages were compacted;
the declaring extension does not gain a transcript-read capability outside that authorized call.
Only then does it invoke `summarize`; `None` means the provider contributes no slot to this
conversation. Selecting a returned slot invokes `read` lazily. A callback receives no request,
cookie, bearer, `SurfaceContext`, or authority primitive.

For a core/web-owned source such as live workspace files, the web surface lazily prepares one
bounded, schema-validated `projection` matching the provider's declared content type. This is data,
not a sandbox or database capability. Extension-owned providers leave it `None` and read their own
scoped records through `ext`.

```text
GET .../conversations/{conversation}/slots
    -> {slots: [{id, label, icon, kind, count}]}

GET .../conversations/{conversation}/slots/{slot}
    -> one bounded, discriminated payload
```

Initial payloads are exact models for changes, files, sources, artifacts, and tasks. An item may carry an
`ImagePreview` with a same-origin URL and one of `image/png`, `image/jpeg`, `image/gif`, or
`image/webp`, limited to 20 MiB per image; the portal renders an image only from that typed
capability and never infers one from a broad media type or filename. The portal owns their
renderers, panel chrome, ordering, loading, empty, unavailable, truncated, error, retry, and
responsive states. Extension HTML, JavaScript, CSS, arbitrary fetch URLs, and arbitrary JSON do not
enter the portal origin. A new layout requires a new host renderer or RFC 0023's isolated-frame
tier, not a widget schema.

The chat route carries the selected slot as URL state (`#/c/<conversation>?slot=<id>`). Desktop
opens one bounded right panel; narrow screens use a sheet. The controls are toolbar toggles, not
closable persisted tabs. Closing the panel removes the slot parameter without replacing the chat.

The first owners are:

| id | owner | source of truth |
|---|---|---|
| `changes` | core/web | bounded durable transcript projection |
| `files` | core/web | live conversation sandbox |
| `artifacts` | core/web | durable artifact records |
| `sources` | research | new typed conversation-keyed observation records |
| `tasks` | todos | durable conversation-keyed todo board in the extension-scoped store |

Every preview URL carries a signed capability binding its media type and byte size. The byte route
accepts only a claim no larger than 20 MiB, reads exactly that many bytes, and validates the claimed
PNG, JPEG, GIF, or WebP container with Pillow before responding with the signed media type and
`X-Content-Type-Options: nosniff`. Validation decodes every frame under explicit dimension, frame,
and aggregate decoded-pixel caps.

Files carry an `ImagePreview` only for eligible raster entries. Its capability binds the workspace,
conversation, fixed-size digest of the exact path, media type, and size; the URL retains the
root-conversation proof for spawned work. A path that cannot form a bounded safe same-origin URL
keeps its file row without a preview. Downloads and every non-raster file remain octet-streams. The
portal loads thumbnails lazily, expands images in place, and decodes only non-image files as text
under its 256 KiB text bound.

Exa is a search backend, not the Sources owner. Browser and connector outputs remain Files or
Artifacts until they gain a distinct durable conversation record. Memory is not a slot because it
has no conversation provenance. The todo board qualifies because its existing durable key is the
conversation id; the provider reads that record directly and does not infer task state from tool
messages or transcript text.

## Doctrine fit / implications

The manifest point and closed payload union are core because the slot-id namespace and extension
binding are deploy-wide and every host renderer needs an exact, bounded trust-boundary contract;
an extension cannot safely define arbitrary portal-origin data. The panel and every renderer remain
in web. A declaration grants nothing. It is presentation over a read projection, not an RFC 0022
member action, so mutations continue through audited turns.

The conversation-to-slots relation is computed from each provider's source of truth. There is no
`conversation_slot` table whose derived membership can become stale. One provider failure renders
that slot unavailable without suppressing its peers. Every payload is bounded and schema-validated.
The generic renderer receives behavioral tests, and each provider receives one producer-to-payload
binding test; declaration-equals-itself tests do not count.

`Manifest.conversation_slots` lands with a real extension-owned provider, not before one has a
consumer. Changes may move into the generic panel first as a web-owned provider.

## Alternatives

- **Keep Changes special.** Rejected: every new detail repeats its route, launcher, panel lifecycle,
  and authorization plumbing.
- **Let declarations name extension routes.** Rejected: those routes resolve a workspace, while the
  web surface owns member and conversation-audience authorization.
- **Persist slot membership.** Rejected: it duplicates provider state and becomes stale after
  compaction, sandbox retirement, or extension removal.
- **Run extension components in the portal.** Rejected by RFC 0023's origin boundary.

## Sources decision

Sources means the bounded set of results successfully retrieved by research tools in this
conversation. It does not claim that the final answer cited or relied on every row. A future cited
sources view needs an explicit answer-to-source relation rather than inferring one from URLs in
assistant text.
