# Pack recipes and pack package declarations  `stage-18.1`

This stage is a set of recipes for starting the system in different modes. A “pack” is a named bundle: it does not build the tools itself, but tells UFO which existing extensions, skills, onboarding steps, and service choices to load together. Think of it like choosing a pre-packed toolbox before work begins.

The local assistant pack turns on the full developer assistant experience, while the billing assistant pack adds billing setup for local testing. The hosted assistant pack selects versions meant to run on managed services. The assistant evaluation pack uses fake email, calendar, and code-search tools so tests can run safely without real integrations.

Other packs target specific jobs. The chief-of-staff pack loads Slack, meetings, notes, memory, tasks, and follow-up workflows for a manager-style assistant. The DSQA and GDPVal files define evaluation toolkits with different search or browser capabilities. The sample pack is a small end-to-end proof that packs can add an extension, a skill, and onboarding. The YC package marker makes its folder importable, and its manifest defines the YC founder pack’s name, version, dependencies, and skills.

## Files in this stage

### Assistant pack variants
Local, billing-aware, evaluation, and hosted recipes define the main assistant configurations for different runtime environments.

### `packs/assistant_billing/ufo_pack_assistant_billing.py`

`config` · `config load`

This file exists to fill a gap between two ways of running the system. The normal local assistant pack is safe for development because it does not send usage to a billing provider. The hosted assistant pack includes billing, but also includes hosted services that are not useful on a laptop. This pack is the middle option: it starts with the regular assistant setup and adds Metronome, the external billing and usage metering service.

In plain terms, it is like taking the standard local toolbox and adding just the billing tool, without bringing the whole production workshop. That matters because one onboarding action, “Set up billing,” needs the billing pieces to be present. Without this pack, a developer could see that action locally but could not prove the full billing path worked end to end.

The file names the pack `assistant_billing`, gives it a version, and builds its list of extensions by reusing everything from the normal assistant pack plus `metronome`. Because real billing credentials are needed, this pack is intentionally opt-in. It is meant to be used with sandbox or test-mode credentials, not live production billing keys.

#### Function details

##### `pack`  (lines 25–26)

```
def pack() -> Pack
```

**Purpose**: This function builds the pack description that the UFO system can load. It tells the system the pack’s name, version, and which extensions should be turned on, including the added Metronome billing extension.

**Data flow**: It reads the file’s constants: the pack name, version, and combined extension list. It uses those values to create a `Pack` object, which is the system’s structured description of what this pack contains. The result is returned to the caller so the runtime can enable the right pieces.

**Call relations**: When the pack system asks this file for its pack definition, this function is the handoff point. It creates a `Pack` by calling `Pack.__init__`, giving the broader system a ready-made recipe for running the assistant with billing enabled.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_dev/ufo_pack_assistant.py`

`config` · `startup/config load`

This file is like a packing list for a complete assistant setup. When someone chooses the pack named “assistant”, the system uses this file to know exactly which extensions should be active. Those extensions provide the real features: memory, search, web research, browser automation, code execution, document creation, connectors to outside services, scheduled tasks, debugging tools, and more.

The important idea is that this pack does not contain the tools themselves. It only names them. Each extension listed here brings its own tools, setup instructions, and behavior through its own manifest. That keeps this file small and focused: it answers the question, “What should be switched on together for the standard assistant?”

The module sets three pieces of information: the pack name, its version, and the ordered collection of extension names. The `pack` function then wraps those values in a `Pack` object, which is the format the wider system expects when discovering and activating packs.

Without this file, users could still perhaps enable extensions one by one, but there would be no single named “assistant” bundle that reliably starts the intended local assistant configuration.

#### Function details

##### `pack`  (lines 48–49)

```
def pack() -> Pack
```

**Purpose**: This function creates the pack description that the system can load. It turns the file’s name, version, and extension list into a `Pack` object, which is the standard container used to describe an activatable bundle.

**Data flow**: It takes no arguments. It reads the module’s constants: `NAME`, `VERSION`, and `EXTENSIONS`. It passes those values into `Pack`, producing a pack object that says, in effect, “load the assistant pack with these extensions.”

**Call relations**: When the pack discovery or configuration-loading part of the system asks this module for its pack, this function is the handoff point. It calls `Pack.__init__` to build the object the rest of the system understands, then returns that object so startup can activate the listed extensions.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_eval/ufo_pack_assistant_eval.py`

`config` · `startup`

This file exists so the assistant can be tested in a controlled world instead of the real one. In normal use, the assistant pack may include real outside service connectors, such as broker services that connect to third-party tools. In an evaluation run, those real services may not have working keys or accounts. If they stayed visible, the assistant could waste time trying to use tools that are only empty decoys.

The file starts from the regular assistant pack, then carefully edits its list of extensions. It removes the real broker extensions named "composio" and "pipedream". Then it adds "eval_env", which provides deterministic fake services, meaning services that behave predictably every time. This is like replacing a real city map with a small test town where every street and shop is known in advance.

The key idea is that tool registry entries can appear even without permission grants. Because of that, fake evaluation providers must live only in this evaluation pack, not in the normal product pack. Otherwise, real users might see fake tools, or evaluation agents might see broken real ones. The result is a clean pack named "assistant_eval" that gives tests the right set of tools.

#### Function details

##### `pack`  (lines 23–24)

```
def pack() -> Pack
```

**Purpose**: This function builds and returns the pack definition for the evaluation version of the assistant. It is used when the system needs to know this pack's name, version, and which extensions should be loaded.

**Data flow**: It reads the file-level constants for the pack name, version, and prepared extension list. It passes those values into the Pack object, which turns them into a structured pack description. The result is returned to the caller, with no other state changed.

**Call relations**: When the pack system asks this module for its pack definition, this function creates it. Its only handoff is to Pack.__init__, which receives the chosen name, version, and evaluation-safe extension list.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_hosted/ufo_pack_assistant_hosted.py`

`config` · `config load`

This file is like a packing list for a hosted version of the assistant. When a workspace chooses the pack named “assistant_hosted”, the system reads this file to learn which capabilities should be available. Those capabilities include memory, research tools, Slack integration, browser and computer-use tools, document generation, scheduled tasks, coding help, model providers, and several connector systems for talking to outside services.

The important idea is that this pack does not implement those features itself. It gathers many separate extensions into one named bundle. Each extension brings its own behavior and setup rules. For example, Turbopuffer is used as the hosted memory index, Redis is used for live frame sharing, E2B provides sandboxed execution, and sandbox Chrome provides browser control inside each conversation’s sandbox.

Without this file, users could still potentially enable these pieces one by one, but there would be no single “hosted assistant” choice that consistently turns on the right hosted backends together. It is the difference between handing someone a full travel kit versus making them collect every item separately.

#### Function details

##### `pack`  (lines 57–58)

```
def pack() -> Pack
```

**Purpose**: This function builds and returns the pack definition that the wider system can read. It gives the pack its name, version, and the full list of extensions that should be activated.

**Data flow**: It starts with the constants in this file: the pack name, its version, and the tuple of extension names. It passes those values into the Pack data structure, which turns them into a formal pack object. The result is returned to the caller, with no other state changed.

**Call relations**: When the system is loading available packs, it calls this function to get the official description of the hosted assistant pack. The function hands the collected name, version, and extension list to Pack so the rest of the system can treat this bundle like any other pack.

*Call graph*: 1 external calls (__init__).


### Chief of staff pack
The chief of staff recipe bundles manager-focused tools, skills, and workflows into a named loadable assistant configuration.

### `packs/chief_of_staff/ufo_pack_chief_of_staff.py`

`config` · `startup / pack loading`

This file is not the assistant’s working brain itself. Instead, it is the manifest, or plain declaration, that tells the larger system what this pack is called, what version it is, what built-in extensions it needs, and which skill folders belong to it. Without this file, the system would not know how to assemble the chief-of-staff experience.

The pack is meant to support a manager’s daily operating rhythm. It pulls together things like meeting transcripts, Slack conversations, notes, people files, org information, daily logs, todos, and follow-up reminders. The actual work is carried by named skills: setup, sync, prep, and triage. Those skills live in separate folders, and this file points the system to them.

The extension list is the important “equipment checklist.” It asks for connector access, source syncing, memory, search indexing, embeddings, a knowledge graph, Slack, scheduled tasks, page alerts, todos, skill creation, and self-improvement support. In everyday terms, these are the services that let the assistant read from the right places, remember what matters, talk in Slack, prepare work on a schedule, and improve under governance.

The single function, `pack`, bundles all of that into a `Pack` object that the UFO runtime can load.

#### Function details

##### `pack`  (lines 42–48)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the formal description of the chief-of-staff pack. The system uses this description to know the pack’s name, version, required extensions, and the skill directories it should load.

**Data flow**: It starts with constants defined in this file: the pack name, version, extension names, the skills directory, and the list of skill names. It turns each skill name into a `SkillSpec`, which is a small description pointing at that skill’s folder. It then puts the name, version, extensions, and skill descriptions into a `Pack` object and returns it.

**Call relations**: When the larger UFO system loads this pack, it calls `pack` to get the pack’s manifest. Inside that moment, `pack` creates `SkillSpec` objects for each skill folder, then hands all of them to `Pack` so the runtime has one complete object describing what to enable and where to find the workflows.

*Call graph*: 2 external calls (__init__, __init__).


### Benchmark evaluation packs
DSQA and GDPVal recipes declare compact tool bundles used for controlled evaluation scenarios.

### `packs/dsqa_eval/ufo_pack_dsqa_eval.py`

`config` · `pack discovery / setup`

This file is like a menu of tool bundles for DSQA evaluation work. Instead of asking users or other code to remember a long list of required extensions, it gives each bundle a clear name and version, then returns a `Pack` object that describes it.

The smallest bundle is the core pack. It includes the base pieces needed for indexing, OpenAI-style embeddings, and access through OpenRouter. The search pack builds on that by adding web/research search tools. The browser pack builds on the search pack again by adding browser automation and a Chrome sandbox, so tasks can go beyond searching and actually interact with web pages.

The important idea is layering. Each larger pack reuses the previous set of extensions and adds more capability. That keeps the definitions short and reduces the chance that similar packs accidentally drift apart. Without this file, someone wanting a DSQA setup would need to manually assemble the right extension list, which is error-prone and harder to standardize across runs.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the basic DSQA pack. Someone would use this when they need the core evaluation setup without web search or browser control.

**Data flow**: It reads the fixed core name, version, and core extension list from this file. It passes those values into `Pack`, which packages them into a manifest object. The result is a `Pack` describing the core DSQA setup.

**Call relations**: When code wants the core DSQA bundle, it calls this function. The function immediately hands the chosen name, version, and extensions to `Pack.__init__`, which creates the actual pack description.

*Call graph*: 1 external calls (__init__).


##### `search_pack`  (lines 17–18)

```
def search_pack() -> Pack
```

**Purpose**: Creates a DSQA pack that includes search and research tools. Someone would use this when evaluation tasks need to look up information beyond the local index or model context.

**Data flow**: It reads the search pack name, shared version, and the search extension list. That list includes the base extensions plus extra search and research extensions. It gives those values to `Pack`, and returns the completed pack description.

**Call relations**: When code wants the search-capable DSQA bundle, it calls this function. The function delegates the actual manifest construction to `Pack.__init__`, using the search-specific extension set.

*Call graph*: 1 external calls (__init__).


##### `browser_pack`  (lines 21–22)

```
def browser_pack() -> Pack
```

**Purpose**: Creates the most capable DSQA pack in this file, adding browser automation on top of search. Someone would use this when tasks may need to open pages and interact with them, not just search for them.

**Data flow**: It reads the browser pack name, shared version, and browser extension list. That list includes the base tools, search and research tools, plus browser and sandboxed Chrome support. It passes everything into `Pack`, and returns the resulting pack manifest.

**Call relations**: When code wants the browser-enabled DSQA bundle, it calls this function. The function then calls `Pack.__init__` to turn the chosen bundle details into a usable pack object.

*Call graph*: 1 external calls (__init__).


### `packs/gdpval_eval/ufo_pack_gdpval_eval.py`

`config` · `config load`

This file is a small manifest builder. A manifest is a plain description of what should be included when a system starts or installs a capability. Here, the capability is GDPVal evaluation, and the file offers four versions of it: a basic core pack, a document-focused pack, a research-focused pack, and a full pack that includes everything.

The shared pieces are kept as constants at the top. The base extensions include common services such as indexing, OpenAI embeddings, and OpenRouter model access. The document extensions add tools for working with documents, a REPL-like interactive environment, and coding support. The research extensions add web and research tools such as Exa, browser access, and a Chrome sandbox.

Each function returns a `Pack`, which is the UFO SDK’s object for saying “this named package contains these extensions at this version.” Without this file, someone setting up GDPVal would have to manually remember and assemble the right extension combinations. This file makes those choices explicit, reusable, and less error-prone.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the smallest GDPVal pack. Someone would use this when they only need the shared foundation: indexing, embeddings, and model routing.

**Data flow**: It reads the fixed core name, version number, and base extension list from this file. It passes those values into a new `Pack` object, which comes out as a compact description of the core GDPVal setup. It does not change any outside state.

**Call relations**: When a pack registry or loader asks for the core GDPVal package, this function builds it by calling `Pack.__init__`. It hands the SDK a simple name, version, and extension list so the rest of the system can later install or activate that bundle.

*Call graph*: 1 external calls (__init__).


##### `documents_pack`  (lines 17–22)

```
def documents_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack for document-heavy work. It starts with the base tools and adds document, interactive, and coding-related extensions.

**Data flow**: It reads the fixed document pack name, version number, base extension list, and document extension list. It combines the base and document extensions into one ordered bundle, then returns a new `Pack` object describing that bundle. Nothing else is modified.

**Call relations**: When the system needs the document-oriented GDPVal setup, this function is the recipe it uses. It calls `Pack.__init__` with the combined extension list, handing the UFO SDK enough information to recognize and load the document-capable pack.

*Call graph*: 1 external calls (__init__).


##### `research_pack`  (lines 25–30)

```
def research_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack for research-style tasks. It keeps the base tools and adds extensions for search, browsing, research workflows, and a sandboxed Chrome browser.

**Data flow**: It reads the research pack name, shared version, base extensions, and research extensions. It joins the base and research tools together, then returns a `Pack` object containing that name, version, and full extension set. It has no side effects beyond creating that object.

**Call relations**: When a loader wants the research version of the GDPVal pack, this function supplies the manifest. It delegates the actual `Pack` object creation to `Pack.__init__`, giving the SDK the combined list of tools to include.

*Call graph*: 1 external calls (__init__).


##### `full_pack`  (lines 33–38)

```
def full_pack() -> Pack
```

**Purpose**: Creates the most complete GDPVal pack. It includes the base, document, and research extensions all together.

**Data flow**: It reads the full pack name, version number, and all three extension groups from this file. It merges them into one extension tuple and uses that to create and return a `Pack` object. It does not write files, contact services, or alter global settings.

**Call relations**: When the system wants the all-included GDPVal setup, this function acts as the one-stop recipe. It calls `Pack.__init__` with every extension group so the SDK can treat the full collection as a single named pack.

*Call graph*: 1 external calls (__init__).


### Sample pack fixture
The sample pack proves the pack mechanism end to end with a minimal extension, skill, and onboarding step.

### `packs/sample_pack/ufo_pack_sample.py`

`test` · `pack loading and onboarding`

This is a conformance sample: a deliberately simple pack that tests the public pack boundary. A “pack” is a bundle of extension-related pieces that can be installed and activated together, like a starter kit. This file imports only from `ufo.sdk`, which is the public surface other pack authors are expected to use. That matters because if this sample breaks, it suggests outside pack authors may also be broken.

The file names the pack, its version, the bundled extension, a skill directory, and an onboarding marker key. Its `pack()` function returns a `Pack` object that says: include the `sample` extension, include one skill stored on disk, and run one onboarding step named `sample_pack_setup`.

The onboarding step uses `_setup`. When run, `_setup` writes `{"pack_onboarded": True}` into the pack’s scoped store under `pack:onboarded`. This is important because it is not just printing a fake message or using a mock. It writes through the same durable storage path the real system uses. In everyday terms, this file is a smoke alarm for the pack doorway: if loading this pack no longer produces the expected extension, skill, and stored onboarding record, the project knows the pack seam has been damaged.

#### Function details

##### `_setup`  (lines 25–26)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: This is the onboarding action for the sample pack. It records, in persistent pack storage, that the pack’s setup step has run.

**Data flow**: It receives an `ExtensionContext`, which is the pack’s access point to shared services such as its scoped store. It writes the key `pack:onboarded` with the value `{"pack_onboarded": True}` into that store. It does not return a meaningful value; the lasting result is the saved record.

**Call relations**: The `pack()` function attaches `_setup` to an `OnboardingStep`. Later, when the system runs that onboarding step, this function is the callback that performs the actual write, giving tests a real stored result to read back.


##### `pack`  (lines 29–36)

```
def pack() -> Pack
```

**Purpose**: This is the public entry point for the pack. It builds and returns the `Pack` description that tells the system what this sample pack contains.

**Data flow**: It reads the constants in this file, including the pack name, version, bundled extension name, skill path, and onboarding name. It uses those values to create a `SkillSpec`, an `OnboardingStep` wired to `_setup`, and then a `Pack` containing the extension, skill, and onboarding step. The output is a complete `Pack` object ready for the loader to use.

**Call relations**: When the pack loader imports this module, it calls `pack()` to discover the pack’s contents. Inside, `pack()` calls `SkillSpec.__init__` to describe the skill directory, `OnboardingStep.__init__` to register `_setup` as the setup action, and `Pack.__init__` to bundle everything into the final pack definition.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### YC founder pack
The YC package marker and manifest expose the founder-focused pack and declare its dependencies and skill folders.

### `packs/yc/ufo_pack_yc/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker. In Python projects, a file named `__init__.py` tells Python, and often project tools, that the surrounding folder should be treated as an importable package. You can think of it like a label on a drawer: the drawer may contain useful tools, but this label mainly makes sure the rest of the system can find and open it in the expected way. Because the file has no code, it does not start anything, configure anything, or change data. Its value is structural: without it, some import styles or tooling may fail to recognize `packs/yc/ufo_pack_yc` as a package, especially in environments that still rely on explicit package markers.


### `packs/yc/ufo_pack_yc/manifest.py`

`config` · `startup / pack discovery`

This file is like the label and packing list on a box. The box is a UFO “pack”: a bundle of features meant to work together. Here, the bundle is aimed at YC-style founder work, such as founder operations and company diligence.

The file does not run those skills itself. Instead, it tells the larger system what is inside the pack and what other parts must be present for it to work. The extension list names supporting capabilities such as command-line integration, memory, document handling, scheduled tasks, todos, embeddings, and a knowledge graph. In plain terms, those are the tools this pack expects to have available.

It also defines where the pack’s skills live on disk. The skills are stored under a local `skills` folder, and the file names two skill directories: `founder-operations` and `company-diligence`. When the system loads this pack, it calls `pack()` to receive a structured `Pack` object. Without this manifest, the system would not know the pack’s identity, dependencies, or which skill folders to load.

#### Function details

##### `pack`  (lines 24–30)

```
def pack() -> Pack
```

**Purpose**: This function builds and returns the formal description of the YC founder pack. The system uses it to learn the pack’s name, version, required extensions, and included skills.

**Data flow**: It starts with constants already defined in the file: the pack name, version, extension names, the skills folder path, and the skill names. It turns each skill name into a `SkillSpec`, which is a small description pointing at that skill’s folder. It then puts all of that into a `Pack` object and returns it to the caller.

**Call relations**: When the pack-loading part of the system asks this manifest what it contains, `pack` creates the answer. To do that, it calls `SkillSpec.__init__` for each skill folder so the skills can be described, then calls `Pack.__init__` to wrap the full pack description into one object the rest of UFO can use.

*Call graph*: 2 external calls (__init__, __init__).
