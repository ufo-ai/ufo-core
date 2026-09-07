# External provider and communication extension registrations  `stage-4.5`

This stage is part of startup and shared behind-the-scenes support. It is where optional outside connections introduce themselves to the main UFO system, much like plug-in cards being read before a machine starts work. The manifest files are those cards. The browser manifest registers browser tools, a helper agent, prompt text, and any outside capability needed for web access. The enrichment package marker simply makes that folder importable, while its manifest describes company-website confirmation, optional work-profile lookup, read-only profile display, and short conversation summaries. It also checks whether a provider API key exists before enabling lookup features. The gbrain package marker does the same import job, and its manifest registers knowledge sources from GitHub or a local folder, including an optional GitHub token. The iMessage manifest registers the messaging surface, the connect action, and required cloud secrets. The Slack package marker enables importing, while the Slack manifest advertises its routes, credentials, tools, hooks, and workspace status facts. Together, these files let the core app discover what each extension can do.

## Files in this stage

### Browser access registration
Declares the browser extension’s tools, helper agent, prompt text, and required external capability.

### `extensions/browser/ufo_ext_browser/manifest.py`

`config` · `startup / extension registration`

This file is like the label and packing list on a browser automation add-on. The main system needs a simple way to ask, “What does this extension provide?” and this file answers that question.

It names the extension as `browser`, gives it a version, loads a prompt section from `prompts/browser_section.md`, and combines three main pieces into one declaration. First, it includes the browser tools that can actually operate a browser. Second, it includes delegation tools, which let the main agent ask a specialized browser subagent to do web tasks instead of using the raw browser controls directly. Third, it registers the browser subagent profile, which describes that specialized child agent.

The important design idea is separation. The main agent does not directly hold the full browser-control surface. Instead, it gets tools like `browser_task` or `wide_browse`, which delegate work to a browser-focused subagent. That subagent then receives the browser tools it needs. This is safer and clearer, like asking a trained driver to operate a car rather than handing the steering wheel to everyone in the room.

Without this file, the system would not know that the browser extension exists, what tools it contributes, what prompt guidance to show, or that it depends on CDP providers, which are browser-control backends based on the Chrome DevTools Protocol.

#### Function details

##### `manifest`  (lines 23–31)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the browser extension’s formal declaration. The system uses this declaration to learn the extension’s name, version, tools, subagent, prompt text, and required browser-control support.

**Data flow**: It starts with constants and imported pieces: the extension name and version, the browser tools, delegation tools, browser subagent profile, and prompt text already read from disk. It wraps the prompt text in a `PromptSection`, then places everything into a `Manifest`, which is the structured description the host system can consume. The result is a complete manifest object; it does not change outside state.

**Call relations**: When the host system asks this extension what it provides, this function is the answer point. It creates a `PromptSection` so the main agent can be taught when and how to delegate browser work, then creates a `Manifest` that packages that prompt together with the browser tools, delegation tools, browser subagent profile, and the required `cdp_providers` capability.

*Call graph*: 2 external calls (__init__, __init__).


### Enrichment provider capability
Marks and registers the enrichment extension, including company website confirmation, profile lookup, read-only profile display, and startup availability checks.

### `extensions/enrichment/ufo_ext_enrichment/__init__.py`

`other` · `package import`

This is the front door of the `ufo_ext_enrichment` package. In Python, an `__init__.py` file tells the interpreter that a folder should be treated as an importable package. Here, the file only contains a short docstring: “The enrichment extension.” That means it does not set up services, load configuration, define types, or run any feature logic.

Its value is mostly organizational. It lets other parts of the project refer to this extension as a package, much like putting a label on a folder in a filing cabinet. Without this file, depending on the Python version and packaging setup, imports or package discovery for this extension could be less clear or fail in older-style tooling.

Any real enrichment behavior lives in other files inside this package. This file simply identifies the package boundary and provides a brief description for readers and documentation tools.


### `extensions/enrichment/ufo_ext_enrichment/manifest.py`

`domain_logic` · `startup, user action, scheduled job, prompt handling`

This file is the front door and main behavior for the enrichment feature. The real-world problem it solves is simple: the system wants a helpful starting guess about who a workspace member works for, but it must not look anyone up unless they have agreed. A member gives that agreement by confirming a website. The file records that answer, then a scheduled job does the external lookup later. That separation matters: the portal form stays fast, and members who answer after the first run are still picked up by the job.

The file is careful about consent. If the website field is empty, consent is removed and no lookup happens. If the website is a free email provider such as gmail.com, it is not treated as a company website. Without this safeguard, the system might wrongly say a member works for their mail provider.

Once a profile exists, the file exposes it as a read-only object named enrichment_profile. Users can list or read rows, but cannot edit or delete them directly; the confirm_website action is the only write path. On each user prompt, the inject hook may add a short, clearly walled-off note about the company and speaker. “Walled” means the text is marked as third-party, uncertain data so the agent treats the member’s own words as more trustworthy.

At startup, manifest() registers the object and hook always. It only registers the website action and scheduled job if a provider is available.

#### Function details

##### `website_host`  (lines 162–172)

```
def website_host(raw: str) -> str | None
```

**Purpose**: Turns a website answer into a clean domain name. It accepts either a full URL or a bare domain, removes common clutter like a leading www., and rejects text that is not really a website.

**Data flow**: It receives raw text from the member. It trims spaces, lowercases it, parses it like a URL, extracts the host name, removes a leading www., and returns the host. If the field is empty it returns nothing; if the host has no dot, it raises an error because the answer does not look like a website.

**Call relations**: Enrichment.confirm_website calls this before recording a member’s answer. That means the rest of the enrichment flow stores a normalized website value instead of many different spellings of the same site.

*Call graph*: called by 1 (confirm_website); 1 external calls (urlsplit).


##### `Enrichment.confirm_website`  (lines 182–203)

```
async def confirm_website(self, ctx: ToolContext, args: ConfirmWebsiteInput) -> ToolResult
```

**Purpose**: Records a speaking member’s consent decision and confirmed website. It does not perform the lookup itself; it prepares work for the scheduled enrichment job.

**Data flow**: It receives the tool context and the website form input. It checks that there is an extension context and a speaking member, cleans the website, confirms the member is seated in the workspace, records whether consent is granted, stores the website unless it is a free mail domain, and deletes any older profile row for that member. It returns a short message saying either that the profile will be built soon or that nothing was looked up.

**Call relations**: This is the write action registered by manifest() when a provider exists. It uses website_host to validate the answer, uses the store layer to record consent and clear stale data, and leaves the actual external lookup to Enrichment.tick.

*Call graph*: calls 2 internal fn (_require_ext, website_host); 5 external calls (__init__, __init__, __init__, __init__, __init__).


##### `Enrichment.tick`  (lines 205–224)

```
async def tick(self, ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled background work that builds missing enrichment profiles for members who have consented. It processes a small batch at a time and backs off if the external provider refuses or fails.

**Data flow**: It reads due members from the database, then for each one asks _lookup to build a profile from their email and website. On success, it writes the profile back to storage. If the provider returns an enrichment error, it records a pause for the workspace, logs a warning, and stops early. If work was done successfully, it clears any previous pause.

**Call relations**: manifest() registers this as the scheduled enrichment job. It is the consumer of consent recorded by Enrichment.confirm_website and the producer of profile rows later read by ProfileObjects and inject.

*Call graph*: calls 2 internal fn (transaction, _lookup); 3 external calls (__init__, __init__, warn).


##### `Enrichment._lookup`  (lines 226–233)

```
async def _lookup(self, email: str, website: str | None) -> Profile
```

**Purpose**: Performs the provider lookups needed to build one member’s profile. It asks for person data by email and company data by the confirmed website or, if no website was confirmed, by the email domain.

**Data flow**: It receives an email address and optional website. It asks the provider for a person match using the email. It chooses a company domain from the website if present, otherwise from the email address. If that domain is a free mail provider, it skips the company lookup; otherwise it asks the provider for company data. It passes the gathered pieces to _profile and returns the finished Profile.

**Call relations**: Enrichment.tick calls this for each due member. It hands the final packaging step to Enrichment._profile so the lookup choices and the Profile construction stay separate.

*Call graph*: calls 1 internal fn (_profile); called by 1 (tick).


##### `Enrichment._profile`  (lines 235–249)

```
def _profile(self, email: str, website: str | None, person: Person | None, company: Company | None) -> Profile
```

**Purpose**: Packages provider results into the project’s Profile data shape. It also marks whether anything useful was found.

**Data flow**: It receives the email, website, optional person result, and optional company result. It sets the status to matched if either person or company data exists, otherwise no_match. It records the provider source and the current time, then returns a Profile object.

**Call relations**: Enrichment._lookup calls this after provider calls finish. The resulting Profile is what Enrichment.tick writes to storage and what later readers display or inject.

*Call graph*: called by 1 (_lookup); 2 external calls (__init__, now).


##### `inject`  (lines 252–274)

```
async def inject(ctx: HookContext) -> HookOutcome
```

**Purpose**: Adds a short enrichment note to a user’s prompt when profile data is available. This gives the agent useful context while clearly marking it as uncertain third-party information.

**Data flow**: It receives a hook context for a user prompt. If there is no active turn, it returns nothing. Otherwise it reads the speaker’s profile if known and a list of stored profiles, chooses company information from the speaker or another row, builds short company and member lines, wraps them in an untrusted-data wall, and returns an InjectContext. If there is nothing useful to say, it returns nothing.

**Call relations**: manifest() registers this for the user_prompt_submit event. It reads rows produced by Enrichment.tick and uses _company_lines, _member_lines, and wall to prepare safe text for the conversation.

*Call graph*: calls 2 internal fn (_company_lines, _member_lines); 3 external calls (__init__, __init__, wall).


##### `_company_lines`  (lines 277–285)

```
def _company_lines(company: Company | None) -> list[str]
```

**Purpose**: Builds a compact one-line description of a company for prompt injection. It avoids empty output if the company has no usable name.

**Data flow**: It receives an optional company. If there is no company or no name, it returns an empty list. Otherwise it clips the company name and selected details such as industry, size, and location, joins them into a short human-readable line, and returns that line in a list.

**Call relations**: inject calls this while preparing context for a user prompt. It relies on _clip so long provider values do not make the prompt note too large.

*Call graph*: calls 1 internal fn (_clip); called by 1 (inject).


##### `_member_lines`  (lines 288–292)

```
def _member_lines(person: Person | None) -> list[str]
```

**Purpose**: Builds a compact one-line description of the speaking member. It focuses on the member’s name and job title, if known.

**Data flow**: It receives an optional person. If there is no person or no name/title, it returns an empty list. Otherwise it clips the known parts, joins them with a separator, and returns a single member line.

**Call relations**: inject calls this alongside _company_lines. Like the company formatter, it uses _clip to keep injected text short and tidy.

*Call graph*: calls 1 internal fn (_clip); called by 1 (inject).


##### `_clip`  (lines 295–297)

```
def _clip(value: str, limit: int) -> str
```

**Purpose**: Shortens a piece of text to a fixed limit and makes it one line. It is a small safety helper that keeps summaries from becoming noisy or too long.

**Data flow**: It receives a string and a character limit. It collapses all whitespace into single spaces. If the result fits, it returns it unchanged; otherwise it cuts it just under the limit and adds an ellipsis.

**Call relations**: _company_lines, _member_lines, and summary call this whenever provider text is shown in a compact place.

*Call graph*: called by 3 (_company_lines, _member_lines, summary).


##### `summary`  (lines 300–312)

```
def summary(profile: Profile) -> str
```

**Purpose**: Creates the short summary shown for one enrichment profile row. It tries to say something useful like a title at a company, and falls back to No match when nothing was found.

**Data flow**: It receives a Profile. It looks for a person job title, company name, person full name, and company industry. It combines the best available pieces into one line, capitalizes the industry label if present, clips the final text, and returns it.

**Call relations**: _row calls this when turning a stored Profile into an object row for listing. The summary is the quick label a reader sees before opening details.

*Call graph*: calls 1 internal fn (_clip); called by 1 (_row).


##### `_row`  (lines 315–338)

```
def _row(profile: Profile) -> ObjectRow
```

**Purpose**: Turns a Profile into the generic object-row format used by the portal and object APIs. It flattens nested person and company data into named fields.

**Data flow**: It receives a Profile. It pulls out email, website, status, source, person fields, and company fields, using null where something is unknown. It builds an ObjectRow with the email as the row name, summary(profile) as the row summary, and the flattened fields as the row data.

**Call relations**: ProfileObjects._page and ProfileObjects._entry call this before returning stored profiles to readers. It is the adapter between enrichment-specific data and the project’s shared object display format.

*Call graph*: calls 1 internal fn (summary); called by 2 (_entry, _page); 1 external calls (__init__).


##### `_require_ext`  (lines 341–344)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Checks that an extension context is present and returns it. The extension context is the object that gives access to the workspace and database transaction tools.

**Data flow**: It receives an optional extension context. If it is missing, it raises a runtime error explaining that enrichment objects were dispatched incorrectly. If present, it returns the context unchanged.

**Call relations**: The website action and several ProfileObjects methods call this before touching storage. It acts like a guardrail so later code does not fail with a confusing missing-context error.

*Call graph*: called by 5 (confirm_website, get, list, member_detail, member_page).


##### `ProfileObjects.list`  (lines 353–356)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists enrichment profile rows for callers who are allowed to read shared workspace data. If the caller lacks that shared access, it returns an empty page.

**Data flow**: It receives a tool context and list query. It checks whether the shared subject is in the caller’s read permissions. If not, it returns an empty object page. If yes, it verifies the extension context and delegates to _page to fetch and format rows.

**Call relations**: This is part of the read-only object store behind PROFILE_OBJECT. It is called when normal object listing asks for enrichment_profile rows.

*Call graph*: calls 2 internal fn (_page, _require_ext); 1 external calls (object_page).


##### `ProfileObjects.member_page`  (lines 358–374)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists profile rows through the member-object path, but only from the main agent lane. This prevents the same workspace-level rows from appearing once for every agent.

**Data flow**: It receives an optional extension context, member information, admin flag, and query. It checks the extension context, asks storage whether the current object agent is the main one, and returns an empty page if not. If it is the main agent, it delegates to _page.

**Call relations**: The core member-object listing flow calls this when it fans out over agents. This method narrows the result so enrichment rows, which belong to the workspace as a whole, appear only once.

*Call graph*: calls 2 internal fn (_page, _require_ext); 3 external calls (object_agent_id, object_page, agent_is_main).


##### `ProfileObjects.get`  (lines 376–380)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[Profile] | None
```

**Purpose**: Fetches one enrichment profile by name for callers with shared read access. The name is the member email address.

**Data flow**: It receives a tool context and row name. It checks shared read permission. If access is missing, it returns nothing. Otherwise it checks the extension context, asks _entry for the stored row, and returns the detail object if found.

**Call relations**: This is the single-row counterpart to ProfileObjects.list. It is called by the object API when someone opens or requests one enrichment_profile row.

*Call graph*: calls 2 internal fn (_entry, _require_ext).


##### `ProfileObjects.member_detail`  (lines 382–397)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[Profile] | None
```

**Purpose**: Fetches one profile row through the member-object path, again only from the main agent lane. This avoids duplicate workspace-level rows.

**Data flow**: It receives an optional extension context, row name, member information, and admin flag. It checks the extension context, asks whether the current object agent is the main one, and returns nothing if not. If it is main, it delegates to _entry to fetch and format the profile.

**Call relations**: The core member-detail flow calls this after narrowing by agent. It mirrors ProfileObjects.member_page but for one row instead of a list.

*Call graph*: calls 2 internal fn (_entry, _require_ext); 2 external calls (object_agent_id, agent_is_main).


##### `ProfileObjects.status`  (lines 399–406)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports no special write or sync status for enrichment profiles. These rows are read-only views of stored lookup results.

**Data flow**: It receives a context, row name, and optional expected generation value. It does not read or change anything and always returns nothing.

**Call relations**: This satisfies the object-store interface. Since enrichment rows are not edited through the object API, there is no status information to provide.


##### `ProfileObjects.apply`  (lines 408–417)

```
async def apply(self, ctx: ToolContext, name: str, spec: Profile, old: Profile | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or edit enrichment profiles directly. This protects the consent-based write path.

**Data flow**: It receives the requested row name, new profile spec, old profile if any, and generation information. Instead of writing anything, it raises VerbNotSupported with an explanation that confirm_website is the only write path.

**Call relations**: The object API calls this for apply-style mutations. This method deliberately blocks that route so all changes go through Enrichment.confirm_website and the scheduled job.

*Call graph*: 1 external calls (__init__).


##### `ProfileObjects.delete`  (lines 419–426)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete enrichment profiles directly. Clearing a confirmed website is the supported way to stop lookup and drop stored data.

**Data flow**: It receives the context, row name, and optional generation value. It does not delete storage. It raises VerbNotSupported with the shared refusal message.

**Call relations**: The object API calls this for delete requests. Like apply, it keeps the data lifecycle tied to the consent action rather than arbitrary object mutations.

*Call graph*: 1 external calls (__init__).


##### `ProfileObjects._page`  (lines 428–431)

```
async def _page(self, ext: ExtensionContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Reads stored enrichment profiles and formats them as a paged object list. It is the common helper behind both normal and member-based listing.

**Data flow**: It receives an extension context and list query. Inside a database transaction, it reads up to the configured maximum number of profile rows. It converts each stored profile with _row, applies the object-page helper using the query, and returns the page.

**Call relations**: ProfileObjects.list and ProfileObjects.member_page call this after doing their access and agent checks. It centralizes the actual database read and row formatting.

*Call graph*: calls 2 internal fn (transaction, _row); called by 2 (list, member_page); 2 external calls (__init__, object_page).


##### `ProfileObjects._entry`  (lines 433–445)

```
async def _entry(self, ext: ExtensionContext, name: str) -> MemberObject[Profile] | None
```

**Purpose**: Reads one stored enrichment profile by email and formats it with both row and detail information. It returns nothing if no such profile exists.

**Data flow**: It receives an extension context and profile name. Inside a database transaction, it looks up a stored profile by email. If none is found, it returns nothing. If found, it creates a MemberObject containing the display row, the full Profile as the detail spec, and timestamps based on when the profile was fetched.

**Call relations**: ProfileObjects.get and ProfileObjects.member_detail call this after their permission or agent checks. It is the single-profile version of _page.

*Call graph*: calls 2 internal fn (transaction, _row); called by 2 (get, member_detail); 3 external calls (__init__, __init__, __init__).


##### `manifest`  (lines 468–510)

```
def manifest() -> Manifest
```

**Purpose**: Builds the extension manifest, which tells the host system what this extension offers. It always exposes read access and prompt injection, and only exposes lookup-producing features when a provider is configured.

**Data flow**: It checks the environment for a provider. If one exists, it creates an Enrichment instance, defines the confirm_website tool, and schedules the enrichment job. If no provider exists, those producer pieces stay absent. It then returns a Manifest containing the extension name, version, deployment key names, tools, read-only object kind, jobs, and prompt hook.

**Call relations**: The host calls this at startup to register the extension. The objects and inject hook are always registered so old stored rows can still be read, while the action and job are registered only when they can actually perform lookups.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, owner_candidates, provider_from_env).


### Imported knowledge sources
Marks and registers the gbrain extension’s GitHub and local-folder knowledge import sources and optional private-repository token.

### `extensions/gbrain/ufo_ext_gbrain/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` is used to say “this folder is a package,” meaning other code can import modules from it using package-style names. Think of it like a label on a drawer: the label does not contain the tools, but it tells the system that the drawer is part of the organized workspace. Because this file is empty, it does not run setup code, expose shortcut imports, or define any public functions or classes. Its value is structural: without it, some Python versions, tools, or packaging setups might not recognize `extensions/gbrain/ufo_ext_gbrain` as an importable package.


### `extensions/gbrain/ufo_ext_gbrain/manifest.py`

`config` · `startup / extension discovery`

This file is the extension’s “label on the box.” When the main system discovers the gbrain extension, it needs to know what new abilities the extension provides. This manifest answers that in one place.

The extension declares a name and version, then lists the object type it contributes: a gbrain source, meaning a saved origin that can be synced. It also declares two source providers. One provider knows how to build a Git-backed reader, which reads Markdown pages from a GitHub repository. The other knows how to build a folder-backed reader, which reads Markdown pages from a local directory. Think of these as two different doors into the same kind of library: one door opens a remote GitHub shelf, the other opens a folder on the machine.

The file also declares a credential slot named for a GitHub token. A credential slot is a named place where the system may store a secret. Public repositories do not need it, but private repositories do. Without this manifest, the host would not know that gbrain exists, what source backends it supports, or what credential it may ask for.

#### Function details

##### `manifest`  (lines 16–38)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the gbrain extension declaration that the host application can read. Someone would use this when loading extensions so the system learns which gbrain object type, source backends, and credentials are available.

**Data flow**: It starts with the extension name and version, then packages together three pieces of information: the gbrain source object kind, two source providers, and one GitHub credential slot. For the Git provider, it supplies a small builder that receives credentials and creates a Git-based gbrain source. For the folder provider, it supplies a builder that ignores credentials and creates a local-folder source. The result is a Manifest object that the wider system can use during setup.

**Call relations**: During extension loading, the host calls this function to ask, “What do you provide?” The function creates SourceProvider entries for the Git and folder backends, creates a CredentialSlot for the optional GitHub token, and wraps everything in a Manifest. That Manifest is then handed back to the host so later sync code can build the right source reader when a gbrain origin is registered.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Messaging surface registrations
Registers the iMessage and Slack communication surfaces, including connection actions, routes, credentials, tools, hooks, and workspace status facts.

### `extensions/imessage/ufo_ext_imessage/manifest.py`

`config` · `startup / extension loading`

Think of this file as the extension’s front desk sign and wiring diagram. When the larger UFO system loads the iMessage extension, it needs a clear answer to three questions: what is this extension called, what can users do with it, and how does the system send or receive messages through it? This file answers those questions by building a Manifest, which is a package of registration information for the host platform.

It names the extension “imessage” and gives it a version. It creates an ImessageSurface, which is the part that can listen for incoming iMessages, post outgoing messages, attach content, and speak through the messaging surface. It also creates an ImessageConnect tool, which lets a workspace member prove they control an iMessage phone number by texting a code to an assigned line.

The manifest marks the connect tool as untrusted and side-effecting. In plain terms, that means user input should not be blindly trusted, and running the tool changes the outside world rather than only calculating an answer. Finally, it lists required deployment keys for the Spectrum cloud provider, so the platform knows which environment secrets must exist before this extension can work.

#### Function details

##### `manifest`  (lines 21–55)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the complete registration record for the iMessage extension. The host platform uses this record to discover the extension’s name, tool, messaging surface, and required cloud credentials.

**Data flow**: It starts with fixed module values such as the extension name, version, surface name, action name, and environment variable names. It creates an iMessage surface and an iMessage connection tool, both using the shared line provider. It then packages those pieces into a Manifest object, including the tool definition, surface definition, and required deploy keys, and returns that finished manifest to the caller.

**Call relations**: This is the file’s single assembly point. When the extension is loaded, this function constructs the pieces the platform needs: it creates the iMessage surface, creates the connect action, wraps the action in a ToolDef, binds that tool to the iMessage surface, describes the surface with a SurfaceSpec, and finally hands the completed Manifest back to the UFO platform.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).


### `extensions/slack/ufo_ext_slack/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells Python that the folder should be treated as an importable package. You can think of it like putting a label on a drawer: even if the label has no extra instructions written on it, it still tells the system that the drawer belongs to the project and can be opened by name.

For this Slack extension, the file makes it possible for code elsewhere to import modules under `extensions/slack/ufo_ext_slack`. Without it, some Python setups or packaging tools might not recognize the folder as part of the extension package, which could make imports fail or make the extension harder to discover.

Because the file is empty, it has no behavior of its own. It does not connect to Slack, configure anything, register commands, or expose public names. Its importance is structural: it helps define the package boundary for the Slack-related extension code.


### `extensions/slack/ufo_ext_slack/manifest.py`

`config` · `startup and workspace status checks`

A manifest is like a plug’s shape and label: it lets the main app know how to connect to this extension without needing to know all of Slack’s inner details. This file declares that Slack can receive incoming events, handle interactive button or form actions, complete OAuth installation, send replies, attach to conversations, and identify which workspace a request belongs to.

It also declares two private credential slots for workspaces that bring their own Slack app: a bot token and a signing secret. For the preferred OAuth install path, the shared Slack app secrets come from the deployment environment instead, so they are not listed here as per-workspace credentials.

The file wires in Slack-specific tools and hooks. Hooks are callbacks that run at important moments, such as before a tool is used, when a user submits a prompt, or when a connection has been recorded. In practical terms, these make Slack message sending attributable, restart Slack thread-following work for each turn, and update install buttons once setup succeeds.

Finally, it declares a workspace fact: a short line the core can show only when Slack is truly live for that workspace. This matters because a saved installation record alone does not prove Slack messages are actually reaching this deployment.

#### Function details

##### `_slack_answers`  (lines 73–79)

```
async def _slack_answers(ext: ExtensionContext) -> bool
```

**Purpose**: Checks whether Slack is not just partly installed, but actually working for this workspace. It prevents the system from claiming Slack is available when setup is only half-finished.

**Data flow**: It receives an extension context, which gives access to installation records and Slack connection checks. First it looks for a Slack installation record for the workspace; if none exists, it returns false. If a record exists, it asks `install_is_live` whether Slack can really reach this deployment, and returns that answer.

**Call relations**: This function is handed to the workspace fact declared in `manifest`, so the core can call it when deciding whether to show the Slack-installed status line. Its key handoff is to `ufo_ext_slack.surface.install_is_live(ext)`, which performs the deeper Slack-specific liveliness check after the basic installation record is found.

*Call graph*: 1 external calls (install_is_live).


##### `manifest`  (lines 82–127)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the complete Slack extension manifest. This is the single object the core system uses to learn what the Slack extension can do and how to call into it.

**Data flow**: It starts from constants and imported Slack handlers, then assembles a `Manifest` object. Into that object it places credential definitions, HTTP routes, send and attach functions, workspace identity functions, tools, event hooks, a setup skill directory, and the Slack workspace fact. The result is a ready-to-register description of the whole Slack extension.

**Call relations**: The extension loader calls this function when it needs to register Slack with the core system. Inside, it creates `CredentialSlot`, `SurfaceRoute`, `SurfaceSpec`, `HookSpec`, `SkillSpec`, `WorkspaceFact`, and finally `Manifest` objects, fitting them together so the core knows which Slack functions to invoke for incoming web requests, outgoing messages, setup flows, hooks, and workspace status checks.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).
