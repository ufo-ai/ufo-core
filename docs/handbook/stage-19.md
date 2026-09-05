# Public SDK, extension APIs, and generated protocols  `stage-19` (cross-cutting infrastructure)

This stage is the public front door for extension authors and nearby integrations. It is shared behind-the-scenes support, not the main user work loop. Its job is to give outside code stable names, safe data shapes, and approved entry points, even while the system’s private internals keep changing.

The SDK authoring helpers define what an extension can declare and what limited toolbox it receives while running. The capability interfaces expose plug-in points for browser control, search, models, memory, terminal access, objects, and similar services. The identity and credential APIs provide safe public access to login tokens, connectors, OAuth, grants, and web sessions. The workspace and surface helpers expose billing, audience, listing, hub, and channel-integration types.

The sample extension acts like a test dummy that exercises all official hooks. The iMessage protocol area supplies generated message formats and network call wiring, so separate pieces can exchange the same structured data. Finally, conversation_slots.py defines what extensions may place into conversation portal areas, with size and safety rules so the display layer gets predictable content.

## Sub-stages

- [Public SDK authoring and execution helpers](stage-19.1.md) `stage-19.1` — 13 files
- [Public SDK capability provider interfaces](stage-19.2.md) `stage-19.2` — 12 files
- [Public SDK identity, credentials, connectors, and grants](stage-19.3.md) `stage-19.3` — 7 files
- [Public SDK workspace, billing, audience, and surface helpers](stage-19.4.md) `stage-19.4` — 9 files
- [Sample extension hook coverage](stage-19.5.md) `stage-19.5` — 1 files
- [iMessage extension provider and generated protocol plumbing](stage-19.6.md) `stage-19.6` — 22 files

## Files in this stage

### Public SDK, extension APIs, and generated protocols
### `core/src/ufo/runtime/ext/conversation_slots.py`

`data_model` · `request handling`

This file is a contract between extensions and the conversation portal. A portal slot is a named area of the user interface where an extension can publish extra conversation information, like files produced during the chat, source links, task progress, websites, or scheduled automations. Without these definitions, each extension could send different or unsafe data, and the portal would not know how to display it reliably.

Most of the file is made of Pydantic models. Pydantic is a validation library: it checks incoming data and turns it into well-defined Python objects. These models forbid unknown fields, are frozen so they cannot be changed after creation, and set limits on text lengths and list sizes. That keeps portal payloads small, stable, and harder to misuse.

Several URL fields are checked carefully. Artifact, source, site, and image-preview links must be normal HTTP or HTTPS web links and must not contain embedded usernames or passwords. Image preview URLs are checked even more strictly because they may be drawn inside browser pages; the code rejects fragments, backslashes, and control characters that could confuse rendering or escaping.

The file also defines provider and context dataclasses. These describe how an extension registers a slot, what data type it will return, and what conversation information is available when the system asks the extension to summarize or read that slot.

#### Function details

##### `ImagePreview.drawable_url`  (lines 52–69)

```
def drawable_url(cls, value: str) -> str
```

**Purpose**: Checks that an image preview link is safe and usable by a browser. It protects the portal from image URLs that are missing a real web host, include hidden credentials, contain fragments, or include characters that could be interpreted in surprising ways.

**Data flow**: A URL string comes in when an ImagePreview object is being created. The function breaks the URL into parts, decodes escaped characters, and inspects the scheme, host, username, password, fragment, backslashes, and control characters. If the URL is acceptable, the same string comes out unchanged; if not, model creation stops with a validation error.

**Call relations**: Pydantic calls this validator while building an ImagePreview. The validator relies on urllib.parse.urlsplit to understand the URL, urllib.parse.unquote to inspect the decoded form, and unicodedata.category to spot control characters. Its result decides whether the preview can be included in a conversation artifact payload.

*Call graph*: 3 external calls (category, unquote, urlsplit).


##### `ConversationArtifact.http_url`  (lines 85–96)

```
def http_url(cls, value: str | None) -> str | None
```

**Purpose**: Checks the optional download or view link attached to a conversation artifact. It allows the artifact to have no URL, but if a URL is present, it must be a normal HTTP or HTTPS link without embedded login information.

**Data flow**: The artifact URL value comes in as either a string or None. If it is None, the function returns None immediately. If it is a string, the function splits it into URL parts and rejects it unless it has an http or https scheme, a host name, and no username or password. A valid URL is returned unchanged.

**Call relations**: Pydantic calls this validator when creating a ConversationArtifact. The function uses urllib.parse.urlsplit to inspect the link before the artifact is accepted into an ArtifactsSlotPayload that the portal may display.

*Call graph*: 1 external calls (urlsplit).


##### `ConversationSource.http_url`  (lines 117–126)

```
def http_url(cls, value: str) -> str
```

**Purpose**: Checks that a source citation link points to a normal web address. This matters because source links may be shown to users, so they should not contain embedded credentials or malformed non-web schemes.

**Data flow**: A source URL string comes in during ConversationSource creation. The function splits it into parts and verifies that it is HTTP or HTTPS, has a host, and does not include a username or password. If it passes, the original URL string comes out; otherwise, validation fails.

**Call relations**: Pydantic invokes this validator while building a ConversationSource. It uses urllib.parse.urlsplit to inspect the link before the source can be included in a SourcesSlotPayload for the conversation portal.

*Call graph*: 1 external calls (urlsplit).


##### `TasksSlotPayload.consistent_progress`  (lines 155–169)

```
def consistent_progress(self) -> 'TasksSlotPayload'
```

**Purpose**: Makes sure the visible task list and the summary counts tell the same story. It prevents impossible task progress, such as having more completed tasks than total tasks or showing a full untruncated list whose length does not match the total count.

**Data flow**: A complete TasksSlotPayload object comes in after its fields have been individually checked. The function compares completed_count, total_count, the number of visible tasks, each visible task status, and the truncated flag. If all numbers are consistent, it returns the same payload object; if any count contradicts another, it raises a validation error.

**Call relations**: Pydantic calls this model-level validator after constructing a TasksSlotPayload. It does not hand work to other project functions; instead, it acts as the final sanity check before task progress can be shown in a conversation slot.


##### `ConversationSite.http_url`  (lines 184–193)

```
def http_url(cls, value: str) -> str
```

**Purpose**: Checks that a site link is a normal HTTP or HTTPS web address without embedded credentials. This keeps site entries safe and predictable before they are shown or used by the portal.

**Data flow**: A site URL string comes in during ConversationSite creation. The function splits it into URL parts and confirms that it has an http or https scheme, a host name, and no username or password. A valid URL is returned unchanged; an invalid one causes validation to fail.

**Call relations**: Pydantic calls this validator while creating a ConversationSite. It uses urllib.parse.urlsplit to inspect the URL before the site can be included in a SitesSlotPayload for conversation-level display.

*Call graph*: 1 external calls (urlsplit).

## 📊 State Registers Touched

- `reg-pack-extension-registry` — The approved set of installed packs and extensions, including what tools, jobs, agents, hooks, providers, and surfaces they add.
- `reg-extension-store` — The per-workspace saved data that extensions use to remember their own settings and state.
- `reg-tool-catalog` — The shared menu of tools the agent may call, including built-in tools, extension tools, and guarded bridge tools.
- `reg-model-provider-catalog` — The shared list of available AI models and providers, including limits, prices, credentials, and adapter rules.
- `reg-auth-tokens-sessions` — The login, surface, sandbox, and signing tokens that prove who a request belongs to and what it may access.
- `reg-authority-context` — The current acting identity for runtime work, saying which workspace, member, and agent are allowed to act.
- `reg-surface-routing-state` — The saved routing information that maps web, Slack, iMessage, terminal, hosted app, and public-link traffic to the right workspace and conversation.
- `reg-audience-visibility-state` — The shared privacy labels that decide who may read or join conversation content and workspace objects.
- `reg-credential-vault-connections` — The lockbox of account connections, OAuth grants, API keys, BYOK attempts, and agent permissions to use outside services.
- `reg-memory-index-profiles` — The searchable memory layer made from synced pages, chunks, embeddings, summaries, facts, and member or workspace profiles.
- `reg-object-change-journal` — The durable history of object changes, recording who changed what and what the object looked like before and after.
- `reg-schedules-automations` — The durable alarm clock for future work, pauses, monitors, source-change triggers, notification inbox items, and extension jobs.
- `reg-portal-slots-ui-state` — The structured conversation portal display state that extensions can fill with artifacts, sources, tasks, sites, and automations.
