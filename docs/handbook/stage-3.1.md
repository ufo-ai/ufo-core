# Pack and extension discovery  `stage-3.1`

This stage is part of startup and setup. Before the assistant can do useful work, the system must know which “pack” to load. A pack is a named recipe: it lists the extensions, skills, and setup steps that should be turned on together. The assistant, hosted assistant, billing, evaluation, chief-of-staff, YC, GDPVal, DSQA, and sample pack files are these recipes. Some build a normal local assistant, some use cloud-style services, and others create controlled test setups that avoid real outside accounts and secrets.

The extension package files are the door labels for Python. Files like the REPL, research, scheduled tasks, Slack, web, sources, sites, YC, and UFO extension initializers make their folders importable so the rest of the system can find their code. A few also describe their extension’s role, such as creating skills or reviewing past activity for approved prompt improvements.

Together, these files act like a plug-in shelf and a set of shopping lists. The shelf makes tools discoverable; the lists choose which tools are used for each assistant setup.

## Files in this stage

### Extension package markers
These package initializers make each extension importable before packs refer to their manifests, tools, skills, jobs, and hooks.

### `extensions/repl/ufo_ext_repl/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That matters because other parts of the project can then refer to code inside `extensions/repl/ufo_ext_repl` using normal Python import paths. Think of it like putting a label on a folder in a filing cabinet: the label does not contain documents, but it tells the system that the folder is meant to be found and used as a unit. Because this file has no code, it does not start anything, configure anything, or change runtime behavior directly. Its value is structural: without it, some Python tooling or older import setups might not recognize this directory as a package, which could make the REPL extension harder or impossible to import in those environments.


### `extensions/research/ufo_ext_research/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can write imports that refer to `extensions.research.ufo_ext_research` and the modules inside it. Think of it like a label on a drawer: the drawer may contain useful tools elsewhere, but this label is what lets the rest of the system find the drawer by name. Because the file has no code, it does not run setup steps, create objects, or change program behavior directly. Its value is structural: without it, depending on the Python version and import style, code that expects this directory to be a normal package might fail to import it.


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a folder with an `__init__.py` file is treated as an importable package, like putting a label on a drawer so the rest of the system can find what is inside it. Here, the drawer is the `ufo_ext_scheduled_tasks` extension package.

Because the file is empty, it does not run setup code, expose helper functions, or change any settings. Its main value is structural: it lets Python and project tooling recognize this directory as a module namespace. Without it, depending on the Python version and packaging setup, imports that expect `ufo_ext_scheduled_tasks` to be a normal package might fail or behave differently.

So this file matters not because of code it contains, but because of what it allows: other files in the scheduled-tasks extension can live under a clear package name and be imported consistently.


### `extensions/self_improvement/ufo_ext_self_improvement/__init__.py`

`other` · `import time`

This file is very small, but it gives the package its identity. In Python, an `__init__.py` file tells the system that a folder should be treated as an importable package. Here, it also contains a short description of the extension’s purpose.

The extension is described as an offline replay evaluation loop. In plain terms, it looks back at saved records of what happened in the workspace, called trajectories, and uses them to find possible improvements. Instead of changing prompts automatically, it opens proposed prompt changes under governance, meaning a person must review and approve them before they take effect.

The important idea is safety. This extension is about learning from past runs, but not silently rewriting how the system behaves. It is more like a suggestion box with evidence attached: the system studies what happened, drafts an improvement, and then waits for a human decision.


### `extensions/sites/ufo_ext_sites/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it has an `__init__.py` file. That lets the rest of the project refer to code inside this directory using package-style names, rather than only by file paths. Think of it like a label on a drawer: the drawer may hold useful tools in other files, but this label is what lets people find and open it by name. Because the file is empty, it does not define settings, run setup code, or change behavior when imported. Its main value is structural: without it, some Python environments or tooling might not recognize `extensions/sites/ufo_ext_sites` as a normal package, which could break imports or plugin discovery.


### `extensions/skill_create/ufo_ext_skill_create/__init__.py`

`other` · `cross-cutting`

This file is the front door label for the skill-creation extension. It does not contain executable code, but its short module note explains why this package exists. The extension supports a workflow where users can create new skills, treats `skill` as a kind of object the system understands, and provides a per-turn runtime-skills provider. In plain terms, that provider makes sure the skills saved in a workspace are available when the system is deciding what it can do during a turn. Without this package marker, Python would not treat this folder as an importable package in the usual way, and without the extension it describes, the larger system would not have this particular skill-authoring feature set. Think of it like a labeled drawer: the drawer label does not do the work itself, but it tells the rest of the workshop what belongs here and lets other code open the drawer by name.


### `extensions/slack/ufo_ext_slack/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Here, it makes `ufo_ext_slack` available as a named package inside the Slack extension area. Think of it like a label on a drawer: the drawer may contain useful tools in other files, but this label lets the rest of the system find and open it in a standard way. Because the file is empty, it does not run setup code, create objects, or change settings. Its importance is mostly structural. Without it, some import styles or packaging tools might not recognize this directory as a normal Python package, which could make the Slack extension harder or impossible to load in certain environments.


### `extensions/sources/ufo_ext_sources/__init__.py`

`other` · `import/package discovery`

This is an empty package file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Think of it like putting a label on a drawer: the drawer may contain useful tools in other files, but this label lets the rest of the program find the drawer by name. Without this file, depending on the Python version and packaging setup, imports from `extensions/sources/ufo_ext_sources` might fail or behave differently. Because it is empty, it does not run setup logic, define shared values, or expose helper functions. Its main value is structural: it makes the source-extension folder part of the project’s Python module layout.


### `extensions/ufo/ufo_ext_ufo/__init__.py`

`other` · `import time`

This is an empty Python package marker file. In Python, a folder with an `__init__.py` file is treated as a package, which means code elsewhere can import modules from that folder using package-style names. Think of it like putting a label on a drawer: the drawer may contain useful tools, but this label simply tells Python that the drawer belongs in the organized set of importable code. Because the file is empty, it does not run setup code, expose shortcuts, or define any values. Its main value is structural: without it, depending on the Python version and import style being used, imports that expect `ufo_ext_ufo` to be a normal package could fail or behave differently.


### `extensions/web/ufo_ext_web/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools, but this label mainly lets the rest of the system find and open it by name. Because the file is empty, importing `ufo_ext_web` does not run setup code, define shortcuts, or expose extra names. Its value is structural: without it, some Python environments or tooling might not recognize `extensions/web/ufo_ext_web` as a package, which could make imports less reliable.


### `extensions/yc/ufo_ext_yc/__init__.py`

`other` · `cross-cutting`

In Python, a folder often needs an `__init__.py` file to be treated as a package, which is a named group of related code files. This file is that marker for the `extensions/yc/ufo_ext_yc` package. It is like a label on a drawer: the label does not do the work itself, but it lets people and tools find what is inside the drawer in an organized way.

Because the file is empty, it does not define settings, run startup code, or expose helper functions. Its importance is structural. Without it, some Python environments or packaging tools might not recognize this directory as an importable package, especially in older or stricter setups. That could make code elsewhere fail when it tries to import modules from `ufo_ext_yc`.

So this file matters not because of behavior, but because it helps the project’s extension code sit in the expected Python package layout.


### Assistant pack variants
These packs define the main assistant configurations for local development, billing-path testing, deterministic evaluation, and hosted operation.

### `packs/assistant_billing/ufo_pack_assistant_billing.py`

`config` · `config load / startup`

Most local assistant setups should not send usage data to a billing vendor, so the regular assistant pack leaves billing out. But one important user flow, setting up billing, needs the Metronome billing service to be present. This file fills that gap.

Think of it like a special test kit: it starts with the usual assistant bundle, then adds one extra part, Metronome, so the whole billing chain can be tried locally. The file gives this pack a name, a version, and a list of extensions to turn on. Its extension list is copied from the normal assistant pack and then extended with "metronome".

The long comment at the top is important context. It warns that this pack is opt-in because it needs real billing credentials, though they should be sandbox or test-mode credentials. Choosing this pack turns on billing-related behavior such as billing setup, activation jobs, and usage or seat reporting. Without this file, a developer could run the assistant locally, but could not locally prove that the hosted “Set up billing” action actually works.

#### Function details

##### `pack`  (lines 25–26)

```
def pack() -> Pack
```

**Purpose**: This function builds the pack description that the system uses to know what this optional assistant-with-billing setup contains. It returns the pack name, version, and enabled extensions in a single Pack object.

**Data flow**: It reads the module-level constants for the pack name, version, and extension list. It passes those values into the Pack constructor, which turns them into a structured pack record. The result is returned to whoever is loading available packs; nothing else is changed.

**Call relations**: When the pack-loading part of the system asks this module what it provides, this function creates the answer. Its only handoff is to Pack.__init__, which receives the chosen name, version, and extensions so the rest of the system can activate the assistant features plus Metronome billing.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_dev/ufo_pack_assistant.py`

`config` · `startup/config load`

This file does not implement the assistant’s tools directly. Instead, it tells the system which ready-made pieces to load when someone chooses the pack named “assistant”. A pack is a convenient bundle: rather than enabling memory, web research, browser tools, coding tools, connectors, document tools, scheduled tasks, and other features one by one, this file groups them under one name.

The important idea is that this assistant pack is meant to run using the project’s own local carrier and local index, not a separate managed hosted setup. The extensions listed here bring in the actual behavior. For example, memory and recall come from the memory-related extensions, web research comes from research and Exa search support, browser use comes through sandbox Chrome, and coding support comes through the coding and REPL extensions.

The file also records a version number, so the pack can be identified and evolved over time. Its single function, `pack`, returns a `Pack` object containing the name, version, and extension list. Without this file, the system would not have this one-step “assistant” preset; users or deployers would need to know and configure all of these individual extensions themselves.

#### Function details

##### `pack`  (lines 48–49)

```
def pack() -> Pack
```

**Purpose**: Creates the pack definition for the local assistant bundle. The system uses it when it needs to know the pack’s name, version, and which extensions should be activated together.

**Data flow**: It reads the constants in this file: the pack name, version, and tuple of extension names. It puts those values into a `Pack` object, which is the structured description the rest of the system can load and use.

**Call relations**: When the pack system asks this file for its pack definition, `pack` builds and returns a `Pack`. It hands the configured extension list to `Pack.__init__`, so the broader startup/configuration flow can activate exactly those capabilities as the “assistant” pack.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_eval/ufo_pack_assistant_eval.py`

`config` · `startup/config load`

This file is a small but important safety switch for running the assistant in an evaluation setting. The normal assistant pack can include connectors to real broker services such as Composio or Pipedream. In an evaluation run, those real services are not available, and listing them as usable tools would mislead the agent into wasting attempts on connectors that cannot work.

To avoid that, this file builds a separate pack named `assistant_eval`. It starts from the normal assistant pack’s extension list, removes the real broker extensions, and then adds `eval_env`, which provides deterministic fake services such as email, calendar, and code search. “Deterministic” means the same inputs produce the same outputs every time, which is essential for fair and repeatable tests.

An everyday analogy: the normal assistant pack is like a real office with live phones, calendars, and inboxes. This evaluation pack is like a training room with realistic props. The assistant can practice the same tasks, but nothing reaches the outside world and the results are predictable.

#### Function details

##### `pack`  (lines 23–24)

```
def pack() -> Pack
```

**Purpose**: This function creates and returns the pack definition for the evaluation version of the assistant. It gives the system the pack name, version, and the curated list of extensions that should be available during eval runs.

**Data flow**: It reads the constants defined in this file: the evaluation pack name, version, and extension list. It passes those values into the `Pack` object constructor, producing a single pack description that the larger system can load. Nothing else is changed; the result is the configured `Pack` object.

**Call relations**: When the system loads this pack, it calls `pack` to get the configuration. Inside that moment, `pack` hands the prepared name, version, and extension list to `Pack.__init__`, which turns those plain values into the standard pack object used by the rest of the platform.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_hosted/ufo_pack_assistant_hosted.py`

`config` · `config load`

A “pack” is like a pre-packed toolbox: choosing it in configuration gives the assistant a known set of abilities without the user having to list every piece one by one. This file defines the hosted assistant toolbox. It names the pack, gives it a version, and lists the extensions that should be activated with it.

The important idea is that this pack does not implement memory, Slack, browser control, research, coding, documents, or other features itself. Instead, it gathers many existing extensions into one bundle. Those extensions provide things like memory search, web research, Slack connection, scheduled tasks, browser and computer-use tools, code execution, document generation, connectors to outside services, model providers, usage metering, and a coding subagent.

What makes this pack “hosted” is the choice of backing services. For example, it uses managed services such as Turbopuffer for indexing, Redis for live frame sharing, E2B for sandboxes, and Chrome running inside each conversation sandbox. Without this file, a workspace could still enable these pieces manually, but there would be no single named pack that reliably turns on the hosted assistant experience.

#### Function details

##### `pack`  (lines 57–58)

```
def pack() -> Pack
```

**Purpose**: This function builds and returns the pack definition that the larger system can load. Someone would use it when the configuration says to activate the “assistant_hosted” pack.

**Data flow**: It starts with three constants in this file: the pack name, its version, and the list of extension names. It passes those values into `Pack`, which is the system’s object for describing a pack. The result is a complete pack description that says, in effect, “load this named bundle of extensions.”

**Call relations**: When the pack loader asks this file for its pack, `pack` creates a `Pack` object by calling `Pack.__init__`. That object is then handed back to the surrounding configuration and startup flow, which can use the extension list to activate the hosted assistant capabilities.

*Call graph*: 1 external calls (__init__).


### Chief-of-staff pack
This specialized pack assembles Slack, meetings, notes, people files, and follow-up skills into a manager-focused assistant.

### `packs/chief_of_staff/ufo_pack_chief_of_staff.py`

`config` · `startup/config load`

This file is like the label and contents list on a toolkit. It does not run the chief-of-staff workflows itself. Instead, it describes the pack so the rest of the system can load it correctly.

The pack is meant to support a manager’s daily work: syncing meeting notes and Slack activity, preparing for one-on-one meetings, tracking decisions and follow-ups, and improving its own guidance based on corrections. To do that, it needs many shared system abilities, called extensions. An extension is a plug-in-like capability, such as Slack access, scheduled tasks, memory storage, search indexing, a knowledge graph, or todo tracking.

The file also points to four named skills stored in a nearby `skills` folder: setup, sync, prep, and triage. A skill is a packaged workflow the assistant can run. For example, setup is conversational, while sync and prep use the connected sources and saved state to help the member review work.

Without this file, the system would not know that this pack exists, which extensions it needs, or which skill directories belong to it. The workflows might still be written elsewhere, but there would be no simple manifest tying them into a loadable pack.

#### Function details

##### `pack`  (lines 42–48)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the pack manifest for the chief-of-staff assistant. The larger system uses this manifest to learn the pack’s name, version, required extensions, and available skills.

**Data flow**: It starts with the constants in this file: the pack name, version, extension names, the skills folder, and the list of skill names. It turns each skill name into a `SkillSpec`, which is a small description pointing at that skill’s directory. It then wraps all of that into a `Pack` object and returns it to whoever is loading packs.

**Call relations**: When the pack-loading part of the system asks this module what it provides, this function is the answer. It creates the individual skill descriptions first, then hands them to the `Pack` constructor so the system receives one complete bundle it can register and make available.

*Call graph*: 2 external calls (__init__, __init__).


### Evaluation capability packs
These packs provide named tool bundles for DSQA and GDPVal evaluation scenarios at different capability levels.

### `packs/dsqa_eval/ufo_pack_dsqa_eval.py`

`config` · `startup / pack discovery`

This file is a small manifest file: it tells the UFO system which extension bundles are available for DSQA evaluation work. A pack is a packaged configuration that names a version and a set of extensions to load. Without this file, the system would not have these named DSQA bundles to discover and use.

The file builds the packs in layers. The core pack includes the basic pieces: a default index, OpenAI-style embedding support, and OpenRouter model access. The search pack starts with those same basics and adds tools for web-style search and research. The browser pack builds on the search pack and adds browser automation, including a Chrome sandbox. This is like offering three travel kits: a basic kit, a kit with maps and guidebooks, and a full kit with a vehicle included.

The three functions are simple factory functions. Each one creates and returns a `Pack`, which is the object the wider system can read to learn the pack’s name, version, and extension list. The important behavior is the tiering: higher-capability packs include everything from the lower tiers, so users can choose the smallest pack that fits their needs.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the smallest DSQA pack, named `dsqa_core`. Someone would use this when they only need the basic indexing, embedding, and model-provider extensions.

**Data flow**: It reads the file’s constants for the core pack name, shared version, and core extension list. It puts those values into a new `Pack` object and returns that object to the caller.

**Call relations**: When the wider pack-loading system asks for the core DSQA bundle, this function constructs it by calling `Pack.__init__`. It does not hand off to other local logic; its job is simply to package the core settings into the standard `Pack` shape.

*Call graph*: 1 external calls (__init__).


##### `search_pack`  (lines 17–18)

```
def search_pack() -> Pack
```

**Purpose**: Creates the DSQA search pack, named `dsqa_search`. This is for runs that need the core tools plus search and research-related extensions.

**Data flow**: It reads the search pack name, shared version, and search extension tuple. It uses those values to create a `Pack` object, then returns the finished pack.

**Call relations**: When something needs the search-enabled DSQA bundle, this function is called to produce it. It calls `Pack.__init__` to turn the configured name, version, and extensions into the standard pack object the rest of the system understands.

*Call graph*: 1 external calls (__init__).


##### `browser_pack`  (lines 21–22)

```
def browser_pack() -> Pack
```

**Purpose**: Creates the largest DSQA pack, named `dsqa_browser`. This is for workflows that need search tools plus browser and sandboxed Chrome capabilities.

**Data flow**: It reads the browser pack name, shared version, and browser extension list, which includes all search extensions plus browser-related additions. It creates a `Pack` object from those values and returns it.

**Call relations**: When the system needs the browser-capable DSQA bundle, this function supplies it. It calls `Pack.__init__` to build the pack object, giving the rest of the system a clear list of everything that must be loaded for browser-based evaluation.

*Call graph*: 1 external calls (__init__).


### `packs/gdpval_eval/ufo_pack_gdpval_eval.py`

`config` · `pack discovery and configuration load`

This file is like a menu for a toolkit. A “pack” is a named bundle of extensions, where an extension is an add-on capability such as document handling, web research, browser use, or access to a model provider. Instead of making every GDPVal evaluation run load every possible tool, this file offers four clear combinations.

The shared starting point is the base set: indexing, OpenAI embeddings, and OpenRouter access. The document pack adds tools for working with documents, running a REPL-style interactive environment, and coding. The research pack adds web and research tools, including Exa, browser support, and a Chrome sandbox. The full pack combines both document and research capabilities.

Each function builds and returns a `Pack` object from `ufo.sdk.manifest`. That object is the system’s formal record of “this pack has this name, this version, and these extensions.” Without this file, the GDPVal evaluation setup would not have these ready-made capability bundles, so users or automation would need to assemble the right extension lists by hand, which is more error-prone.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the smallest GDPVal pack. Someone would use it when they only need the basic shared capabilities: indexing, embeddings, and OpenRouter model access.

**Data flow**: It starts with the fixed core pack name, the shared version number, and the base extension list. It passes those into `Pack`, which turns them into a pack manifest object. The result is a ready-to-load description of the core GDPVal setup.

**Call relations**: When the system or a user asks for the core GDPVal pack, this function is the recipe that builds it. Its only handoff is to `Pack`, which packages the name, version, and extension list into the standard form the rest of UFO understands.

*Call graph*: 1 external calls (__init__).


##### `documents_pack`  (lines 17–22)

```
def documents_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack aimed at document-heavy work. It includes the basic tools plus extra support for documents, interactive work, and coding.

**Data flow**: It takes the base extension list and adds the document-focused extensions to it. It then combines that extension list with the document pack name and version, and gives all of that to `Pack`. The output is a pack manifest for document-oriented GDPVal evaluations.

**Call relations**: When a document-focused GDPVal setup is requested, this function assembles the correct bundle. It relies on `Pack` to convert the chosen name, version, and extensions into the manifest object used by the wider system.

*Call graph*: 1 external calls (__init__).


##### `research_pack`  (lines 25–30)

```
def research_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack aimed at research and web-based investigation. It includes the basic tools plus search, research, browser, and sandboxed browser capabilities.

**Data flow**: It starts with the shared base extensions, adds the research-specific extensions, and attaches the research pack name and version. It passes that information into `Pack`, which returns a manifest describing this research-capable bundle.

**Call relations**: When the GDPVal evaluation needs online research tools rather than document tools, this function provides the pack recipe. It hands the final list of capabilities to `Pack` so the rest of the system can load them in a standard way.

*Call graph*: 1 external calls (__init__).


##### `full_pack`  (lines 33–38)

```
def full_pack() -> Pack
```

**Purpose**: Creates the most complete GDPVal pack. It combines the base tools, document tools, and research tools into one larger bundle.

**Data flow**: It gathers all three extension groups: base, document, and research. It pairs the combined list with the full pack name and version, then passes everything to `Pack`. The result is a manifest for loading the full GDPVal evaluation toolkit.

**Call relations**: When a run needs every GDPVal capability offered here, this function builds that all-in-one pack. Like the other pack functions, it delegates the final manifest construction to `Pack`, which is the common format expected elsewhere in the system.

*Call graph*: 1 external calls (__init__).


### Sample and YC packs
These files cover a minimal SDK-backed sample pack and the YC founder pack package plus manifest.

### `packs/sample_pack/ufo_pack_sample.py`

`config` · `pack discovery and onboarding`

This is a conformance sample pack: a deliberately simple pack that acts like a test plug-in for the larger system. Its job is to exercise the “pack seam,” meaning the boundary where outside pack authors connect their work to the core product. The important rule is that it imports only from `ufo.sdk`, the public interface that pack authors are supposed to use. If this file stops working, it suggests that real external packs may also be broken.

The file names the pack, gives it a version, says which extension it bundles, points to one skill directory, and defines one onboarding step. An onboarding step is setup work that runs when the pack is activated, like writing a note saying “this pack has been initialized.” Here, that note is stored through the extension context’s store, which is durable storage rather than a fake test log.

The `pack()` function is the entry point the system looks for. It builds and returns a `Pack` object that says: include the bundled `sample` extension, include this skill, and run this setup function as an onboarding step. In everyday terms, the file is a small shipping label plus a setup checklist for the sample pack.

#### Function details

##### `_setup`  (lines 25–26)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: This asynchronous setup step records that the sample pack’s onboarding has run. It writes a small marker into the pack’s scoped store so later checks can confirm the setup happened through the real storage path.

**Data flow**: It receives an `ExtensionContext`, which is the pack’s doorway into services such as storage. It writes the key `pack:onboarded` with the value `{"pack_onboarded": true}` into that store. It returns nothing, but it changes durable stored state by leaving behind that onboarding marker.

**Call relations**: This function is handed to the onboarding system by `pack()` as the handler for the pack’s setup step. When the pack is activated and onboarding runs, the system calls this function to make the visible, testable record that onboarding completed.


##### `pack`  (lines 29–36)

```
def pack() -> Pack
```

**Purpose**: This is the public entry point for the sample pack. It builds the `Pack` description that tells the system what the pack is called, what extension it includes, what skill it contributes, and what onboarding step should run.

**Data flow**: It reads the constants defined in this file, such as the pack name, version, bundled extension name, skill path, and onboarding name. It wraps the skill path in a `SkillSpec`, wraps the setup function in an `OnboardingStep`, then returns a `Pack` containing all of that information. Nothing is written to storage here; it only describes what the pack contains.

**Call relations**: The pack-loading code calls this function when it discovers this installed pack. Inside, it creates the `SkillSpec`, `OnboardingStep`, and `Pack` objects that the rest of the system uses to activate the bundled extension, expose the skill, and later call `_setup` during onboarding.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `packs/yc/ufo_pack_yc/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder with an `__init__.py` file is treated as an importable package, a bit like putting a label on a drawer so the rest of the program knows it can look inside. Here, that means code elsewhere can refer to `packs.yc.ufo_pack_yc` and then import the real modules that live under this package. Because the file is empty, it does not run setup code, create shortcuts, or expose special names. Its value is structural: without it, some Python environments or tooling might not recognize this directory as a package, which could make imports fail or make the project harder to navigate.


### `packs/yc/ufo_pack_yc/manifest.py`

`config` · `config load`

This file works like the label and packing list on a box. The box is a UFO pack aimed at YC-style founder work, and the manifest says what is inside and what other pieces must be available for it to work. It defines the pack name, its version, a list of required extensions, and the local folder where its skills live. The required extensions include things like command-line support, memory, document handling, scheduled tasks, todos, embeddings, and a knowledge graph. In plain terms, those are the supporting tools the pack expects to have available. The file also names two skills: founder operations and company diligence. Instead of loading those skills directly here, it records their paths so the wider UFO system can discover and load them later. Without this file, the system would not have a simple, standard way to identify this pack or know which capabilities and skill directories should be connected when the pack is installed or started.

#### Function details

##### `pack`  (lines 24–30)

```
def pack() -> Pack
```

**Purpose**: This function builds and returns the pack description that the UFO system can read. Someone would use it when they want to register or load the YC founder pack with its name, version, required extensions, and skill locations.

**Data flow**: It starts with the constants defined in this file: the pack name, version, extension names, the skills folder path, and the list of skill names. It turns each skill name into a full folder path and wraps that path in a SkillSpec, which is a small description of a skill location. It then returns a Pack object containing all of that information.

**Call relations**: When the broader pack-loading code asks this manifest what it provides, this function is the answer. It hands the gathered information to Pack to create the overall pack record, and it hands each skill folder path to SkillSpec so the system has a clear pointer to each skill that should be included.

*Call graph*: 2 external calls (__init__, __init__).
