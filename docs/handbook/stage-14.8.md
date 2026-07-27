# Source extension registry, tools, pages, and special sources  `stage-14.8`

This stage is shared support for bringing outside information into a workspace and making it usable after it has been synced. It is not the main work loop itself; it is more like the cabinet and label maker for connected sources.

The registry file is the master catalogue of built-in connectors, such as Slack, GitHub, Stripe, and Google Drive. It also gives each connected account a stable internal name, so the same account can be recognized reliably later. The tools file defines a “source,” meaning a saved connection to an outside service and the data streams the system should keep in sync. It also watches for changed synced pages and alerts subscribed conversations. The pages file presents synced documents as read-only workspace pages, so callers can list them, read a safely limited copy, or let the workspace owner forget one. The YC source file is a special connector for Y Combinator information, supporting trusted guidance collections and limited directory-style searches that can be registered for syncing.

## Files in this stage

### Read-only source pages
Expose synced source documents as bounded, read-only workspace pages that can be listed, read, or forgotten.

### `extensions/sources/ufo_ext_sources/pages.py`

`domain_logic` · `request handling`

A “page” here is not something a user writes by hand. It is one document that a source connector has already synced into the system, such as an issue, pull request, or other provider record. This file is the public object interface for those synced documents: it turns stored source-page rows into workspace objects that tools can list and read.

The important safety rule is visibility. A caller can read shared pages, and if the current turn belongs to a specific member, that member’s private pages too. They cannot see another member’s private pages. Another rule is ownership: pages cannot be created or edited through this object kind, because the sync driver owns them. Deleting a page really means “forget this synced page,” and only the workspace owner may do it.

The file also protects readers from huge page bodies. When a page is fetched, it reads the body from the blob store, but only up to 65,536 bytes. If the body is longer, the returned object says it was truncated. It also avoids cutting a UTF-8 character in half when possible, so text stays readable.

Think of this file as a library checkout desk for synced documents: visitors may browse what they are allowed to see, check out a safe preview of a document, but only the owner can remove a document from the catalog.

#### Function details

##### `_require_ext`  (lines 61–64)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure the current tool call has an ExtensionContext, which is the extension-facing gateway to source data. Without it, page operations would have no safe way to ask the extension for pages or forget a page.

**Data flow**: It receives the current ToolContext. If that context contains an extension context, it returns it. If not, it stops immediately with a runtime error, because page objects should never be used without that extension access.

**Call relations**: PageObjects._pages calls this before reading source and page records, and PageObjects.delete calls it before forgetting a page. It acts like a required key check before either browsing or deleting synced pages.

*Call graph*: called by 2 (_pages, delete).


##### `_audience_subjects`  (lines 67–73)

```
def _audience_subjects(ctx: ToolContext) -> frozenset[str]
```

**Purpose**: This helper decides which visibility areas the caller is allowed to read. It always includes shared pages, and it includes the current member’s private area when the request is tied to a member.

**Data flow**: It reads the audience member id from the ToolContext. If there is no member id, it returns only the shared subject. If there is a member id, it returns both the shared subject and that member’s subject string.

**Call relations**: PageObjects._pages uses this when asking the extension for source pages, so the page list is filtered before objects are built. It calls member_subject to create the private subject name in the same format used elsewhere.

*Call graph*: called by 1 (_pages); 1 external calls (member_subject).


##### `_page_timestamp`  (lines 76–86)

```
def _page_timestamp(provider_value: str | None, row_value: datetime) -> str
```

**Purpose**: This helper turns a page timestamp into a consistent UTC timestamp string. It accepts either a timestamp from the original provider or, if that is missing, the local row timestamp.

**Data flow**: It receives an optional provider timestamp string and a database row datetime. If the provider value exists, it parses it and requires it to include a timezone. If it is missing, it uses the row value and adds UTC when needed. It returns an ISO-formatted UTC string with microseconds.

**Call relations**: _Page.spec and _Page.fields call this when preparing page data for readers. It keeps timestamps consistent whether the time came from the outside provider or from this system’s own stored row.

*Call graph*: called by 2 (fields, spec); 2 external calls (fromisoformat, replace).


##### `_Page.name`  (lines 106–107)

```
def name(self) -> str
```

**Purpose**: This property gives the object name for a page. The name is simply the page row’s UUID written as text.

**Data flow**: It reads the page id stored on the _Page instance and converts it to a string. Nothing else changes.

**Call relations**: The page name is used by listing and lookup flows as the public identifier callers pass to object_get or delete. It is the bridge between the stored database id and the object interface.


##### `_Page.links`  (lines 109–117)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: This method describes what source object synced the page, when that source can be named. It lets readers navigate from a page back to the source binding that produced it.

**Data flow**: It checks whether the page has a source_name. If not, it returns no links. If it does, it creates one link with relation synced_by pointing at the source object with that name.

**Call relations**: PageObjects.get includes these links in the ObjectDetail it returns. The method builds ObjectRef and ObjectLink values so the object system can show the page’s relationship to its source.

*Call graph*: 2 external calls (__init__, __init__).


##### `_Page.spec`  (lines 119–132)

```
def spec(self, body: str, body_truncated: bool) -> PageSpec
```

**Purpose**: This method builds the full public description of a page, including its metadata and the bounded body text that was read from blob storage. It is what callers see as the page’s detailed content.

**Data flow**: It receives the body text and a flag saying whether the body was truncated. It combines those with the _Page’s stored fields, normalizes created and updated timestamps, and returns a PageSpec model.

**Call relations**: PageObjects.get calls this after it has read the page body. The method calls _page_timestamp so the returned spec has clean, UTC timestamp strings.

*Call graph*: calls 1 internal fn (_page_timestamp); 1 external calls (__init__).


##### `_Page.summary`  (lines 134–135)

```
def summary(self) -> str
```

**Purpose**: This method creates a short human-readable summary for page listings. It helps a person recognize the page without opening the full body.

**Data flow**: It combines the page title, source backend, stream, and subject into one line. It then cuts that line to the configured maximum length.

**Call relations**: PageObjects.list uses this summary for each ObjectRow in the list response. It is the quick label shown in the browse view.


##### `_Page.fields`  (lines 137–145)

```
def fields(self) -> dict[str, JsonValue]
```

**Purpose**: This method prepares the small set of metadata fields that can be shown, filtered, or ordered in list results. It avoids including the full body in lightweight browsing.

**Data flow**: It reads the page’s source id, backend, stream, title, and timestamps. It normalizes the timestamps and returns the values in a dictionary.

**Call relations**: PageObjects.list uses these fields when building each ObjectRow. Like _Page.spec, it relies on _page_timestamp so listing and detailed reads use the same timestamp format.

*Call graph*: calls 1 internal fn (_page_timestamp).


##### `PageObjects.list`  (lines 156–161)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This method returns a browsable page of synced page objects the caller is allowed to see. It is used when someone wants an overview rather than the full body of one page.

**Data flow**: It receives the tool context and a list query. It asks _pages for all visible page records, turns each one into an ObjectRow with a name, summary, and fields, then passes those rows through object_page so the query’s paging, filtering, and ordering rules are applied.

**Call relations**: This is the list operation for the PAGE_OBJECT store. It depends on PageObjects._pages to enforce visibility and gather source metadata, then hands the rows to the SDK’s object_page helper for final list shaping.

*Call graph*: calls 1 internal fn (_pages); 2 external calls (__init__, object_page).


##### `PageObjects.get`  (lines 163–193)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[PageSpec] | None
```

**Purpose**: This method returns the detailed view of one synced page, including a safe-size copy of its body. It is used when a caller has a page name and wants to read that page.

**Data flow**: It receives the context and page name. It finds the visible page, returns null if there is none, then streams the body from blob storage. It reads only a little beyond the maximum so it can tell whether the body was truncated, decodes the bytes as UTF-8 text, trims safely if the last character was incomplete, and returns an ObjectDetail with the spec, timestamps, and links.

**Call relations**: This is the get operation for the PAGE_OBJECT store. It asks PageObjects._find to locate the page, uses the blob capability on the context to read the stored body, then asks _Page.spec and _Page.links to shape the response.

*Call graph*: calls 1 internal fn (_find); 1 external calls (__init__).


##### `PageObjects.status`  (lines 195–196)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: This method reports that page objects have no separate status view. A page is either present and readable through get, or absent.

**Data flow**: It receives the context and page name but does not inspect them. It always returns null.

**Call relations**: The object interface includes a status hook, but this page kind does not use it. The real useful operations are list, get, and owner-only delete.


##### `PageObjects.apply`  (lines 198–201)

```
async def apply(self, ctx: ToolContext, name: str, spec: PageSpec, old: PageSpec | None) -> None
```

**Purpose**: This method rejects create and update attempts for pages. Pages are produced by the sync driver, so users and tools cannot author or edit them through this object kind.

**Data flow**: It receives the desired page name, new spec, and optional old spec, but does not apply them. It raises VerbNotSupported with a message explaining that pages must come from registered synced sources.

**Call relations**: The object system calls apply for create or update-style operations. Here it deliberately stops that flow, preserving the rule that only the content-sync driver writes page records.

*Call graph*: 1 external calls (__init__).


##### `PageObjects.delete`  (lines 203–209)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: This method lets the workspace owner forget one synced page. Forgetting tombstones the page so the existing page-change pipeline can clean up derived index data.

**Data flow**: It receives the context and page name. First it asks whether the speaker is the workspace owner. If not, it raises OwnerRequired. Then it finds the page visible to the caller, errors if it does not exist, and asks the extension context to forget that page id.

**Call relations**: This is the delete operation for the PAGE_OBJECT store. It calls ToolContext.speaker_is_owner for the owner gate, PageObjects._find to resolve the name, and _require_ext to get the extension method that performs the actual forget operation.

*Call graph*: calls 3 internal fn (speaker_is_owner, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects._find`  (lines 211–212)

```
async def _find(self, ctx: ToolContext, name: str) -> _Page | None
```

**Purpose**: This helper finds one visible page by its object name. It keeps get and delete from duplicating the same lookup logic.

**Data flow**: It receives the context and name. It asks _pages for all pages the caller may see, scans for the first page whose name matches, and returns that _Page or null if none matches.

**Call relations**: PageObjects.get uses this before reading a body, and PageObjects.delete uses it before forgetting a page. Because it relies on _pages, it inherits the same visibility filtering as list.

*Call graph*: calls 1 internal fn (_pages); called by 2 (delete, get).


##### `PageObjects._pages`  (lines 214–241)

```
async def _pages(self, ctx: ToolContext) -> tuple[_Page, ...]
```

**Purpose**: This helper gathers the live source pages the caller is allowed to see and enriches them with source information. It is the central place where raw source-page records become _Page objects used by list, get, and delete.

**Data flow**: It receives the context, requires an ExtensionContext, then reads all registered sources. It builds a map from source id to backend name and, when possible, a source object name using connector configuration. It asks for source pages limited to the caller’s allowed subjects, then converts each raw record into a _Page with page metadata, body reference, timestamps, and optional source name.

**Call relations**: PageObjects.list calls this to build list rows, and PageObjects._find calls it when get or delete needs one page. It uses _audience_subjects to enforce who can see what, ConnectorSourceConfig.model_validate to understand connector settings, and binding_name to connect pages back to their source object names.

*Call graph*: calls 2 internal fn (_audience_subjects, _require_ext); called by 2 (_find, list); 3 external calls (__init__, model_validate, binding_name).


### Source registry and tools
Define built-in connector registration, stable account naming, saved source objects, stream configuration, and change alerts.

### `extensions/sources/ufo_ext_sources/registry.py`

`config` · `startup and source registration`

This file answers two important questions for the source-sync system: “Which external services do we know how to connect to?” and “What should we call one registered connection?” Instead of searching the codebase automatically, it imports every supported connector class and puts them into one explicit registry. That makes the list easy to audit: adding a new provider means adding its connector here, like adding a new contact to an address book.

Each connector has a short name, called a slug, such as a backend name. The registry turns those names into a dictionary so the rest of the system can look up the right connector class when it sees a source that says it uses that backend. The file also protects against a dangerous mistake: two connectors using the same name. If that happens, it raises an error immediately instead of letting the system choose the wrong connector later.

The other key piece is `binding_name`, which creates a stable, compact name for a specific connected account. It combines the provider, account, and optional base URL, hashes them, and adds a short digest to the provider name. This avoids long or unsafe names while still making collisions unlikely.

#### Function details

##### `_connector_registry`  (lines 64–72)

```
def _connector_registry(connector_types: tuple[type[Connector], ...]) -> dict[str, type[Connector]]
```

**Purpose**: Builds the lookup table that maps each connector’s public name to its connector class. It exists so the rest of the system can ask for a connector by name instead of hard-coding every provider everywhere.

**Data flow**: It receives a tuple of connector classes. It starts with an empty dictionary, reads the `name` from each connector class, and stores that class under that name. If it sees the same name twice, it stops with a clear error. The result is a dictionary from connector name to connector class.

**Call relations**: This function is used in this file when `CONNECTORS` is created. At import time, the long explicit list of connector classes is passed in, and the function turns that list into the registry that later source-sync code can use to find the correct connector.


##### `binding_name`  (lines 79–85)

```
def binding_name(provider: str, account: str, base_url: str | None) -> str
```

**Purpose**: Creates one consistent internal name for a registered source connection. Someone would use it when they need the same provider/account/base-URL combination to always produce the same short binding name.

**Data flow**: It receives a provider name, an account identifier, and an optional base URL. It places those values into a small JSON object with sorted keys, hashes that text with SHA-256, keeps the first eight hexadecimal characters, and returns a name made from the provider plus that digest. Underscores in the provider name are changed to hyphens so the final name is cleaner.

**Call relations**: This function is the shared naming rule for source bindings. When other parts of the source system need to name or link a registered source, they can call this function so they all derive the same name from the same inputs. Internally, it hands the structured values to `json.dumps` for stable text formatting, then to `hashlib.sha256` to make the short fingerprint.

*Call graph*: 2 external calls (sha256, dumps).


### `extensions/sources/ufo_ext_sources/tools.py`

`domain_logic` · `object requests and page-change hooks`

A source is like a labeled pipe from an external service into UFO’s memory. This file lets members create, inspect, share, delete, and subscribe to those pipes through the object system. It turns many low-level source rows, one per stream, into one human-facing binding named from the provider, account, and tenant URL. That derived name matters: it prevents someone from accidentally changing the identity of a source by editing a friendly label.

When a source is applied, the file checks that the provider exists, that the requested streams are real, that any tenant URL has a safe expected shape, and that authentication is available. Authentication can come from a connected account, or from a workspace credential for direct providers. Private sources belong to the registering member. A private source can be made shared by the registrar or workspace owner, but changing streams or unsharing requires deleting and recreating it.

The special `subscribers` field is more open: any member who can see a source may add or remove only their own conversation id. Later, when pages synced from a shared source change, the hook groups changes by source and invokes subscribed conversations with page references they can read.

#### Function details

##### `_Binding.name`  (lines 149–150)

```
def name(self) -> str
```

**Purpose**: Builds the official object name for a source binding. The name is derived from the provider, account, and tenant URL so the same real connection always gets the same name.

**Data flow**: It reads the binding’s provider, account, and base URL, passes them to the shared naming helper, and returns the resulting stable name string.

**Call relations**: Other source-object methods use this property when listing, finding, deleting, and alerting about bindings. It delegates the exact naming rule to `binding_name` so names stay consistent with the registry.

*Call graph*: 1 external calls (binding_name).


##### `_Binding.created_at`  (lines 153–154)

```
def created_at(self) -> datetime
```

**Purpose**: Reports when the overall binding first came into existence. Because a binding contains several stream rows, it uses the oldest stream creation time.

**Data flow**: It reads every stream’s creation timestamp, chooses the earliest one, and returns that timestamp as the binding’s creation time.

**Call relations**: The detail view uses this value when presenting a binding as one object instead of many stream rows.


##### `_Binding.updated_at`  (lines 157–158)

```
def updated_at(self) -> datetime
```

**Purpose**: Reports the latest update time for the overall binding. This tells readers when any stream in the binding last changed.

**Data flow**: It reads every stream’s update timestamp, chooses the newest one, and returns that timestamp as the binding’s update time.

**Call relations**: The detail view uses this value so a grouped binding still shows a meaningful last-modified time.


##### `_Binding.spec`  (lines 160–168)

```
def spec(self, subscribers: tuple[str, ...]=()) -> SourceSpec
```

**Purpose**: Turns an internal binding back into the public source manifest that object users see and re-apply. This is the bridge from stored rows to a readable object specification.

**Data flow**: It reads the binding’s provider, streams, account, URL, sharing subject, and optional subscribers. It converts the internal direct-account marker back to an empty account id, then returns a `SourceSpec` object.

**Call relations**: Detail and apply flows use this when comparing the requested source against the existing one. It creates a `SourceSpec` so the object system can show and validate a familiar shape.

*Call graph*: 1 external calls (__init__).


##### `_Binding.summary`  (lines 170–172)

```
def summary(self) -> str
```

**Purpose**: Creates a short human-readable summary of a binding. It is used where a compact label is better than the full specification.

**Data flow**: It joins the stream names, combines them with the provider and account, trims the text to a fixed maximum length, and returns the summary string.

**Call relations**: Listings use this kind of summary to show sources at a glance, and `_alert_message` includes it in change notifications so subscribers know which external connection changed.

*Call graph*: called by 1 (_alert_message).


##### `_require_ext`  (lines 175–178)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Makes sure the current tool call has the extension context it needs. The extension context is the doorway to source rows, storage, credentials, and hooks.

**Data flow**: It receives a tool context, checks whether `ctx.ext` is present, and returns it. If it is missing, it raises an error because source objects cannot work without it.

**Call relations**: Most source operations call this before reading or writing extension data. It acts like a safety check at the edge of the file’s logic.

*Call graph*: called by 7 (_apply_owned, _bindings, _delete_owned, _detail, _resolved_account, _status, apply).


##### `_require_connectors`  (lines 181–184)

```
def _require_connectors(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Makes sure the current tool call has a connector registry. The registry tells the code which connected-account providers are available.

**Data flow**: It receives a tool context, checks whether `ctx.connectors` is present, and returns it. If not, it raises an error because account resolution would be unreliable.

**Call relations**: `SourceObjects._resolved_account` calls this when deciding whether a source should use a connected account or a direct workspace credential.

*Call graph*: called by 1 (_resolved_account).


##### `_bindings_from_ext`  (lines 187–216)

```
async def _bindings_from_ext(ext: ExtensionContext) -> tuple[_Binding, ...]
```

**Purpose**: Reconstructs user-facing source bindings from the lower-level source rows stored by the extension. Each stream is stored separately, but users think of them as one provider connection.

**Data flow**: It asks the extension for all registered source rows, ignores rows for providers outside this extension, reads each row’s connector config, groups rows by provider, account, and base URL, and returns sorted `_Binding` objects containing `_Stream` entries.

**Call relations**: `SourceObjects._bindings` uses this for object list/get/status flows. `on_page_change` also uses it to connect changed page rows back to the source binding that produced them.

*Call graph*: calls 1 internal fn (sources); called by 2 (_bindings, on_page_change); 3 external calls (__init__, __init__, model_validate).


##### `_subscribers_map`  (lines 219–231)

```
async def _subscribers_map(ext: ExtensionContext, name: str) -> dict[str, str]
```

**Purpose**: Reads the saved subscribers for one source. It returns not just conversation ids, but also the agent id to use when sending the later alert back into that conversation.

**Data flow**: It looks up a storage key based on the source name. If the stored value is a valid mapping of strings to strings, it returns a copy; if nothing is stored, it returns an empty map; if the stored data is malformed, it raises an error.

**Call relations**: Detail and status views call it to show subscriptions. Subscriber edits call it before changing the map. The page-change hook calls it to know which conversations should be alerted.

*Call graph*: called by 4 (_detail, _edit_subscribers, _status, on_page_change).


##### `_store_subscribers`  (lines 234–240)

```
async def _store_subscribers(ext: ExtensionContext, name: str, mapping: dict[str, str]) -> None
```

**Purpose**: Saves or clears the subscriber map for one source. It keeps subscription storage tidy by deleting the storage entry when no one is subscribed.

**Data flow**: It receives an extension context, source name, and conversation-to-agent map. If the map has entries, it writes them to storage; if the map is empty, it deletes the stored key.

**Call relations**: `SourceObjects._edit_subscribers` uses this after a conversation subscribes or unsubscribes. `SourceObjects._delete_owned` uses it to remove subscriptions when the source itself is deleted.

*Call graph*: called by 2 (_delete_owned, _edit_subscribers).


##### `_binding_identity`  (lines 243–244)

```
def _binding_identity(spec: SourceSpec) -> tuple[str, tuple[str, ...], str, str, bool]
```

**Purpose**: Extracts the parts of a source spec that define the actual source connection. This lets the code tell a subscription-only edit apart from a real source reconfiguration.

**Data flow**: It receives a `SourceSpec`, sorts the streams, and returns a tuple containing provider, streams, account id, base URL, and sharing flag.

**Call relations**: `SourceObjects.apply` uses it to compare the incoming spec with the old visible spec before deciding whether only the subscribers field changed.

*Call graph*: called by 1 (apply).


##### `_self_only_change`  (lines 247–254)

```
def _self_only_change(old: tuple[str, ...], new: tuple[str, ...], caller: str) -> None
```

**Purpose**: Enforces the rule that a conversation may only subscribe or unsubscribe itself. This prevents one conversation from silently changing another conversation’s alerts.

**Data flow**: It compares the old and new subscriber id sets, removes the caller’s own id from the difference, and raises a clear error if anything else changed. If only the caller changed, it returns without changing data itself.

**Call relations**: `SourceObjects.apply` calls this before allowing the lighter subscription-edit path. If it passes, the edit is handed to `_edit_subscribers`.

*Call graph*: called by 1 (apply).


##### `SourceObjects.apply`  (lines 276–291)

```
async def apply(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None) -> None
```

**Purpose**: Decides whether an object apply is just a subscription toggle or a real source create/update. Subscription toggles are allowed for any viewer; source changes go through the stricter owner rules.

**Data flow**: It receives the requested name, new spec, and old visible spec. If the source identity is unchanged, it checks that only the caller’s subscriber id changed and updates subscriber storage. Otherwise, it passes the request to the base object logic for normal ownership-gated apply behavior.

**Call relations**: The object system calls this when someone applies a source object. It uses `_binding_identity`, `_self_only_change`, `_require_ext`, and `_edit_subscribers` for the subscription shortcut; all other changes continue into the parent class flow, which later calls `_apply_owned`.

*Call graph*: calls 4 internal fn (_edit_subscribers, _binding_identity, _require_ext, _self_only_change).


##### `SourceObjects._edit_subscribers`  (lines 293–304)

```
async def _edit_subscribers(self, ext: ExtensionContext, name: str, desired: tuple[str, ...], caller: str, agent: UUID) -> None
```

**Purpose**: Adds or removes the current conversation from one source’s subscriber list. When adding, it also remembers which agent should receive the future alert.

**Data flow**: It reads the existing subscriber map, either inserts the caller conversation with the current agent id or removes the caller entry, and then writes the updated map back to storage.

**Call relations**: `SourceObjects.apply` calls this after proving the edit only affects the caller. It relies on `_subscribers_map` and `_store_subscribers` so the rest of the file sees a consistent subscriber record.

*Call graph*: calls 2 internal fn (_store_subscribers, _subscribers_map); called by 1 (apply).


##### `SourceObjects._owned_rows`  (lines 306–317)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow, ...]
```

**Purpose**: Builds the compact rows used when listing source objects. Each row tells the object system the source name, a short summary, and who owns or shares it.

**Data flow**: It gathers all bindings, converts each binding into an `OwnedRow`, and marks the owner as either a specific member or shared with the workspace.

**Call relations**: The member-owned object framework calls this during listing and visibility checks. It uses `_bindings` as the source of truth and wraps each binding in the object-system row format.

*Call graph*: calls 1 internal fn (_bindings); 2 external calls (__init__, __init__).


##### `SourceObjects._detail`  (lines 319–328)

```
async def _detail(self, ctx: ToolContext, name: str) -> ObjectDetail[SourceSpec] | None
```

**Purpose**: Returns the full public detail for one source object. This is what lets a user inspect the current manifest and then re-apply it safely.

**Data flow**: It finds the binding by name. If found, it reads the subscriber ids, builds the binding’s `SourceSpec` with those subscribers, and returns it with creation and update timestamps; if not found, it returns nothing.

**Call relations**: The object system calls this for object-get style reads. It uses `_find` for the binding and `_subscribers_map` for the one editable field that is stored separately.

*Call graph*: calls 3 internal fn (_find, _require_ext, _subscribers_map); 1 external calls (__init__).


##### `SourceObjects._status`  (lines 330–351)

```
async def _status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: Builds live status information for one source. It shows sharing, the caller’s own subscriber id, whether the caller is subscribed, and per-stream sync health.

**Data flow**: It finds the binding, reads the subscriber map, then creates a dictionary with shared/private status, the caller conversation id, subscription state, each stream’s next sync time, and each stream’s consecutive error count. For private sources, it may also include the owner member id.

**Call relations**: The object system calls this when status is requested. It combines `_find`, `_require_ext`, and `_subscribers_map` so callers can see both sync state and how to subscribe.

*Call graph*: calls 3 internal fn (_find, _require_ext, _subscribers_map).


##### `SourceObjects._apply_owned`  (lines 353–421)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Creates a new source binding or performs the limited allowed update of making an existing private source shared. It is the main validation gate for source registration.

**Data flow**: It checks that a speaking member exists, rejects subscribers during first registration, validates the provider and streams, validates the tenant URL, resolves the authentication account, checks that the supplied object name matches the derived name, and then either updates sharing on existing source rows or registers one source row per stream.

**Call relations**: The base member-owned object flow calls this after ownership rules have been applied. It uses `_resolved_account` for authentication choice, `_validated_base_url` for safe tenant URLs, `_find` to detect existing bindings, and the extension context to register sources or change their sharing subject.

*Call graph*: calls 4 internal fn (_find, _resolved_account, _require_ext, _validated_base_url); 6 external calls (__init__, __init__, __init__, member_subject, get, binding_name).


##### `SourceObjects._delete_owned`  (lines 423–430)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Deletes all stream rows that make up a source binding and clears its subscriptions. This removes the connection as one object even though it is stored as several rows.

**Data flow**: It finds the binding by name. If none exists, it raises an unknown-object error. If found, it removes each stream’s source row through the extension context and then clears the subscriber map.

**Call relations**: The base object flow calls this after delete permission has been checked. It uses `_find`, `_require_ext`, and `_store_subscribers` to remove both the synced source records and their alert settings.

*Call graph*: calls 3 internal fn (_find, _require_ext, _store_subscribers); 1 external calls (__init__).


##### `SourceObjects._resolved_account`  (lines 432–487)

```
async def _resolved_account(self, ctx: ToolContext, spec: SourceSpec) -> str
```

**Purpose**: Chooses which credential a source will use to authenticate. It supports both connected accounts, such as OAuth-style grants, and direct workspace credentials, often called BYOK or “bring your own key.”

**Data flow**: It reads the connector registry, available connected accounts, declared direct credentials, and the requested account id. It then either returns a connected account id, returns the direct-account marker after confirming the credential exists, or raises a clear error explaining what the user must connect or configure.

**Call relations**: `SourceObjects._apply_owned` calls this before registering rows, because every source row needs a stable account value in its connector config. It uses `_require_ext`, `_require_connectors`, and the tool context’s account lookup.

*Call graph*: calls 3 internal fn (connector_accounts, _require_connectors, _require_ext); called by 1 (_apply_owned).


##### `SourceObjects._find`  (lines 489–492)

```
async def _find(self, ctx: ToolContext, name: str) -> _Binding | None
```

**Purpose**: Looks up one binding by its derived object name. It is a small helper that keeps the rest of the code from repeating the same search.

**Data flow**: It gathers the current bindings, scans for one whose name matches the requested name, and returns that binding or `None`.

**Call relations**: Apply, delete, detail, and status operations call this whenever they need to work with a single source. It delegates the actual gathering work to `_bindings`.

*Call graph*: calls 1 internal fn (_bindings); called by 4 (_apply_owned, _delete_owned, _detail, _status).


##### `SourceObjects._bindings`  (lines 494–495)

```
async def _bindings(self, ctx: ToolContext) -> tuple[_Binding, ...]
```

**Purpose**: Returns all current source bindings visible to this object store layer. It is the class-level wrapper around the lower-level extension reconstruction helper.

**Data flow**: It checks that the tool context has an extension context, asks `_bindings_from_ext` to rebuild bindings from stored source rows, and returns the resulting tuple.

**Call relations**: `_find` and `_owned_rows` call this as their starting point. It uses `_require_ext` so callers fail fast if source objects were invoked without the needed extension services.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); called by 2 (_find, _owned_rows).


##### `on_page_change`  (lines 498–537)

```
async def on_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Sends alert turns to conversations subscribed to sources whose shared synced pages changed. It makes source subscriptions useful by turning background sync changes into visible notifications.

**Data flow**: It receives a hook payload, verifies it is a page-change batch, maps changed source ids back to bindings, groups changes by binding, ignores unclaimed or private changes, reads subscribers, builds one message per changed binding, and invokes each subscribed conversation with an idempotency key so replayed batches do not double-alert.

**Call relations**: The extension hook system calls this when synced pages change. It uses `_bindings_from_ext` to identify bindings, `_subscribers_map` to find listeners, and `_alert_message` to prepare the text sent into each subscribed conversation.

*Call graph*: calls 3 internal fn (_alert_message, _bindings_from_ext, _subscribers_map); 1 external calls (UUID).


##### `_alert_message`  (lines 540–551)

```
def _alert_message(binding: _Binding, changes: list[PageChange]) -> str
```

**Purpose**: Writes the human-readable notification text for a source change. The message tells the agent which source changed and which page objects to inspect.

**Data flow**: It receives a binding and a list of page changes, formats up to a small number of page references, counts extra and removed pages, includes the binding summary, and returns one message string.

**Call relations**: `on_page_change` calls this before invoking subscribed conversations. It uses `_page_reference` for each listed page and `_Binding.summary` to make the source recognizable.

*Call graph*: calls 2 internal fn (summary, _page_reference); called by 1 (on_page_change).


##### `_page_reference`  (lines 554–560)

```
def _page_reference(change: PageChange) -> str
```

**Purpose**: Formats one changed page as an object reference with a short readable label. This helps the alerted agent know what to fetch next.

**Data flow**: It receives a page change. If the page is not deleted and has a body, it takes the first line as a label; otherwise it uses a generic empty-page label. It returns text like a `page/<id>` reference plus the label.

**Call relations**: `_alert_message` calls this while building the list of changed pages included in a subscription alert.

*Call graph*: called by 1 (_alert_message).


##### `_validated_base_url`  (lines 563–597)

```
def _validated_base_url(provider: str, base_url: str | None) -> str | None
```

**Purpose**: Checks and normalizes tenant API URLs for providers that need them. This protects the system from unsafe or unexpected URLs while giving users provider-specific examples.

**Data flow**: It receives a provider and optional base URL. For providers with a fixed host, it rejects overrides. For tenant-specific providers, it checks that the URL is HTTPS, has no username, password, port, query, or fragment, and matches the allowed host and path pattern; then it returns a normalized URL without a trailing slash.

**Call relations**: `SourceObjects._apply_owned` calls this before account resolution and registration. It uses `urlsplit` to inspect the URL and the provider-specific rules defined near the top of the file.

*Call graph*: called by 1 (_apply_owned); 1 external calls (urlsplit).


### YC source connector
Provide the specialized YC source integration for trusted guidance collections and bounded directory-style searches.

### `extensions/yc/ufo_ext_yc/source.py`

`io_transport` · `source sync and tool request handling`

This file is the bridge between UFO and YC’s searchable data. Without it, the workspace could not import YC manuals, startup library content, or selected Bookface-style search results into shared memory for later use.

The file defines what a valid YC source request looks like, fetches matching records through the YC runner, and turns the returned CSV data into internal Page objects. A Page is the system’s standard “piece of source material”: it has text, a title, a stable reference, and a digest, which is a fingerprint used to notice changes.

There are two broad kinds of YC collections. Guidance collections, such as user manuals and the startup library, are fetched as authoritative material and do not accept a user query. Directory collections, such as companies, founders, investors, jobs, or forum posts, require a search query and a maximum result count. The config validator enforces this so the system does not ask YC for impossible or overly broad searches.

Fetching is careful and bounded. It skips work if the source was refreshed recently, times out long fetches, paginates results, checks that YC returned what it claimed, and truncates very large pages. The final tool, yc_index, is what a user-facing action calls to say: “sync these YC search results into shared memory.”

#### Function details

##### `YcSourceConfig.validate_collection`  (lines 79–88)

```
def validate_collection(self) -> 'YcSourceConfig'
```

**Purpose**: This function checks that the user’s YC source settings make sense for the chosen collection. It prevents guidance collections from being searched like directories, and prevents directory collections from being requested without a search query.

**Data flow**: It reads the chosen collection, optional query, and optional result limit from the config object. If the combination is invalid, it raises a clear error before any YC request is made. If a directory search has no explicit result limit, it fills in the default maximum and returns the corrected config.

**Call relations**: This runs automatically when a YcSourceConfig is created. That means later code, such as YcSource.fetch and yc_index, can assume the config is already shaped correctly instead of repeating these checks.


##### `YcSource.fetch`  (lines 138–155)

```
async def fetch(self, config: YcSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This is the main entry point for syncing a YC source. It decides whether a refresh is needed, calls YC when it is time to fetch, and returns the pages plus a new cursor marking when the sync happened.

**Data flow**: It receives a source config, an optional cursor from the previous sync, and authentication context. It compares the current time with the last sync time; if the data is still fresh, it returns no new pages and keeps the same cursor. Otherwise it fetches the collection within a timeout, catches the case where YC credentials are missing, and returns a SyncResult containing the fetched pages and a fresh timestamp cursor.

**Call relations**: The wider source-sync system calls this when it wants this YC source updated. If work is needed, fetch hands off to YcSource._fetch_collection to do the actual YC querying, then packages that result for the sync system. If credentials are not connected, it turns that into a StreamSkipped message so the sync can skip this source gracefully.

*Call graph*: calls 2 internal fn (__init__, _fetch_collection); 5 external calls (__init__, __init__, timeout, now, timedelta).


##### `YcSource._fetch_collection`  (lines 157–213)

```
async def _fetch_collection(self, config: YcSourceConfig, auth: SourceAuth) -> tuple[Page, ...]
```

**Purpose**: This function performs the repeated YC search requests needed to collect all requested records, one page at a time. It also checks that YC’s response is internally consistent before trusting it.

**Data flow**: It takes a validated config and authentication context. It builds a JSON request for each page, runs the matching YC search command through the runner, validates the returned wrapper, converts the CSV results into Page objects, and stops when it has reached the total available results or the configured maximum. It returns a tuple of Pages ready for indexing.

**Call relations**: YcSource.fetch calls this when a refresh is due. For every raw YC response, this function delegates the CSV-to-page conversion to YcSource._pages. It is the part of the flow that talks to the YC runner and enforces pagination limits.

*Call graph*: calls 1 internal fn (_pages); called by 1 (fetch); 1 external calls (dumps).


##### `YcSource._pages`  (lines 215–218)

```
def _pages(self, collection: str, body: str) -> tuple[Page, ...]
```

**Purpose**: This small router chooses the right way to turn YC CSV output into internal pages. Guidance records and directory search records have different shapes, so they need different formatting rules.

**Data flow**: It receives the collection name and a CSV string from YC. If the collection is a guidance collection, it sends the CSV to the guidance parser; otherwise it sends it to the directory parser. It returns the resulting Page objects unchanged.

**Call relations**: YcSource._fetch_collection calls this after each YC search response. It then hands off either to YcSource._guidance_pages or YcSource._directory_pages, depending on what kind of collection was fetched.

*Call graph*: calls 2 internal fn (_directory_pages, _guidance_pages); called by 1 (_fetch_collection).


##### `YcSource._guidance_pages`  (lines 220–238)

```
def _guidance_pages(self, collection: str, body: str) -> tuple[Page, ...]
```

**Purpose**: This function converts YC guidance CSV rows, such as manuals or startup library entries, into Page objects the workspace can store and search. It preserves the useful text fields and gives each page a stable identity.

**Data flow**: It receives a collection name and CSV text. For each CSV row, it validates the expected fields, combines the link, description, body, and categories into readable text, trims the text if it is too large, computes a SHA-256 digest as a change fingerprint, and creates a Page with a source reference, title, stream name, body, and digest. It returns all pages as a tuple.

**Call relations**: YcSource._pages calls this for guidance collections. During conversion it calls YcSource._bounded so that a single oversized YC record cannot create an unbounded page.

*Call graph*: calls 1 internal fn (_bounded); called by 1 (_pages); 4 external calls (__init__, DictReader, sha256, StringIO).


##### `YcSource._directory_pages`  (lines 240–271)

```
def _directory_pages(self, collection: str, body: str) -> tuple[Page, ...]
```

**Purpose**: This function converts YC directory-style search results, such as companies or founders, into Page objects. It turns each row’s available attributes into a simple readable block of text.

**Data flow**: It receives a collection name and CSV text. For each row, it keeps the record id and link, gathers all other non-empty columns as name-value attributes, formats them into text, truncates the text if needed, computes a SHA-256 digest, and creates a Page. The output is a tuple of these pages.

**Call relations**: YcSource._pages calls this for search collections. Like the guidance converter, it calls YcSource._bounded before making each Page so that imported search results stay within the source size limit.

*Call graph*: calls 1 internal fn (_bounded); called by 1 (_pages); 4 external calls (__init__, DictReader, sha256, StringIO).


##### `YcSource._bounded`  (lines 273–278)

```
def _bounded(self, body: str) -> str
```

**Purpose**: This function enforces the maximum size of a single YC page. It protects the system from storing or processing unexpectedly huge records.

**Data flow**: It receives a text body. If its UTF-8 encoded size is within the allowed byte limit, it returns the text unchanged. If it is too large, it keeps only the leading portion that fits, avoids cutting invalid text in the middle of a character, appends a clear truncation note, and returns that shortened text.

**Call relations**: Both YcSource._guidance_pages and YcSource._directory_pages call this just before creating Page objects. It is the final safety gate between raw YC content and stored workspace source pages.

*Call graph*: called by 2 (_directory_pages, _guidance_pages).


##### `yc_index`  (lines 281–304)

```
async def yc_index(ctx: ToolContext, args: YcIndexInput) -> ToolResult
```

**Purpose**: This tool function lets a user or agent start syncing a bounded YC directory search into shared workspace memory. It is meant for searches like companies, founders, investors, jobs, and similar collections.

**Data flow**: It receives a tool context and typed search arguments. It checks that the YC extension context exists, verifies that YC credentials are available, builds a YcSourceConfig from the requested entity, query, and result limit, and registers that source under the shared subject. It returns a short text message saying the sync has been requested and that repeating the same request is safe.

**Call relations**: The tool-dispatch layer calls this when the yc_index tool is invoked. It does not fetch the records itself; instead, it registers the source so the normal source-sync machinery can later call YcSource.fetch to do the actual import.

*Call graph*: 3 external calls (__init__, __init__, __init__).
