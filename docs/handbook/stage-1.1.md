# Pack selection and deployment capability bundles  `stage-1.1`

This stage is part of startup. Before the assistant begins work, the system chooses a named “pack,” which is like a prepacked toolbox for a certain setting. The extension store is the gatekeeper: it can search the catalog of available extensions, install one by recording it in a lockfile, and remove it later. That turns possible capabilities into the exact ones this deployment will load.

The pack files are the ready-made toolboxes. The local assistant pack enables the normal full assistant on local infrastructure. The billing pack adds billing setup so developers can test it locally. The hosted assistant pack selects features meant for managed cloud workspaces. The evaluation pack starts from the assistant tools, removes real broker connections, and adds fake test tools plus Docker support. The chief-of-staff pack focuses on Slack-driven work with meetings, notes, todos, people files, and logs. DSQA and GDPVal packs provide different evaluation capability sets. The sample pack is a small end-to-end proof that extensions, skills, and onboarding steps can be bundled correctly.

## Files in this stage

### Extension Store Foundation
The extension store provides the mechanism for discovering, installing, and removing the extensions that packs later select.

### `core/src/ufo/ext/store.py`

`domain_logic` · `extension command handling`

This file supports commands like `ufoctl ext` that work with extensions. An extension catalog is like a shop shelf: it lists extensions that are available by name and version. The lockfile is like a receipt or packing list: it records exactly which extensions are pinned for the system to load, including a digest, which is a fingerprint of the installed package contents. That fingerprint matters because it makes the chosen extension version precise and repeatable.

The file defines small data shapes for catalog entries and search results, then provides an `ExtensionStore` object that works over one catalog and one lockfile. Searching checks the catalog and marks which matching entries are already pinned. Installing first confirms the extension is listed in the catalog, refuses entries marked `disabled` because those are only meant for bundle creation, then checks that the Python package is actually installed in the current environment before writing a pin. Removing does the opposite: it checks the lockfile, errors if the extension is not pinned, and writes a new lockfile without it.

A key detail is that the store does not download or install Python packages itself. It only records installed extensions in the lockfile. Without this file, the project would lack the safe, consistent step that turns available extensions into the exact extension set the loader should boot with.

#### Function details

##### `read_catalog`  (lines 48–49)

```
def read_catalog(path: Path) -> Catalog
```

**Purpose**: Reads an extension catalog file from disk and turns it into a checked `Catalog` object. Someone would use this before searching or installing, so the store knows what extensions are available.

**Data flow**: It receives a file path. It reads the text at that path, parses the text as TOML, which is a human-readable configuration format, and validates the result against the catalog shape. It returns a `Catalog` containing the listed extensions.

**Call relations**: This is the doorway from a catalog file into the in-memory store. It relies on the path object to read the file and on TOML parsing to understand the text; after that, other code can create an `ExtensionStore` with the returned catalog.

*Call graph*: 2 external calls (read_text, loads).


##### `ufo_version`  (lines 52–53)

```
def ufo_version() -> str
```

**Purpose**: Finds the installed version of the `ufo` package. The lockfile uses this as an anchor so the pinned extensions are tied to the UFO version that created or owns the lockfile.

**Data flow**: It takes no project data as input. It asks Python package metadata for the version of the installed `ufo` package and returns that version string.

**Call relations**: This helper is used by `ExtensionStore._write` when a lockfile does not already exist. In that case, `_write` needs a UFO version to put into the new lockfile before saving it.

*Call graph*: called by 1 (_write); 1 external calls (version).


##### `pin_for`  (lines 56–63)

```
def pin_for(name: str) -> ExtensionPin
```

**Purpose**: Builds the exact lockfile pin for an extension that is already installed in the current Python environment. It prevents the store from pinning a name that exists in the catalog but is not actually available to run.

**Data flow**: It receives an extension name. It asks the extension loader what extensions have been discovered in the current environment. If the name is missing, it raises an error. If found, it takes the extension's declared version and calculates a digest, meaning a content fingerprint, from the installed package entry. It returns an `ExtensionPin` with the name, version, and digest.

**Call relations**: `ExtensionStore.install` calls this after checking that the catalog allows the extension to be installed. `pin_for` then hands back the precise pin that `install` writes into the lockfile.

*Call graph*: called by 1 (install); 3 external calls (__init__, discovered, extension_digest).


##### `ExtensionStore.search`  (lines 73–84)

```
def search(self, query: str) -> tuple[StoreListing, ...]
```

**Purpose**: Searches the catalog for extension names containing a query string and reports whether each match is already pinned in the lockfile. This is what lets a user see both availability and current install state in one result.

**Data flow**: It receives a search string. It reads the current pins from the lockfile through `_pins`, collects the pinned names, then scans the catalog entries. For entries whose names contain the query, it creates `StoreListing` results with the catalog name, version, disabled flag, and an installed true-or-false value. It returns all matching listings as a tuple.

**Call relations**: This is a read-only path through the store. It calls `_pins` to learn what is already selected, combines that with the catalog, and returns display-friendly search results without changing the lockfile.

*Call graph*: calls 1 internal fn (_pins); 1 external calls (__init__).


##### `ExtensionStore.install`  (lines 86–96)

```
def install(self, name: str) -> ExtensionPin
```

**Purpose**: Pins one catalog extension into the lockfile so the loader can use it later. It also protects users from installing names that are not in the catalog, bundle-only entries, or packages missing from the current environment.

**Data flow**: It receives an extension name. It looks for that name in the catalog. If there is no entry, it raises an error. If the entry is disabled, it raises an error explaining that it is bundle-only. Otherwise it asks `pin_for` to create a precise pin from the installed package. It reads the existing pins, replaces any old pin with the same name, writes the updated set, and returns the new pin.

**Call relations**: This is the main write path for adding an extension. It calls `pin_for` to prove the extension is installed and get its fingerprint, uses `_pins` to preserve the rest of the lockfile, then hands the new pin set to `_write` to save it.

*Call graph*: calls 3 internal fn (_pins, _write, pin_for).


##### `ExtensionStore.remove`  (lines 98–102)

```
def remove(self, name: str) -> None
```

**Purpose**: Removes an extension pin from the lockfile. This tells the loader that the extension should no longer be part of the selected extension set.

**Data flow**: It receives an extension name. It reads the current pins from the lockfile. If none of them match the name, it raises an error because there is nothing installed to remove. Otherwise it builds a new pin list without that name and writes it back to the lockfile. It returns nothing.

**Call relations**: This is the opposite of `ExtensionStore.install`. It uses `_pins` to inspect the current lockfile and `_write` to save the reduced pin list.

*Call graph*: calls 2 internal fn (_pins, _write).


##### `ExtensionStore._pins`  (lines 104–105)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: Reads the current extension pins from the lockfile, or returns an empty set of pins if there is no lockfile yet. It gives the rest of the store one simple way to ask, “what is currently selected?”

**Data flow**: It uses the store's lockfile path. If the file exists, it reads and parses the lockfile, then returns its extension pins. If the file does not exist, it returns an empty tuple.

**Call relations**: `search`, `install`, and `remove` all call this before deciding what to show or change. It hides the difference between “no lockfile yet” and “a lockfile with no extensions,” so those higher-level operations can stay simpler.

*Call graph*: called by 3 (install, remove, search); 1 external calls (read_lockfile).


##### `ExtensionStore._write`  (lines 107–111)

```
def _write(self, pins: tuple[ExtensionPin, ...]) -> None
```

**Purpose**: Writes a complete lockfile with a supplied set of extension pins. It preserves the existing lockfile's UFO version when possible, and creates a new version anchor when writing the first lockfile.

**Data flow**: It receives the full tuple of pins that should be saved. It checks whether the lockfile already exists. If it does, it reads the existing UFO version from it; if not, it asks `ufo_version` for the currently installed UFO version. It builds a new `Lockfile` object with that version and the supplied pins, then writes it to disk.

**Call relations**: `install` and `remove` call this after they have decided the new desired pin set. `_write` is the final saving step: it gathers the lockfile version anchor, creates the lockfile data, and hands it to the loader's lockfile writer.

*Call graph*: calls 1 internal fn (ufo_version); called by 2 (install, remove); 3 external calls (__init__, read_lockfile, write_lockfile).


### Assistant Deployment Packs
These packs define local, billing-enabled, hosted, and evaluation-focused assistant capability bundles.

### `packs/assistant_dev/ufo_pack_assistant.py`

`config` · `startup/config load`

A “pack” is a convenient preset. Instead of asking someone to enable dozens of features one by one, this file gives that whole set a single name: “assistant”. When this pack is activated, the system knows exactly which extensions belong in this assistant setup.

The file does not implement the features itself. It does not contain the web search engine, memory store, browser tools, connector code, document creation, coding helper, or debugger. Instead, it names those extension packages so the larger system can load them. An everyday analogy is a recipe card: the card does not grow vegetables or bake bread, but it lists the ingredients needed to make the meal.

The important detail is that this pack is for a locally carried assistant. It includes memory, search, connectors, sandbox browser support, code execution, document tools, scheduled tasks, user-created skills, the web portal, debugging tools, and model/provider support. It also includes index and embedding extensions used for local recall. Because the pack only lists extensions, each extension remains responsible for its own tools, setup instructions, and behavior.

#### Function details

##### `pack`  (lines 52–53)

```
def pack() -> Pack
```

**Purpose**: Creates the pack definition that tells the system the pack’s name, version, and which extensions to activate. Someone would use this when the system is loading available packs and needs a concrete Pack object for the “assistant” preset.

**Data flow**: The function reads the file’s constants: the pack name, its version, and the tuple of extension names. It passes those values into the Pack constructor, which turns the plain list of settings into a Pack object. The result is returned to the caller; the function does not change files, network state, or other data.

**Call relations**: When the pack-loading part of the system asks this module for its pack, this function is the small handoff point. It delegates the actual object creation to Pack.__init__, giving it the name, version, and extension list so the broader system can later activate that bundle.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_billing/ufo_pack_assistant_billing.py`

`config` · `config load`

A “pack” here is a named bundle of system extensions that can be switched on together. The regular local assistant pack is meant for day-to-day development, so it does not include Metronome, the external billing and usage-metering service. The hosted assistant pack does include Metronome, but it also brings in other hosted-only services that are not useful on a laptop. This file fills the gap between those two choices.

It creates an `assistant_billing` pack by taking all the extensions from the normal assistant pack and adding one more extension: `metronome`. That makes it possible to test the hosted onboarding action called “Set up billing” in a local environment. Without this pack, a developer could see that billing action in the product flow, but a local deployment would not have the billing service available to complete it.

This pack is intentionally opt-in. Turning it on also enables billing-related jobs, such as activating billing and shipping usage or seat-count information. Those jobs need real credentials for Metronome and Stripe, so the file’s comments warn developers to use sandbox or test-mode keys. In everyday terms, this file is like a special “local rehearsal” switch: it lets the team practice the billing path safely before using the real stage.

#### Function details

##### `pack`  (lines 25–26)

```
def pack() -> Pack
```

**Purpose**: This function builds and returns the pack definition for `assistant_billing`. It is used when the system asks this file what extensions should be enabled for the local assistant-with-billing setup.

**Data flow**: It reads the fixed pack name, version, and extension list defined in this file. It then creates a `Pack` object containing that information, including all normal assistant extensions plus `metronome`, and returns that object to the caller.

**Call relations**: When the pack system loads this file, it calls `pack` to get the bundle description. Inside, `pack` hands the name, version, and extensions to `Pack.__init__`, which turns those plain values into the structured pack object the rest of the system can use.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_hosted/ufo_pack_assistant_hosted.py`

`config` · `config load / startup`

This file is like a packing list for the hosted version of the assistant. It does not implement Slack, memory, browser automation, billing, or research itself. Instead, it names all the pieces that should be switched on together when a workspace chooses the hosted assistant pack.

The pack combines many assistant abilities: memory and recall, web research, browser tools, document generation, scheduled tasks, coding help, connectors to outside services, Slack support, usage metering, and more. The important hosted-specific idea is that some heavy infrastructure is supplied by managed backends. For example, memory indexing uses Turbopuffer rather than a local index, live frames use Redis, browser sessions use Browserbase-hosted Chrome, and code sandboxes use E2B.

It also includes one shipped skill, `customer-onboarding-help`. A skill here is a packaged set of assistant knowledge or behavior. This one gives the hosted assistant a curated, read-only source of onboarding facts, so it can answer common workspace setup questions without depending on a customer’s own memory store.

Without this file, the system would not know what “assistant_hosted” means. The hosted deployment would have no single, declarative recipe for which capabilities, providers, and bundled skills belong together.

#### Function details

##### `pack`  (lines 66–72)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the pack description for the hosted assistant. The system uses this description to learn the pack’s name, version, enabled extensions, and bundled skill folders.

**Data flow**: It starts with constants in this file: the pack name, version, extension names, the skills directory, and the skill names. It turns each skill name into a `SkillSpec`, which points at that skill’s folder on disk. It then puts everything into a `Pack` object and returns it to whoever is loading packs.

**Call relations**: When the pack-loading part of the system asks this module for its pack definition, `pack` creates the final manifest object. To do that, it calls `SkillSpec.__init__` for each bundled skill path, then calls `Pack.__init__` to assemble the full hosted-assistant recipe.

*Call graph*: 2 external calls (__init__, __init__).


### `packs/assistant_eval/ufo_pack_assistant_eval.py`

`config` · `startup / pack selection`

This file is a small but important safety and setup switch for running assistant evaluations. In normal product use, the assistant may know about real external tool brokers such as Composio or Pipedream. In an evaluation run, those real services are not available and should not be advertised as usable tools. If they stayed in the pack, the assistant could waste time trying to use decoy integrations instead of using the controlled test environment.

The file builds an `assistant_eval` pack by reusing the regular assistant pack, but filtering out the real broker extensions named `composio` and `pipedream`. It then adds two evaluation-specific extensions: `eval_env`, which provides deterministic fake services like email, calendar, and code search, and `docker`, which lets the sandbox run each conversation workspace as a real mounted directory. In plain terms, this file swaps the assistant from “real-world tool mode” into “test lab mode.”

The main idea is separation. Fake evaluation providers should not be included in the product pack, because registered tools can show up even before permission checks. Likewise, real broker providers should not be included in the evaluation pack, because they do not have real keys there and would only confuse the run.

#### Function details

##### `pack`  (lines 26–27)

```
def pack() -> Pack
```

**Purpose**: This function creates the pack description that the system can load when an evaluation deploy selects the `assistant_eval` pack. It names the pack, gives it a version, and lists the extensions that should be active.

**Data flow**: It reads the constants in this file: the pack name, version, and prepared extension list. It passes those values into `Pack`, which creates a manifest-like object describing what this pack contains. The result is returned to the caller so the larger system can register or load the pack.

**Call relations**: When the pack system asks this module what it provides, `pack` is the function that answers. It hands the final name, version, and filtered extension list to `Pack.__init__`, which turns them into the standard pack object used by the rest of the system.

*Call graph*: 1 external calls (__init__).


### Role-Specific Workspace Pack
The chief-of-staff pack bundles Slack-centered management tools, skills, and workspace workflows.

### `packs/chief_of_staff/ufo_pack_chief_of_staff.py`

`config` · `startup / pack discovery`

This file is like the label and packing list on a toolbox. It does not perform the chief-of-staff work itself. Instead, it tells the platform how to assemble that working environment.

The pack is meant to support a manager who wants one Slack-based front door for many daily activities: syncing meeting notes and Slack activity into memory, preparing for 1:1 meetings, triaging observations into follow-up work, and improving the workflow over time. To make that possible, the file names the outside capabilities the pack depends on, such as connectors for external services, Slack, scheduled tasks, todos, memory, search indexing, and self-improvement tools.

It also points to four skill folders: setup, sync, prep, and triage. A “skill” here is a packaged workflow the system can run or expose to the user. The setup skill is deliberately conversational, meaning the user grants access and configures things through chat rather than through hidden code.

Without this file, the system would not know that this pack exists, which extensions to enable for it, or which skill directories belong to it. The actual workflows might be present on disk, but they would not be advertised as one coherent chief-of-staff package.

#### Function details

##### `pack`  (lines 40–46)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the pack definition that the UFO system can load. It gathers the pack name, version, required extensions, and skill folder locations into one object.

**Data flow**: It starts with constants defined in this file: the pack name, version, extension names, the skills directory, and the list of skill names. For each skill name, it creates a SkillSpec that points to that skill’s folder. It then puts all of that into a Pack object and returns it to the caller.

**Call relations**: When the larger system wants to discover or load this pack, it calls this function to get the pack’s manifest. Inside, the function hands each skill path to SkillSpec so the platform knows where each workflow lives, then hands the complete collection of metadata to Pack so the platform can treat it as one installable bundle.

*Call graph*: 2 external calls (__init__, __init__).


### Evaluation Capability Packs
The DSQA and GDPVal packs provide named evaluation bundles with different project and workflow extensions enabled.

### `packs/dsqa_eval/ufo_pack_dsqa_eval.py`

`config` · `pack discovery and setup`

This file is like a menu of tool bundles for a DSQA evaluation setup. DSQA likely needs different levels of capability depending on the task: a basic core setup, a search-enabled setup, and a browser-enabled setup. Rather than repeating those extension lists in many places, this file names them once and exposes three simple functions that build the matching Pack objects.

The shared version number is set at the top, so all three packs stay in sync. The basic pack includes indexing, OpenAI-style embedding, and OpenRouter access. The search pack adds research and Exa search support on top of that. The browser pack adds browser automation and a Chrome sandbox on top of the search pack. This layering matters because it makes the relationship between the packs easy to see: each larger pack is the smaller one plus extra abilities.

When another part of the system wants one of these bundles, it calls the matching function. That function creates a Pack, which is a manifest object describing the pack name, version, and enabled extensions. Without this file, the system would not have these named DSQA evaluation bundles in one clear place, and setup code would have to know the exact extension combinations itself.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Builds the smallest DSQA evaluation pack. This is useful when the system needs only the core abilities: default indexing, embeddings, and OpenRouter access.

**Data flow**: It starts with the fixed core pack name, shared version number, and core extension list defined in the file. It passes those values into the Pack constructor, which turns them into a Pack object. The result is a ready-to-use manifest for the core DSQA setup.

**Call relations**: When setup or pack discovery asks for the core DSQA bundle, this function is the small factory that creates it. Its only handoff is to Pack.__init__, which receives the name, version, and extension list and builds the manifest object.

*Call graph*: 1 external calls (__init__).


##### `search_pack`  (lines 17–18)

```
def search_pack() -> Pack
```

**Purpose**: Builds the DSQA pack that includes search and research features. Someone would use this when evaluation work needs to look things up beyond the basic indexed and embedded data.

**Data flow**: It takes the predefined search pack name, shared version, and search extension list. That list includes the base extensions plus Exa and research support. It sends these values into the Pack constructor and returns the resulting Pack object.

**Call relations**: When the system needs the search-capable DSQA bundle, this function creates the manifest for it. It delegates the actual Pack object creation to Pack.__init__, giving it the chosen name, version, and extensions.

*Call graph*: 1 external calls (__init__).


##### `browser_pack`  (lines 21–22)

```
def browser_pack() -> Pack
```

**Purpose**: Builds the most capable DSQA evaluation pack, including browser automation. This is useful for tasks that need search plus the ability to open and interact with web pages in a controlled browser environment.

**Data flow**: It uses the predefined browser pack name, shared version number, and browser extension list. That list builds on the search pack and adds browser and sandboxed Chrome support. It passes everything to the Pack constructor and returns the completed Pack object.

**Call relations**: When setup or pack discovery needs the browser-enabled DSQA bundle, this function supplies it. It hands the pack details to Pack.__init__, which packages them into the manifest object used by the rest of the system.

*Call graph*: 1 external calls (__init__).


### `packs/gdpval_eval/ufo_pack_gdpval_eval.py`

`config` · `config load / pack discovery`

This file is like a menu for GDPVal evaluation setups. Instead of making users remember many individual extension names, it offers four ready-made bundles: a core bundle, a document-focused bundle, a research-focused bundle, and a full bundle with everything included.

A “Pack” is a manifest object: a simple description of a package the system can load. Each Pack has a name, a version, and a list of extensions. Extensions are optional capabilities, such as indexing, embeddings, document tools, browser tools, or research tools. The shared base extensions are included in every pack, so all GDPVal setups start with the same foundation. The more specialized packs add document-related extensions, research-related extensions, or both.

Without this file, someone setting up GDPVal evaluation would need to assemble these capability lists by hand, which would be easy to get wrong or make inconsistent across runs. This file keeps those combinations explicit, repeatable, and named. The functions do not run the evaluation themselves; they create small Pack objects that other parts of the system can discover and load when they need a particular setup.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the smallest GDPVal pack. It includes only the shared base capabilities needed across all GDPVal evaluation setups.

**Data flow**: It starts with the fixed core pack name, the shared version string, and the base extension list. It puts those into a new Pack object and returns that object to whoever is asking what the core setup should contain.

**Call relations**: When the pack system asks for the core GDPVal setup, this function builds a Pack by calling Pack.__init__. It does not call any other project logic; it simply hands back the manifest that says, “load these base extensions.”

*Call graph*: 1 external calls (__init__).


##### `documents_pack`  (lines 17–22)

```
def documents_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack for document-oriented work. It includes the base capabilities plus tools for documents, an interactive Python-style workspace, and coding support.

**Data flow**: It reads the fixed document pack name, the shared version, the base extensions, and the document extension list. It combines the two extension lists, places them into a Pack object, and returns the finished manifest.

**Call relations**: When a document-capable GDPVal setup is needed, this function is the recipe used to build it. It calls Pack.__init__ with the combined extension list, then hands the resulting Pack back to the pack-loading machinery.

*Call graph*: 1 external calls (__init__).


##### `research_pack`  (lines 25–30)

```
def research_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack for research-oriented work. It includes the base capabilities plus tools for web research, browsing, and browser-based sandbox work.

**Data flow**: It takes the fixed research pack name, the shared version, the base extensions, and the research extension list. It joins the base and research extensions into one ordered bundle, creates a Pack object from them, and returns it.

**Call relations**: When the system or a user wants the research version of the GDPVal setup, this function provides the manifest. Its only handoff is to Pack.__init__, which turns the name, version, and extension list into a Pack object.

*Call graph*: 1 external calls (__init__).


##### `full_pack`  (lines 33–38)

```
def full_pack() -> Pack
```

**Purpose**: Creates the most complete GDPVal pack. It includes the base capabilities, the document tools, and the research tools all together.

**Data flow**: It starts with the fixed full pack name and version, then combines the base, document, and research extension lists. It passes that complete list into a new Pack object and returns the resulting all-in-one manifest.

**Call relations**: When the fullest GDPVal environment is requested, this function builds that configuration. It calls Pack.__init__ to create the Pack, then returns it so the broader pack system can load every listed extension.

*Call graph*: 1 external calls (__init__).


### Sample Pack
The sample pack demonstrates the pack system end to end with a small extension, skill, and onboarding bundle.

### `packs/sample_pack/ufo_pack_sample.py`

`config` · `pack discovery and onboarding`

This is a conformance sample: a tiny but real pack that exercises the same public path a normal installed pack would use. In everyday terms, it is like a test plug-in that must fit through the same doorway as every real plug-in. If this file stops working, it suggests the pack boundary has been broken.

The file names the pack, gives it a version, points to one bundled extension called "sample", and points to a skill folder on disk. It also defines an onboarding step. An onboarding step is a setup action that runs when the pack is activated, such as recording that the pack has been initialized.

The important behavior is that onboarding writes to the pack's scoped store through the public `ExtensionContext`. That store is durable project storage, not just a fake log message. This means tests can later read back the same value through the same public surface the real system uses. The `pack()` function is the entry point the pack loader looks for: it returns a `Pack` object describing everything this pack contributes.

#### Function details

##### `_setup`  (lines 25–26)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: This is the pack's onboarding action. It records a small marker saying the sample pack has been onboarded, so the system can prove that pack setup ran and wrote to real storage.

**Data flow**: It receives an `ExtensionContext`, which is the pack's access point to shared services such as its scoped store. It writes the key `pack:onboarded` with the value `{"pack_onboarded": true}` into that store. It returns nothing, but it changes durable stored state.

**Call relations**: This function is not called directly in this file. Instead, `pack()` places it inside an `OnboardingStep`, and the pack system calls it later when that onboarding step runs.


##### `pack`  (lines 29–36)

```
def pack() -> Pack
```

**Purpose**: This is the public entry point for the pack loader. It builds and returns the description of the sample pack: its name, version, bundled extension, skill folder, and onboarding step.

**Data flow**: It reads constants from this file, such as the pack name, version, bundled extension name, skill path, and onboarding name. It wraps the skill path in a `SkillSpec`, wraps `_setup` in an `OnboardingStep`, and returns a `Pack` object containing all of that information.

**Call relations**: When the system discovers or activates this pack, it calls `pack()` to learn what the pack contributes. Inside that construction, it calls `SkillSpec.__init__` to describe the skill, `OnboardingStep.__init__` to describe the setup action, and `Pack.__init__` to bundle the whole pack description together.

*Call graph*: 3 external calls (__init__, __init__, __init__).
