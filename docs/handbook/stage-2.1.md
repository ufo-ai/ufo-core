# Pack manifest selection  `stage-2.1`

Pack manifest selection is behind-the-scenes setup work. Before the system can run an assistant, an evaluation, or a specialized workspace, it must know which “pack” to load. A pack is like a recipe card: it names the extensions, skills, onboarding steps, and infrastructure choices that should be turned on together.

The local assistant pack is the full developer recipe for the normal assistant experience. The hosted assistant pack selects similar assistant features but points them toward managed cloud services. The billing pack lets developers test the hosted billing flow locally. The assistant evaluation pack replaces real outside integrations with fake ones so tests are repeatable.

Other manifests define specialized bundles. The chief of staff pack loads manager-support skills. The DSQA and GDPVal evaluation packs offer several preset tool combinations, from small core setups to fuller search, browser, document, or research setups. The sample pack is a simple proof that extensions, skills, and onboarding actions can be bundled correctly. The YC package marker makes the YC pack importable, while its manifest describes the founder-focused extensions and skill folders.

## Files in this stage

### Assistant pack variants
Bundles that select assistant capabilities for local billing development, full local use, deterministic evaluation, and hosted deployment.

### `packs/assistant_billing/ufo_pack_assistant_billing.py`

`config` · `config load`

This file is a small configuration file for choosing what parts of the system should run together. A “pack” is like a named bundle of features and services. The normal local assistant pack does not include Metronome, the external billing and usage-tracking service, because most local development should not send billing data anywhere. The hosted assistant pack does include Metronome, but it also includes other hosted-only services that are not useful on a laptop.

This file fills that gap. It creates an `assistant_billing` pack by taking everything from the regular assistant pack and adding the `metronome` extension. That makes it possible to test billing-related features locally, such as the owner action to “Set up billing,” billing activation jobs, and shipping usage or seat information.

Because this pack can talk to real billing infrastructure, it is deliberately opt-in. A developer must choose it explicitly, usually through configuration such as `UFO_DEV_PACK`. The comments warn that it should be pointed at safe test credentials, such as a Metronome sandbox token and a Stripe test-mode key. Without this file, local development would have no clean middle ground for testing assistant billing: either billing would be missing, or the developer would have to run an unnecessarily large hosted-style setup.

#### Function details

##### `pack`  (lines 25–26)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the pack definition for the local assistant-with-billing setup. Someone uses this when the system needs to know the pack name, version, and which extensions to enable.

**Data flow**: It reads the file-level constants: the pack name `assistant_billing`, the version `0.1.0`, and the extension list made from the normal assistant extensions plus `metronome`. It puts those values into a `Pack` object, which becomes the structured description of this bundle.

**Call relations**: When the pack-loading system asks this module for its pack, `pack` creates the `Pack` object. It hands the name, version, and extension list to `Pack.__init__`, so the broader system can turn on the assistant features together with billing support.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_dev/ufo_pack_assistant.py`

`config` · `config load`

This file answers a simple question: when the system is told to run the “assistant” pack, which abilities should come with it? A pack is like a pre-packed toolbox. Instead of asking a user to enable memory, web research, browser tools, code execution, documents, scheduled tasks, connectors, debugging, and other pieces one by one, this file groups them under one name: “assistant”.

The file does not implement those features itself. It does not contain the memory system, the browser, the research tools, or the coding agent. Instead, it lists the extension names that should be activated together. Each extension brings its own tools, setup, and instructions from its own manifest. This pack is only the bundle label and the bundle contents.

The comments explain an important distinction: this assistant pack uses the project’s own local carrier and local index, rather than a managed hosted setup. That makes it different from a hosted assistant pack. Without this file, there would be no single “assistant” pack name that reliably starts this exact local combination of capabilities.

#### Function details

##### `pack`  (lines 47–48)

```
def pack() -> Pack
```

**Purpose**: Creates and returns the pack definition for the local assistant bundle. The system uses this when it needs to know the pack’s name, version, and which extensions to activate.

**Data flow**: It reads the fixed values in this file: the pack name, version, and extension list. It passes those values into a Pack object, which becomes the structured description of the assistant pack. The result is that callers receive one ready-to-use pack definition, and nothing else in the file is changed.

**Call relations**: When the pack system loads this file, it calls `pack` to get the assistant pack description. `pack` hands the name, version, and extension list to `Pack.__init__`, which builds the object the wider system can use to activate the listed capabilities together.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_eval/ufo_pack_assistant_eval.py`

`config` · `startup`

This pack is a safe, controlled version of the normal assistant tool bundle. In the real product, the assistant may know about external service brokers such as Composio or Pipedream, which connect to outside apps. In an evaluation run, those real services are not available and should not appear as tempting but unusable tools. If they did, the agent might waste steps trying to use them instead of working inside the intended test world.

The file starts from the regular assistant pack, then removes the real broker extensions named "composio" and "pipedream". After that, it adds one special extension called "eval_env". That evaluation environment provides fake but predictable connectors, such as fake email, calendar, and code-search tools. This makes tests repeatable: the same inputs should lead to the same tool world every time.

An everyday analogy is a driving test simulator. You want the dashboard and roads to feel real enough to test the driver, but you do not want the car actually entering live traffic. This file chooses the simulator version of the assistant’s tools.

#### Function details

##### `pack`  (lines 23–24)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the manifest for the evaluation assistant pack. The manifest tells the system this pack’s name, version, and which extensions should be available.

**Data flow**: It uses the fixed pack name "assistant_eval", version "0.1.0", and the prepared extension list that excludes real broker integrations and includes "eval_env". It passes those values into `Pack`, which produces the pack description object returned to the caller.

**Call relations**: When the pack-loading system asks this module what it provides, this function creates the `Pack` object. It hands the chosen name, version, and extension list to `Pack.__init__`, so the wider system can load the fake evaluation tools instead of the real broker-backed ones.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_hosted/ufo_pack_assistant_hosted.py`

`config` · `startup / config load`

A “pack” is like a preset recipe for starting the assistant with a specific set of abilities. This file is the recipe for the hosted version of the assistant. If a workspace chooses the pack named “assistant_hosted”, the system uses this file to know which tools and services to load.

The pack includes many assistant capabilities: memory and recall, research tools, browser control, document generation, scheduled tasks, code execution, Slack integration, connector support, model providers, usage metering, and more. The important distinction is that several parts are backed by managed services. For example, it uses Turbopuffer for the memory index, Redis for live frame sharing, E2B for sandboxed execution, and Browserbase for hosted browser sessions. In plain terms, instead of expecting every workspace to run all of this machinery itself, the hosted pack points the assistant toward shared, operated infrastructure.

The file also attaches one shipped skill, “customer-onboarding-help”. A skill is a packaged source of instructions or knowledge the assistant can use. This one gives the hosted assistant curated onboarding facts, such as signup, invitations, Slack installation, billing, and limits on what must not be disclosed. Without this file, selecting the hosted assistant pack would not reliably turn on the correct feature bundle or its built-in onboarding knowledge.

#### Function details

##### `pack`  (lines 63–69)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the pack definition for the hosted assistant. The rest of the system uses this returned object to know the pack name, version, enabled extensions, and included skill folders.

**Data flow**: It starts with the constants in this file: the pack name, version, extension list, skills directory, and skill names. It turns each skill name into a SkillSpec, which points at that skill’s folder on disk, then places those skill specs alongside the extension list inside a Pack object. The result is a complete pack description that can be loaded by the system.

**Call relations**: When the pack system asks this module for its definition, this function creates the Pack. During that creation it calls SkillSpec.__init__ to describe the bundled skill path, then Pack.__init__ to assemble the final hosted assistant recipe that the loader can activate.

*Call graph*: 2 external calls (__init__, __init__).


### Workspace and benchmark packs
Domain-specific manifests for manager-support workspaces and DSQA or GDPVal evaluation configurations.

### `packs/chief_of_staff/ufo_pack_chief_of_staff.py`

`config` · `startup / pack discovery`

This file is the pack’s manifest, which is like a label and contents list on a box. It does not run the chief-of-staff workflows itself. Instead, it tells the platform how to assemble them.

The pack is meant to support a manager through Slack, meeting notes, memory, scheduled reviews, todos, and guided self-improvement. To make that possible, the file names the extensions the pack depends on. These include connector access, source syncing, memory and search indexing, Slack, scheduled tasks, todo tracking, skill creation, and improvement loops. Without this manifest, the system would not know which building blocks to turn on for this pack.

It also points to four skill folders: setup, sync, prep, and triage. These are the actual user-facing workflows. The setup skill helps connect accounts and configure the pack through conversation. Sync reviews new information and proposes updates. Prep builds a one-on-one meeting brief. Triage captures the user’s judgment so the other workflows can follow it.

The single function, `pack`, packages all of that into a `Pack` object that the UFO platform can load. In everyday terms, this file is the recipe card; the platform reads it before cooking.

#### Function details

##### `pack`  (lines 40–46)

```
def pack() -> Pack
```

**Purpose**: Creates and returns the formal description of the chief-of-staff pack. The platform uses this to learn the pack’s name, version, required extensions, and available skills.

**Data flow**: It starts with constants already written in the file: the pack name, version, extension names, the skills folder, and the four skill names. It turns each skill name into a `SkillSpec`, which points at that skill’s folder on disk. It then puts the name, version, extensions, and skill specs into a `Pack` object and returns that object to the caller.

**Call relations**: When the UFO platform is discovering packs, it calls `pack` to ask, “What does this pack contain?” Inside that answer, `pack` calls `SkillSpec.__init__` once for each skill path so each skill is described in the expected format, then calls `Pack.__init__` to bundle the whole manifest into one loadable object.

*Call graph*: 2 external calls (__init__, __init__).


### `packs/dsqa_eval/ufo_pack_dsqa_eval.py`

`config` · `startup/config load`

This file is like a menu of prebuilt tool bundles. A “pack” is a named collection of extensions, where an extension adds a capability such as indexing, using embeddings, calling a search provider, or driving a browser. Instead of making every caller remember the exact extension list, this file gives three clear options.

The smallest bundle is the core pack. It includes the base pieces needed for default indexing, OpenAI-style embeddings, and OpenRouter access. The search pack builds on that by adding Exa and research-related tools, so it can look things up beyond the local or base setup. The browser pack builds on the search pack again by adding browser and sandboxed Chrome support, so it can interact with web pages more directly.

All three functions return a `Pack` object from `ufo.sdk.manifest`. That object is the system’s structured way of saying: “Here is the pack name, its version, and the extensions it needs.” Without this file, other parts of the project would have to duplicate these extension lists by hand, which would make the setup easier to mistype and harder to update consistently.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the basic DSQA evaluation pack. Use this when the system only needs the shared core extensions: default indexing, OpenAI embeddings, and OpenRouter access.

**Data flow**: It starts with the fixed core pack name, the shared version string, and the core extension list. It passes those values into `Pack`, which turns them into a structured pack object. The result is returned to the caller without changing anything else.

**Call relations**: When another part of the system asks for the core DSQA pack, this function is the simple factory that builds it. Its only handoff is to `Pack.__init__`, which receives the name, version, and extension list and creates the manifest object.

*Call graph*: 1 external calls (__init__).


##### `search_pack`  (lines 17–18)

```
def search_pack() -> Pack
```

**Purpose**: Creates the DSQA evaluation pack that includes search tools. Use this when the evaluation needs the core setup plus external search and research capabilities.

**Data flow**: It takes the fixed search pack name, the shared version string, and the search extension list. That list includes all base extensions plus `exa` and `research`. It gives these values to `Pack` and returns the finished pack object.

**Call relations**: When the system needs a DSQA pack with search enabled, this function prepares that manifest. It delegates the actual object creation to `Pack.__init__`, giving it the search-specific name and extension set.

*Call graph*: 1 external calls (__init__).


##### `browser_pack`  (lines 21–22)

```
def browser_pack() -> Pack
```

**Purpose**: Creates the most capable DSQA evaluation pack, including browser automation. Use this when the evaluation needs core tools, search tools, and the ability to use a sandboxed Chrome browser.

**Data flow**: It starts from the fixed browser pack name, the shared version string, and the browser extension list. That list includes the search extensions plus `browser` and `sandbox_chrome`. It passes everything into `Pack` and returns the resulting pack object.

**Call relations**: When a caller wants the full DSQA setup with browsing support, this function builds the corresponding manifest. It hands the pack details to `Pack.__init__`, which packages them into the object the rest of the UFO system can load.

*Call graph*: 1 external calls (__init__).


### `packs/gdpval_eval/ufo_pack_gdpval_eval.py`

`config` · `pack discovery and configuration load`

This file is like a menu of prebuilt toolkits for running GDPVal evaluation work. A pack is a named bundle of extensions, where an extension is an add-on that gives the system a capability, such as search, document handling, browser use, or access to a language model provider. Without this file, users or automation would need to remember the exact extension combinations for each GDPVal mode, which is error-prone and harder to share.

The file starts by naming a shared version and several groups of extensions. Every pack includes the same base tools: a default index, OpenAI embeddings, and OpenRouter model access. Then each specialized pack adds more tools. The document pack adds document processing, a read-eval-print loop, and coding support. The research pack adds web and research tools such as Exa, browser access, and a Chrome sandbox. The full pack combines everything.

Each function returns a `Pack`, which is the manifest object the UFO system can read to learn the pack’s name, version, and included extensions. In plain terms, the functions do not run the evaluation themselves; they label and package the right tools so the rest of the system can load them consistently.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the smallest GDPVal pack. This is useful when the system only needs the shared base capabilities, such as indexing, embeddings, and model access.

**Data flow**: It reads the fixed core pack name, version, and base extension list from this file. It puts those values into a new `Pack` object. The result is a manifest that tells UFO to load only the base extensions for GDPVal evaluation.

**Call relations**: When the pack system asks for the core GDPVal setup, this function builds the manifest by calling `Pack.__init__`. It hands off the chosen name, version, and extension list so the broader UFO loading process can treat the pack as a standard package of capabilities.

*Call graph*: 1 external calls (__init__).


##### `documents_pack`  (lines 17–22)

```
def documents_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack for document-heavy work. It starts with the core tools and adds document, REPL, and coding-related extensions.

**Data flow**: It reads the shared base extensions and the document-specific extension group. It combines them into one ordered set of extensions, then places that list with the document pack name and version into a new `Pack` object. The result is a manifest for a document-focused GDPVal setup.

**Call relations**: When something needs the document-oriented GDPVal environment, this function prepares that environment’s manifest. It delegates the actual manifest construction to `Pack.__init__`, which receives the combined extension list and makes it usable by the UFO pack loader.

*Call graph*: 1 external calls (__init__).


##### `research_pack`  (lines 25–30)

```
def research_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack for research-oriented tasks. It includes the core tools plus extensions for web search, research workflows, browser use, and a sandboxed Chrome environment.

**Data flow**: It takes the common base extension list and appends the research extension group. It then creates a `Pack` with the research pack name, shared version, and combined extensions. The output is a manifest describing the research-capable GDPVal toolkit.

**Call relations**: When the system or a user selects the research GDPVal setup, this function supplies the pack definition. It calls `Pack.__init__` to turn the chosen extension names into the standard manifest object that the rest of UFO knows how to load.

*Call graph*: 1 external calls (__init__).


##### `full_pack`  (lines 33–38)

```
def full_pack() -> Pack
```

**Purpose**: Creates the most complete GDPVal pack. It combines the base, document, and research extensions so the environment has all available GDPVal-related tools.

**Data flow**: It reads all three extension groups from this file: base, document, and research. It joins them into one extension list, then creates a `Pack` using the full pack name and shared version. The result is a manifest for the all-included GDPVal setup.

**Call relations**: When the broadest GDPVal environment is needed, this function builds that full manifest. It hands the complete extension list to `Pack.__init__`, allowing the UFO loading machinery to activate the whole set as one named pack.

*Call graph*: 1 external calls (__init__).


### Sample and YC packs
Small proof-of-system and YC founder pack definitions, including package setup and manifest metadata.

### `packs/sample_pack/ufo_pack_sample.py`

`config` · `pack discovery and onboarding`

This is a conformance sample: a simple installed pack whose job is to exercise the public pack interface the way a real outside pack would. It imports only from `ufo.sdk`, which is the stable surface that other code is expected to use. That makes it a useful canary: if this file stops working, the pack boundary has probably been broken.

The file names the pack, its version, the extension it bundles, the skill folder it contributes, and an onboarding step. A “skill” is a packaged capability the system can discover and use. An “onboarding step” is setup work that runs when the pack is activated.

The important behavior is that onboarding does not just print a message or write to a fake test log. The `_setup` function writes a small marker into the pack’s scoped store, which is durable storage owned through the extension context. In everyday terms, it leaves a receipt in the same filing cabinet the real system uses. Tests can then read that receipt back through the public API and know the pack was truly loaded and run.

The `pack()` function is the entry point the pack loader asks for. It returns a `Pack` object that describes everything this pack contributes.

#### Function details

##### `_setup`  (lines 25–26)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: This is the pack’s onboarding action. When run, it records that the sample pack completed setup by writing a small marker into the pack’s store.

**Data flow**: It receives an `ExtensionContext`, which gives access to the pack’s scoped storage. It writes the key `pack:onboarded` with the value `{"pack_onboarded": true}`. It returns nothing, but it changes persistent storage so later code or tests can confirm the onboarding step really happened.

**Call relations**: The `pack` function attaches `_setup` to an `OnboardingStep`. Later, when the system activates this pack and runs onboarding, that step calls `_setup` with the current context so it can write its setup receipt.


##### `pack`  (lines 29–36)

```
def pack() -> Pack
```

**Purpose**: This is the pack entry point. The pack loader calls it to learn what the sample pack contains: its name, version, bundled extension, contributed skill, and onboarding step.

**Data flow**: It reads the constants defined in this file, builds a `SkillSpec` pointing at the sample skill directory, builds an `OnboardingStep` that uses `_setup`, and wraps everything in a `Pack` object. The result is a complete description of the pack that the rest of the system can load.

**Call relations**: When the pack system discovers this installed pack, it calls `pack`. Inside, this function creates the `SkillSpec`, the `OnboardingStep`, and finally the `Pack` object that ties them together. The returned `Pack` tells the loader which extension to activate and which setup work to run.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `packs/yc/ufo_pack_yc/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker file. In Python, a folder often needs an `__init__.py` file so the import system treats that folder as a package, meaning code elsewhere can refer to modules inside it using package-style names. Think of it like putting a label on a drawer: the drawer may contain useful tools, but this label mainly tells the rest of the system how to find them. Because the file has no code, it does not set up state, load settings, define shortcuts, or run any startup logic. Its value is structural: without it, depending on the Python version and packaging setup, imports from `packs/yc/ufo_pack_yc` might fail or behave differently.


### `packs/yc/ufo_pack_yc/manifest.py`

`config` · `config load`

A “pack” is a bundle of abilities that can be added to the system, a bit like installing a toolbox with several tools inside. This file is the label and packing list for the YC founder pack. Without it, the system would not know the pack’s name, which version it is, what supporting features it needs, or where to find its skills on disk.

The file first defines simple facts: the pack is called “yc”, its version is “0.1.0”, and it expects several extensions to be available, such as memory, document support, scheduled tasks, todos, and OpenAI-based embeddings. It then points to a local skills directory and names two skills: “founder-operations” and “company-diligence”.

The main function, `pack`, turns those plain settings into a `Pack` object that the UFO software development kit can understand. For each named skill, it creates a `SkillSpec`, which is a small description saying where that skill lives. In short, this file does not perform the founder workflows itself; it tells the system how to discover and prepare the workflows that live in the pack.

#### Function details

##### `pack`  (lines 23–29)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the official description of the YC founder pack. The rest of the system can call this to learn the pack’s name, version, required extensions, and skill locations.

**Data flow**: It reads the constants in this file: the pack name, version, extension names, skills root folder, and skill names. It combines each skill name with the skills folder path, wraps each one in a `SkillSpec`, then places everything into a `Pack` object. The result is a complete pack description returned to the caller; it does not change files or external state.

**Call relations**: When the pack is being discovered or loaded, the system calls `pack` to get a structured description instead of reading the constants by hand. Inside that process, `pack` hands each skill path to `SkillSpec.__init__` so each skill has a formal description, then hands the full set of metadata to `Pack.__init__` so the system receives one ready-to-use pack object.

*Call graph*: 2 external calls (__init__, __init__).
