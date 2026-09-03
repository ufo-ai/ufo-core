# Pack composition  `stage-2.1`

Pack composition is shared setup support. It decides which ready-made groups of features the system should load together, like choosing a toolbox before starting a job. A “pack” is just a named bundle of extensions, skills, prompt instructions, and onboarding steps, so users do not have to list each piece by hand.

The built-in assistant pack turns on the normal assistant extensions. The assistant billing pack adds billing support for local development, so developers can test the billing flow without using the full hosted setup. The assistant hosted pack is the production-style bundle for managed infrastructure, including its needed extensions, skills, and instructions. The assistant eval pack adapts the normal assistant for testing: it removes real outside broker connections and adds safe evaluation tools such as fake environments and Docker sandbox support.

The DSQA and GDPVal eval pack files define several evaluation tool combinations, from basic to search or browser enabled. The sample pack is a small proof that external users can define packs through the public SDK.

## Files in this stage

### Assistant bundles
Defines the standard assistant feature bundle and its local billing and hosted deployment variants.

### `packs/assistant_dev/ufo_pack_assistant.py`

`config` · `config load / startup`

This file is like a packing list for a complete assistant setup. When someone chooses the pack named “assistant” in configuration, the system uses this file to know exactly which capabilities should be included. Those capabilities include memory, search and research tools, browser and coding tools, connectors to outside services, scheduled tasks, document generation, member-facing apps, feature flags, and several model providers.

The important idea is that the pack itself is not the assistant’s brain and does not implement the tools directly. Instead, it names many separate extensions. Each extension brings its own tools, setup, and user-facing behavior. This file simply groups them so they start together as one coherent product.

It also distinguishes this pack from a hosted version: this assistant pack runs using the core system’s own local carrier and index, rather than relying on managed infrastructure. In everyday terms, it is the “local full assistant bundle.” Without this file, users would have to manually enable a long list of extensions, and it would be easier to miss one or assemble an inconsistent assistant setup.

#### Function details

##### `pack`  (lines 68–69)

```
def pack() -> Pack
```

**Purpose**: This function builds and returns the pack definition for the assistant bundle. The rest of the system can call it to discover the pack’s name, version, and the full list of extensions that should be activated.

**Data flow**: It reads the file’s constants: the pack name, version, and extension list. It puts those values into a Pack object, which is the system’s standard container for a named feature bundle, and returns that object to the caller.

**Call relations**: When the pack system loads available packs, it calls this function to get the assistant pack’s manifest. The function hands the collected values to Pack.__init__, which turns the plain constants into a structured pack definition the wider system can use during startup.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_billing/ufo_pack_assistant_billing.py`

`config` · `config load`

The project uses “packs” as named bundles of features that can be turned on together. This file defines one such bundle: `assistant_billing`. It starts with everything from the regular local assistant pack, then adds `metronome`, the billing/usage-tracking integration. The reason this separate pack exists is practical and safety-related. The normal local assistant pack does not include billing, because developers should not accidentally send usage data to a billing vendor. The hosted assistant pack does include billing, but also includes other hosted services that are not useful on a laptop. This pack fills the gap: it lets someone run the local assistant while still proving that the “Set up billing” path works. In everyday terms, it is like a special test kit: mostly the same tools as local development, with one extra billing tool included. Because enabling it can talk to real external billing systems, it is opt-in. A developer must select the pack by name and provide safe test credentials, such as a Metronome sandbox token and a Stripe test-mode key. The file itself is intentionally small: it names the pack, gives it a version, builds its extension list, and exposes a `pack()` function that returns the pack description to the wider system.

#### Function details

##### `pack`  (lines 24–25)

```
def pack() -> Pack
```

**Purpose**: This function builds and returns the manifest object for the `assistant_billing` pack. The rest of the system uses that manifest to know the pack’s name, version, and which feature extensions to enable.

**Data flow**: It reads the constants defined in this file: the pack name, version, and combined extension list. It passes those values into `Pack`, which creates a structured pack description. The result is returned to the caller; the function does not change files, databases, or external services by itself.

**Call relations**: When the pack system loads this file, it calls `pack` to ask, “What does this pack contain?” The function hands the details to `Pack.__init__`, which turns them into the standard manifest object that the rest of the startup/configuration process can use.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_hosted/ufo_pack_assistant_hosted.py`

`config` · `config load`

A “pack” is like a preset configuration for the assistant. Instead of making an operator choose dozens of separate features by hand, this file names one bundle: assistant_hosted. When that pack is active, the assistant gains many hosted-product capabilities, such as memory, web research, Slack and iMessage surfaces, browser tools, document generation, scheduled tasks, coding tools, billing metering, feature flags, and hosted backends like Turbopuffer, Redis, E2B, and Browserbase.

The file is mostly declarative, meaning it describes what should exist rather than doing the work itself. It sets the pack name and version, lists the extensions to enable, points to the included skill folder, and adds one important prompt section. That prompt section tells the assistant that when a paying customer asks about ufo itself — billing, Slack setup, teammates, missing features, and similar product questions — it should first consult the shipped customer onboarding skill. This prevents the assistant from guessing or relying on a customer workspace’s private memory for product facts.

The key result is the pack() function, which packages all these choices into a Pack object the wider system can load during configuration. Without this file, the hosted assistant would not have a single clear recipe for which hosted services and customer-support knowledge should be enabled together.

#### Function details

##### `pack`  (lines 96–103)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the Pack object for the hosted assistant preset. The rest of the system uses this object to know the pack’s name, version, enabled extensions, included skills, and extra prompt instructions.

**Data flow**: It starts with constants defined in this file: the pack name, version, extension names, skill directory, skill names, and customer-support prompt section. It turns each skill name into a SkillSpec pointing at that skill’s folder, then places everything into a Pack. The output is a complete Pack object; it does not directly start any services itself.

**Call relations**: When the pack system loads this file, it calls pack to get the hosted assistant recipe. Inside that call, it creates SkillSpec entries for the bundled customer onboarding skill and then creates the Pack object that carries those skill specs, the extension list, and the prompt section onward to the broader configuration loader.

*Call graph*: 2 external calls (__init__, __init__).


### Evaluation bundles
Defines assistant, DSQA, and GDPVal evaluation packs that swap in controlled tools and capability-specific extension sets.

### `packs/assistant_eval/ufo_pack_assistant_eval.py`

`config` · `startup`

A “pack” is a named bundle of extensions that the system can load. This file creates a safer, more predictable bundle for automated evaluations. In a normal product setting, the assistant pack may include real broker providers such as Composio or Pipedream, which connect to outside services and require real credentials. In an evaluation run, those credentials usually do not exist, and exposing those providers would waste the agent’s time or make tests depend on unavailable services.

So this file builds an evaluation version of the assistant pack. It copies the normal assistant extensions, filters out the real broker extensions, then adds two evaluation-specific extensions: `eval_env`, which provides deterministic fake workplace connectors for tests, and `docker`, which lets the sandbox run a conversation’s `/workspace` as a real mounted directory. In plain terms, it swaps out “real-world doors” for controlled test doors, and adds the machinery needed to run code in an isolated Docker workspace.

The important detail is that tool registry entries may be visible even when a user has not granted access. Because of that, fake providers must live only in this evaluation pack, not in the real product pack, and real broker providers are deliberately kept out of this evaluation pack.

#### Function details

##### `pack`  (lines 26–27)

```
def pack() -> Pack
```

**Purpose**: Creates the pack manifest for the evaluation assistant setup. Other parts of the system use this to discover the pack name, version, and exact list of extensions to load.

**Data flow**: It reads the constants defined in this file: the pack name, version, and filtered extension list. It puts those values into a `Pack` object, which is the system’s standard description of a loadable pack, and returns that object without changing anything else.

**Call relations**: When the pack system asks this module what it provides, `pack` builds and returns the manifest. It hands the name, version, and extension list to `Pack.__init__`, so the broader loader can later activate the evaluation-only extension set instead of the normal assistant pack.

*Call graph*: 1 external calls (__init__).


### `packs/dsqa_eval/ufo_pack_dsqa_eval.py`

`config` · `startup`

This file is like a small menu of preset toolkits for DSQA evaluation. DSQA likely means a document or data question-answering evaluation workflow, and each pack describes what supporting abilities should be available for that workflow. Instead of making users remember a long list of extension names, the file gives them three clear choices.

The smallest option, the core pack, includes basic indexing, OpenAI-based embeddings, and OpenRouter access. In plain terms, that means it can organize information, turn text into searchable numeric representations, and talk to language models through OpenRouter. The search pack builds on that by adding Perplexity and research extensions, so it can do broader information lookup. The browser pack builds on the search pack again by adding browser and sandboxed Chrome support, so it can interact with web pages in a controlled browser environment.

Each function returns a `Pack`, which is a manifest object: a simple description of a bundle, with a name, a version, and a list of extensions. If this file were missing, other parts of the system would not have these convenient, named DSQA evaluation configurations to request.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the basic DSQA pack. Someone would use this when they need the core evaluation setup without extra web search or browser tools.

**Data flow**: It reads the shared version number, the core pack name, and the core extension list from this file. It passes those values into `Pack`, which turns them into a pack manifest object. The result is a `Pack` named `dsqa_core` with the base extensions attached.

**Call relations**: When another part of the system asks for the core DSQA configuration, this function is the small factory that builds it. Its only handoff is to `Pack.__init__`, which receives the name, version, and extensions and creates the manifest object.

*Call graph*: 1 external calls (__init__).


##### `search_pack`  (lines 17–18)

```
def search_pack() -> Pack
```

**Purpose**: Creates a DSQA pack with search and research abilities added. Someone would use this when answers may need outside lookup rather than only the core indexing and model tools.

**Data flow**: It reads the shared version number, the search pack name, and the search extension list. That list includes the base extensions plus Perplexity and research support. It passes everything into `Pack`, and the output is a `Pack` named `dsqa_search`.

**Call relations**: When the system needs the search-enabled DSQA setup, this function supplies it. It calls `Pack.__init__` to package the chosen extension list into the standard manifest form used elsewhere.

*Call graph*: 1 external calls (__init__).


##### `browser_pack`  (lines 21–22)

```
def browser_pack() -> Pack
```

**Purpose**: Creates the most capable DSQA pack in this file, adding browser automation on top of search and research tools. Someone would use this when evaluation may require opening or interacting with web pages.

**Data flow**: It reads the shared version number, the browser pack name, and the browser extension list. That list includes all search-pack extensions plus browser and sandboxed Chrome support. It gives these values to `Pack`, which returns a `Pack` named `dsqa_browser`.

**Call relations**: When another part of the system wants a DSQA setup that can browse the web, this function builds that manifest. Like the other pack functions, it hands the final name, version, and extension list to `Pack.__init__` so the rest of the system receives a normal `Pack` object.

*Call graph*: 1 external calls (__init__).


### `packs/gdpval_eval/ufo_pack_gdpval_eval.py`

`config` · `config load`

This file is like a menu of tool bundles for GDPVal evaluation work. A “pack” is a small manifest object that names a versioned collection of extensions. An extension is an add-on capability, such as document handling, browser research, or access to a model provider. Without this file, someone setting up GDPVal evaluation would need to remember and repeat the exact extension combinations every time, which is easy to get wrong.

The file starts by naming one shared version and several groups of extensions. The base group is included in every pack and gives the common foundation: default indexing, OpenAI embeddings, and OpenRouter model access. On top of that, there is a document-focused group for working with files, running a REPL, and coding, and a research-focused group for web or browser-assisted research tools.

The four functions then create different Pack objects. The core pack includes only the base tools. The documents pack adds document and coding support. The research pack adds research and browsing support. The full pack combines everything. This lets users choose the smallest tool bundle that fits the task, much like choosing a basic, office, research, or all-inclusive toolkit.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the smallest GDPVal pack, containing only the shared base extensions. Use this when the evaluation setup needs the common foundation but not document tools or research/browser tools.

**Data flow**: It takes no input from the caller. It reads the file’s shared pack name, version, and base extension list, then builds a Pack object from them. The result is a versioned pack named for the GDPVal core setup.

**Call relations**: When pack discovery or configuration code asks for the core GDPVal bundle, this function creates it. Its only handoff is to Pack.__init__, which turns the name, version, and extension list into the manifest object the rest of the system can use.

*Call graph*: 1 external calls (__init__).


##### `documents_pack`  (lines 17–22)

```
def documents_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack for document-heavy work. It includes the base tools plus extensions for documents, interactive code execution, and coding support.

**Data flow**: It takes no caller input. It combines the shared base extension list with the document extension list, then passes that combined list along with the documents pack name and version into a new Pack object. The output is a ready-to-use document-oriented pack manifest.

**Call relations**: When the system or a user wants GDPVal evaluation with document support, this function supplies that configured bundle. It delegates the actual Pack object creation to Pack.__init__, giving it the combined extension list.

*Call graph*: 1 external calls (__init__).


##### `research_pack`  (lines 25–30)

```
def research_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack for research-oriented work. It includes the base tools plus extensions for search, research workflows, browser use, and a Chrome sandbox.

**Data flow**: It receives no input from the caller. It joins the base extensions with the research extensions, then uses those values, the research pack name, and the shared version to create a Pack object. The returned value is the research-focused pack manifest.

**Call relations**: When GDPVal evaluation needs web research or browser-assisted capabilities, this function provides the matching bundle. It calls Pack.__init__ to package the selected name, version, and extensions into the standard manifest form.

*Call graph*: 1 external calls (__init__).


##### `full_pack`  (lines 33–38)

```
def full_pack() -> Pack
```

**Purpose**: Creates the most complete GDPVal pack. It includes the base tools, document and coding tools, and research/browser tools all together.

**Data flow**: It takes no caller input. It combines all three extension groups in order: base, document, and research. It then creates and returns a Pack object using the full pack name, the shared version, and the full extension list.

**Call relations**: When someone wants the all-inclusive GDPVal evaluation setup, this function builds that complete bundle. It hands the final name, version, and extension collection to Pack.__init__, which produces the manifest used by the wider pack system.

*Call graph*: 1 external calls (__init__).


### Sample pack
Provides a minimal public-SDK pack that demonstrates bundling an extension, skill, and onboarding step.

### `packs/sample_pack/ufo_pack_sample.py`

`config` · `pack discovery and onboarding`

This is a conformance sample pack: a deliberately small pack that exercises the same path a real outside pack would use. It only imports from `ufo.sdk`, which is the public surface that external pack authors are expected to rely on. That matters because the project can use this file as an early warning system: if internal changes break the public pack interface, this sample pack will fail.

The file names the pack, gives it a version, points to a bundled extension called `sample`, and points to a skill directory on disk. Its `pack()` function builds and returns a `Pack` object, which is like a shipping label saying what this pack contains. That label includes the bundled extension, one skill, and one onboarding step.

The onboarding step runs `_setup`. Instead of merely printing a message or writing to a fake test log, `_setup` stores a real value through the extension context’s scoped store. A scoped store is durable storage reserved for this pack or extension, so the system can later read back what happened through the same public API. In plain terms, the pack leaves a signed receipt saying, “I was onboarded.” If this receipt cannot be written or read, the pack seam is broken.

#### Function details

##### `_setup`  (lines 25–26)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: This is the onboarding action for the sample pack. It records, in the pack’s durable store, that the pack setup ran successfully.

**Data flow**: It receives an `ExtensionContext`, which is the pack’s doorway into approved services such as storage. It writes the key `pack:onboarded` with the value `{"pack_onboarded": true}` into that store. It does not return a value; the important result is the stored record.

**Call relations**: The `pack` function attaches `_setup` to an `OnboardingStep`. Later, when the pack’s onboarding step is run, the system calls `_setup`; `_setup` then hands the proof of onboarding to the context store so conformance checks can read it back.


##### `pack`  (lines 29–36)

```
def pack() -> Pack
```

**Purpose**: This is the pack entry point. It tells the host system what the sample pack is called, what version it is, what extension it bundles, what skill it contributes, and what onboarding step should run.

**Data flow**: It reads the constants defined at the top of the file, including the pack name, version, bundled extension name, skill directory, and onboarding step name. It wraps the skill path in a `SkillSpec`, wraps `_setup` in an `OnboardingStep`, and returns a `Pack` object containing all of that information.

**Call relations**: The pack loader calls `pack` when it discovers this installed pack. Inside, `pack` builds the pieces the loader needs by creating a `SkillSpec`, an `OnboardingStep`, and finally a `Pack`, then hands that completed description back to the host system.

*Call graph*: 3 external calls (__init__, __init__, __init__).
