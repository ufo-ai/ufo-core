# Pack Definitions and Evaluation Bundles  `stage-3.3`

This stage is behind-the-scenes setup support. It defines “packs,” which are named bundles of features the system can turn on together, like choosing a preset mode instead of listing every part by hand. These files do not run the assistant’s main conversation loop. They tell startup and evaluation code what capabilities, tools, services, and test connectors belong in each mode.

The local assistant pack groups the normal assistant features for development. The assistant billing pack adds Metronome billing support on top of local assistant behavior so developers can test billing setup without using the full hosted deployment. The hosted assistant pack describes the production-style bundle, including hosted infrastructure and back-end services. The assistant evaluation pack starts from the normal assistant setup, removes real external broker pieces that would break in tests, and adds evaluation tools such as a Docker sandbox. The DSQA and GDPVal files define ready-made evaluation bundles for different test needs, from small core setups to search, browser, document, research, or all-in configurations. The sample pack is a working public SDK example that proves packs can add an extension, a skill, and an onboarding step.

## Files in this stage

### Local Assistant Packs
Local assistant pack definitions bundle core assistant capabilities and the local billing-enabled variant for development workflows.

### `packs/assistant_billing/ufo_pack_assistant_billing.py`

`config` · `startup / pack selection`

A “pack” here is a named bundle of features that the system can turn on together. The regular local assistant pack does not include billing, because a normal developer setup should not accidentally send usage information to a billing provider. The hosted assistant pack does include billing, but it also includes other hosted services that are not useful on a laptop. This file creates the middle option: assistant plus billing only.

It imports the existing assistant pack and reuses its list of assistant skills. Then it adds one extra extension, `metronome`, which is the billing integration used to report usage and seats. The pack is named `assistant_billing`, so a developer can choose it explicitly through configuration, such as an environment-driven Docker Compose setup.

The important safety idea is that billing is opt-in. Choosing this pack means the local system may try to talk to Metronome and Stripe, so it needs real sandbox or test-mode credentials. Without this file, the local assistant could show a billing setup action but would not have the billing pieces needed to exercise that action end to end.

#### Function details

##### `pack`  (lines 24–33)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the pack definition for `assistant_billing`. Someone would use this when the system is loading available packs and needs to know which extensions and skills belong to this opt-in billing-capable assistant setup.

**Data flow**: It starts with the constants in this file, plus the assistant pack’s existing skill folder and skill names. It creates one skill description for each assistant skill, combines the assistant extensions with the extra `metronome` extension, and returns a complete `Pack` object containing the pack name, version, extensions, and skills.

**Call relations**: When the pack loader asks this module for its pack, `pack` assembles the answer. It hands each skill path to `SkillSpec` so the system can recognize that skill, then hands the full set of details to `Pack` so the rest of startup can enable the assistant features together with billing.

*Call graph*: 2 external calls (__init__, __init__).


### `packs/assistant_dev/ufo_pack_assistant.py`

`config` · `startup/config load`

A “pack” is like a preset or starter kit. Instead of asking someone to enable dozens of assistant features one by one, this file names the full set that should come up together when the assistant pack is selected. Those features include chat-style apps, memory, research, browser and sandbox tools, connectors to outside services, document and site creation, scheduled tasks, coding help, feature flags, model providers, and debugging tools.

The file is deliberately simple. It sets a pack name, a version, and a fixed list of extension names. Each extension is responsible for bringing its own tools, prompts, pages, or setup instructions. This pack mainly says, “these are the pieces that make up the local assistant experience.”

It also points at a local skills folder and includes a small named skill, “first-run”, which likely helps with initial setup or onboarding. The `pack()` function then packages all of this into a `Pack` object that the larger system can load.

Without this file, selecting the assistant pack would not tell the system what to activate. A deployment would either miss major features or require the same long list to be repeated somewhere else.

#### Function details

##### `pack`  (lines 71–77)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the complete description of the assistant pack. The system uses this when it needs a single object saying the pack’s name, version, enabled extensions, and included skills.

**Data flow**: It reads the constants defined in this file: the pack name, version, extension list, skills directory, and skill names. It turns each skill name into a `SkillSpec`, which is a small description of where that skill lives on disk, then puts everything into a `Pack` object. The result is a ready-to-load pack definition; it does not directly start the extensions itself.

**Call relations**: When the pack loader asks this module for its pack definition, this function creates the needed objects. It calls `SkillSpec.__init__` to describe each skill path, then calls `Pack.__init__` to assemble the final pack record that the rest of the system can activate.

*Call graph*: 2 external calls (__init__, __init__).


### Assistant Deployment Variants
Assistant-specific variants adapt the core assistant bundle for evaluation runs and hosted production-style deployments.

### `packs/assistant_eval/ufo_pack_assistant_eval.py`

`config` · `config load`

This file is a small registration file for an evaluation version of the assistant tool pack. A “pack” is a named bundle of extensions that the system can load from configuration. Here, the pack is meant for test or evaluation runs, not normal product use.

The main problem it solves is keeping eval runs realistic but controlled. The regular assistant pack may include real broker integrations such as Composio and Pipedream. In an evaluation environment, those services usually do not have real keys or accounts. If they stayed enabled, the assistant might see fake-looking tools, try to use them, and waste turns failing. So this file filters those real broker extensions out.

It then adds eval-specific extensions: `eval_env`, which represents the deterministic evaluation workplace, and `docker`, which lets an eval run use Docker as the sandbox backend. In plain terms, Docker provides an isolated workspace, like giving each conversation its own temporary workbench. The comment notes that this allows each conversation’s `/workspace` to be a real bind-mounted directory, rather than a command-line path that has to be rewritten.

Nothing expensive happens just by importing this file. It only declares the pack name, version, and extension list. The system uses it when configuration asks for `[pack] name = "assistant_eval"`.

#### Function details

##### `pack`  (lines 26–27)

```
def pack() -> Pack
```

**Purpose**: Creates and returns the manifest object for the evaluation assistant pack. The system uses this to learn the pack’s name, version, and which extensions should be available during eval runs.

**Data flow**: It takes no caller-provided input. It reads the constants defined in this file: the pack name, version, and prepared extension list. It passes those values into `Pack` and returns the resulting pack description object.

**Call relations**: When the pack registry or loader asks this module for its pack definition, `pack` builds the `Pack` object. Its only handoff is to `Pack.__init__`, which turns the plain name, version, and extension list into the standard manifest shape the rest of the system expects.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_hosted/ufo_pack_assistant_hosted.py`

`config` · `startup/config load`

A “pack” is like a pre-built kit for running the assistant in a certain way. This file describes the kit for hosted customers, where many services are provided by managed cloud backends instead of local or self-run pieces. Without this file, the system would not know what belongs in the hosted assistant experience: Slack and iMessage surfaces, browser tools, memory search through Turbopuffer, hosted browser sessions, code execution, document generation, scheduled tasks, billing and metering, feature flags, and many other extensions.

The file starts by naming and versioning the pack. It then defines a special prompt section called `customers`, which gives the assistant an important instruction: when a user asks about ufo itself, such as billing, Slack setup, sign-in, seats, or product limits, the assistant should first consult the bundled `customer-onboarding-help` skill. This matters because a new hosted workspace may not yet have its own memory about ufo’s onboarding details.

The file also lists all extensions that should be active in this hosted setup. Finally, it declares which skills are included: one hosted-specific skill, plus the shared skills from the base assistant pack. The result is a complete manifest that other parts of the system can load when starting a hosted assistant workspace.

#### Function details

##### `pack`  (lines 99–112)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the pack description for the hosted assistant. Other code uses this to learn the pack’s name, version, enabled extensions, bundled skills, and extra prompt instructions.

**Data flow**: It reads the constants defined in this file, such as the pack name, version, extension list, hosted skill folder, and customer-support prompt section. It also reads the shared skill names and skill folder from the base assistant pack. It turns those skill paths into `SkillSpec` entries, then returns a `Pack` object containing the full hosted assistant configuration.

**Call relations**: When the pack system needs to activate `assistant_hosted`, it calls `pack` to get the manifest. Inside that moment, this function creates skill specifications for the hosted-specific skill and the shared assistant skills, then hands everything to `Pack` so the rest of the system can load the requested extensions and prompts.

*Call graph*: 2 external calls (__init__, __init__).


### Evaluation Bundles
Evaluation pack modules provide named extension bundles for DSQA and GDPVal benchmark configurations.

### `packs/dsqa_eval/ufo_pack_dsqa_eval.py`

`config` · `startup/config selection`

This file is like a small menu of preset toolkits. A Pack is a manifest object: a simple description of a named bundle, its version, and which extensions should be turned on. The file creates three bundles for different levels of capability.

The smallest bundle is the core DSQA pack. It includes the shared base extensions: a default index, OpenAI-style embedding support, and OpenRouter model access. The search pack builds on that by adding extensions for Perplexity and research workflows. The browser pack builds on the search pack again by adding browser automation and a Chrome sandbox, so it can support tasks that need web interaction.

The important idea is layering. Each larger pack includes everything from the smaller one, plus extra tools. That avoids copy-and-paste mistakes and makes the intended progression clear: core abilities first, then search, then full browser use. If this file were missing, users or automation would have to manually assemble these extension lists, which would be easy to get wrong and harder to keep consistent as the project changes.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the basic DSQA pack. Someone would use this when they want the minimum DSQA evaluation setup without web search or browser automation.

**Data flow**: It starts with the fixed core name, version number, and base extension list defined at the top of the file. It gives those values to the Pack constructor, which turns them into a Pack object. The result is returned to the caller as a reusable description of the core setup.

**Call relations**: When some higher-level code wants the core DSQA configuration, it calls this function. This function then hands the name, version, and extension list to Pack.__init__ so the SDK can create the actual manifest object.

*Call graph*: 1 external calls (__init__).


##### `search_pack`  (lines 17–18)

```
def search_pack() -> Pack
```

**Purpose**: Creates a DSQA pack with search and research abilities included. Someone would use this when evaluation needs outside information retrieval, not just the core indexing and model tools.

**Data flow**: It reads the preset search pack name, shared version, and search extension list. That list already includes the base extensions, plus search-focused additions. It passes these values into Pack and returns the completed Pack object.

**Call relations**: When code needs the search-enabled DSQA setup, it calls this function instead of building the list by hand. The function delegates the actual object creation to Pack.__init__, giving it the prepared search configuration.

*Call graph*: 1 external calls (__init__).


##### `browser_pack`  (lines 21–22)

```
def browser_pack() -> Pack
```

**Purpose**: Creates the most capable DSQA pack, including search plus browser automation. Someone would use this when evaluation tasks may need to open pages or interact with a sandboxed Chrome browser.

**Data flow**: It takes the browser pack name, shared version, and the largest extension list from this file. That list includes the base tools, the search and research tools, and the browser-related tools. It passes all of that into Pack and returns the resulting Pack object.

**Call relations**: When code needs the full browser-capable DSQA setup, it calls this function. The function packages the prepared browser configuration and hands it to Pack.__init__, which creates the manifest object used by the rest of the system.

*Call graph*: 1 external calls (__init__).


### `packs/gdpval_eval/ufo_pack_gdpval_eval.py`

`config` · `pack discovery and setup`

This file is like a menu of tool bundles. A “Pack” is a manifest object: a small description that says, “install or enable these extensions together under this name and version.” Without this file, someone setting up GDPVal evaluation would need to remember and assemble the right extension lists by hand, which is easy to get wrong.

The file starts by naming shared constants: the version, the basic extensions every pack needs, and extra extension groups for document work and research work. The base group includes indexing, OpenAI embeddings, and OpenRouter model access. The document group adds tools for working with documents, running a REPL-like interactive environment, and coding. The research group adds web and research tools such as Perplexity, browser support, and a Chrome sandbox.

The four functions then build different Pack objects from those ingredients. `core_pack` returns only the basics. `documents_pack` adds document-related tools. `research_pack` adds research and browser tools. `full_pack` combines everything. The important behavior is that these functions do not perform the setup themselves; they return clear package descriptions that another part of the system can use later.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the smallest GDPVal pack. Someone would use this when they only need the shared base capabilities, such as indexing, embeddings, and model routing.

**Data flow**: It reads the fixed core pack name, version, and base extension list from this file. It puts those values into a new `Pack` object. The result is a manifest describing the core GDPVal bundle, with no document or research extras added.

**Call relations**: When pack discovery or setup code asks for the core GDPVal bundle, this function builds it by calling `Pack.__init__`. It hands the resulting Pack object back to the caller so the larger system can register, display, or install that bundle.

*Call graph*: 1 external calls (__init__).


##### `documents_pack`  (lines 17–22)

```
def documents_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack for document-heavy work. It includes the base tools plus document, interactive, and coding-related extensions.

**Data flow**: It reads the document pack name, the shared version, the base extension list, and the document extension list. It combines the base and document extensions into one ordered set of entries, then places them into a new `Pack` object. The output is a manifest for a document-focused GDPVal setup.

**Call relations**: When a caller wants the document-oriented bundle, this function assembles the needed extension list and calls `Pack.__init__`. The returned Pack can then be used by the surrounding pack system to make those tools available together.

*Call graph*: 1 external calls (__init__).


##### `research_pack`  (lines 25–30)

```
def research_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack for research and web-assisted work. It includes the base tools plus research, browser, and sandboxed Chrome extensions.

**Data flow**: It reads the research pack name, the shared version, the base extension list, and the research extension list. It joins the base and research extensions, then uses them to create a `Pack` object. The result is a manifest for a research-focused GDPVal setup.

**Call relations**: When setup code needs the research bundle, this function provides it. It delegates the actual manifest construction to `Pack.__init__`, then returns the completed Pack object to the caller.

*Call graph*: 1 external calls (__init__).


##### `full_pack`  (lines 33–38)

```
def full_pack() -> Pack
```

**Purpose**: Creates the most complete GDPVal pack. It combines the base, document, and research extension groups into one all-in bundle.

**Data flow**: It reads the full pack name, the shared version, and all three extension groups from this file. It combines them into one extension list and passes that list into a new `Pack` object. The output is a manifest describing the complete GDPVal evaluation environment.

**Call relations**: When a caller wants every GDPVal-related capability enabled together, this function is the one it uses. It calls `Pack.__init__` to build the manifest and returns that Pack so the broader system can register or install the full bundle.

*Call graph*: 1 external calls (__init__).


### Sample Pack
The sample pack demonstrates the public SDK pack mechanism with a sample extension, skill, onboarding step, and store write.

### `packs/sample_pack/ufo_pack_sample.py`

`config` · `pack discovery and onboarding`

This is a small but important conformance sample: it acts like an installed third-party pack, but it lives in the project so tests can check the pack boundary end to end. A “pack” is a bundle of extension content that UFO can discover and load. This file only imports from `ufo.sdk`, which is the public surface pack authors are expected to use. That matters because it catches accidental breakage in the public API, not just in internal code.

The file names the pack, gives it a version, says it includes the existing `sample` extension, and points to a skill directory on disk. It also defines an onboarding step. Onboarding is setup work that runs when the pack is activated, like writing an initial note that says “this pack has been set up.” Here, that note is stored through the extension context’s scoped store, which is durable project storage rather than a fake test log. In everyday terms, this pack is a smoke alarm for the plugin doorway: if discovery, manifest creation, skill contribution, onboarding, or persistent pack storage stops working, this sample should fail and reveal the problem.

#### Function details

##### `_setup`  (lines 25–26)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: This is the onboarding action for the sample pack. It records a small marker in the pack’s scoped store so tests can confirm that onboarding really ran and wrote through the same storage path used by normal code.

**Data flow**: It receives an `ExtensionContext`, which is the pack’s doorway to shared services such as storage. It writes the key `pack:onboarded` with the value `{"pack_onboarded": true}` into `ctx.store`. Nothing is returned; the lasting result is the stored record.

**Call relations**: The `pack` function attaches `_setup` to an `OnboardingStep`. Later, when UFO loads and activates this pack, the onboarding system calls `_setup` and the function hands the proof of setup to the context’s store.


##### `pack`  (lines 29–36)

```
def pack() -> Pack
```

**Purpose**: This is the pack entry point UFO calls to learn what this pack contributes. It builds and returns a `Pack` object that describes the bundled extension, the pack-level skill, and the onboarding step.

**Data flow**: It reads the constants in this file, including the pack name, version, bundled extension name, skill path, and onboarding name. It wraps the skill path in a `SkillSpec`, wraps `_setup` in an `OnboardingStep`, and places all of that into a `Pack`. The returned `Pack` is the manifest-like description UFO uses when loading the sample pack.

**Call relations**: During pack discovery, UFO calls `pack` as the public entry point for this module. Inside, it creates the skill and onboarding descriptions, then returns them together as one pack definition so the loader can activate the bundled extension and later run `_setup` during onboarding.

*Call graph*: 3 external calls (__init__, __init__, __init__).
