# Pack recipes  `stage-3.1`

Pack recipes are startup instructions. A pack is a named bundle that tells the system which add-ons, skills, setup steps, and infrastructure choices to load before the assistant starts doing work. They work like preset modes for the same machine.

The local assistant pack turns on the main assistant features for development. The hosted assistant pack chooses the features meant for managed production-style infrastructure. The billing pack is a smaller local recipe that adds billing support so developers can test the billing setup flow without starting the full hosted setup. The assistant evaluation pack swaps in fake evaluation services and Docker sandbox support, while leaving out real external broker connections that would not fit an eval run.

Other packs shape the assistant for specific jobs. The chief of staff pack combines Slack, meetings, memory, todos, scheduling, and improvement skills into a manager-support workflow. The DSQA and GDPVal evaluation packs offer preset toolkits, from minimal core tools to search, browser, document, research, or all-in setups. The sample pack proves the public SDK path works by loading a sample extension, skill, and onboarding step.

## Files in this stage

### Assistant development packs
Local assistant recipes define the core development bundle and the billing-enabled variant for testing setup flows.

### `packs/assistant_billing/ufo_pack_assistant_billing.py`

`config` · `config load`

This file is a small manifest, meaning it tells the system what bundle of features to turn on. The normal local assistant pack does not include Metronome, the outside billing and usage-metering service, because most development work should not send billing data anywhere. The hosted assistant pack does include billing, but it also includes several hosted-only services that are not useful on a laptop. This file fills that gap.

It starts with the regular assistant pack and adds one extra extension: `metronome`. That makes local testing possible for the onboarding action where an owner chooses “Set up billing.” Without this pack, that action could appear in a hosted-style flow but would have no local service behind it to complete the chain.

Because it enables real billing-related shippers, this pack is deliberately opt-in. A developer selects it by setting the pack name to `assistant_billing`, often through the local Docker setup. It is meant to be used with safe test credentials, such as a Metronome sandbox token and a Stripe test-mode key, so developers can prove the billing path works without touching live customer billing.

#### Function details

##### `pack`  (lines 24–25)

```
def pack() -> Pack
```

**Purpose**: This function builds and returns the pack description for the `assistant_billing` bundle. The system uses it to learn the pack’s name, version, and which extensions should be enabled.

**Data flow**: It reads the file’s constants: the pack name, the version, and the extension list made from the normal assistant extensions plus `metronome`. It puts those values into a `Pack` object, which is the structured description the rest of the system expects.

**Call relations**: When the pack system loads this file, it calls `pack` to obtain the manifest. `pack` then hands the name, version, and extensions to `Pack.__init__`, which creates the object that tells the wider startup/configuration process to run the assistant features with billing enabled.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_dev/ufo_pack_assistant.py`

`config` · `config load`

A “pack” is like a pre-packed toolbox. Instead of asking a user or deployer to turn on dozens of separate features one by one, this file gives the system one name, “assistant”, and a fixed list of extensions to load under that name.

The pack described here is for the self-contained local assistant setup. It includes memory, search and research tools, connectors to outside services, browser and computer-use tools in a sandbox, code execution, document generation, todos, objectives, scheduled tasks, member-authored skills, debugging tools, extra model providers, and more. The important idea is that this file does not implement those features itself. It simply says, “When the assistant pack is selected, bring up these extension modules together.”

That makes it a small but important coordination point. Without it, the system would still have the individual extensions, but there would be no single named configuration that reliably activates this exact assistant experience. The file also makes clear that this pack relies on local core infrastructure rather than hosted managed infrastructure, which helps distinguish it from a hosted assistant variant.

#### Function details

##### `pack`  (lines 53–54)

```
def pack() -> Pack
```

**Purpose**: This function builds and returns the pack definition for the local assistant setup. The system can call it to learn the pack’s name, version, and the exact extensions that should be enabled.

**Data flow**: It reads the constants in this file: the pack name, version, and extension list. It passes those values into the Pack object constructor, which packages them into a structured manifest. The result is a Pack object that the rest of the system can use when activating this named configuration.

**Call relations**: When the pack system loads this file, it calls this function to obtain the assistant pack manifest. The function hands the name, version, and extension list to Pack.__init__, so the generic pack machinery can treat this simple Python recipe as a standard pack definition.

*Call graph*: 1 external calls (__init__).


### Assistant evaluation and hosting packs
Evaluation and hosted assistant recipes select the infrastructure, fake services, tools, and built-in skills appropriate to non-local environments.

### `packs/assistant_eval/ufo_pack_assistant_eval.py`

`config` · `startup / pack selection`

This file is a small manifest for an evaluation-only tool pack. A pack is like a labeled toolbox: it tells the system which extensions should be available together under one name. Here, the toolbox is built for automated assistant evaluations, where conversations need predictable fake services such as email, calendar, and code search, plus a Docker sandbox so each run can use a real mounted workspace directory.

The file starts from the normal assistant pack, then removes two real external broker integrations: Composio and Pipedream. Those services need real keys and accounts. In an evaluation run, they would only create misleading dead ends where the assistant wastes steps trying to use tools that cannot actually work. After filtering those out, the file adds the evaluation environment extension and the Docker carrier extension.

This separation matters because tool registry entries may be visible even when the user has not been granted access to them. If fake evaluation tools were mixed into the normal product pack, they could accidentally appear in real deployments. By keeping them in this dedicated `assistant_eval` pack, an eval deployment can opt in explicitly and get exactly the controlled environment it needs.

#### Function details

##### `pack`  (lines 26–27)

```
def pack() -> Pack
```

**Purpose**: This function creates the pack description that the system reads when it wants to load the `assistant_eval` pack. It gives the pack its name, version, and the list of extensions that belong in the evaluation environment.

**Data flow**: It reads the file-level constants for the pack name, version, and extension list. It then packages those values into a `Pack` object, which is the system’s standard way to describe a selectable group of extensions. The result is returned to the caller; the function does not change any outside state.

**Call relations**: When the pack-loading system asks this module what it provides, this function is the answer. It hands the gathered extension list to the `Pack` constructor so the rest of the system can treat this evaluation setup as one named pack.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_hosted/ufo_pack_assistant_hosted.py`

`config` · `config load / startup`

This file is like a packing list for the hosted version of the assistant. Instead of defining the assistant’s behavior directly, it names the pieces that should be assembled: memory, web research, Slack and iMessage surfaces, browser tools, document creation, scheduled tasks, coding tools, metering, hosted sandboxes, hosted browser sessions, and more. Without this file, the system would not know what capabilities belong to the hosted assistant configuration.

The important distinction is that this pack chooses managed backends for some services. For example, it uses Turbopuffer for the memory index, Redis for live frames, E2B for sandboxed code execution, and Browserbase for hosted browser sessions. In plain terms, the assistant is not expected to run all of these heavy services inside its own local environment; it connects to operated services instead.

The file also includes one bundled skill, `customer-onboarding-help`. A skill here is a packaged set of instructions or content the assistant can use. This one contains curated onboarding information for hosted workspaces, so the assistant can answer common setup, billing, Slack install, and invitation questions from shipped content rather than relying on a customer’s own memory store.

#### Function details

##### `pack`  (lines 67–73)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the pack manifest for the hosted assistant. A manifest is a compact description of the pack: its name, version, enabled extensions, and included skills.

**Data flow**: It reads the constants in this file: the pack name, version, list of extension names, the skills folder, and the skill names. It turns each skill name into a `SkillSpec`, which points at that skill’s directory, then places everything into a `Pack` object. The result is a complete description that the wider system can load.

**Call relations**: When the pack-loading system needs to know what the hosted assistant contains, this function is the entry it uses. Inside, it hands the extension list and generated skill specifications to `Pack.__init__`, and it uses `SkillSpec.__init__` to describe each bundled skill by its filesystem path.

*Call graph*: 2 external calls (__init__, __init__).


### Workflow assistant pack
The chief-of-staff recipe assembles communication, memory, scheduling, and productivity skills into a manager-support workflow.

### `packs/chief_of_staff/ufo_pack_chief_of_staff.py`

`config` · `startup`

This file is like the label and packing list on a toolbox. It does not perform the chief-of-staff work itself. Instead, it describes what belongs in this pack so the larger system can load it correctly.

The pack is meant to support a manager’s daily operating rhythm. It connects sources such as Slack, meeting transcripts, smart notes, people files, org charts, and logs into a shared memory. From there, the user can run workflows like syncing new information, preparing for one-on-one meetings, triaging notes into useful categories, and setting up the pack through conversation.

The constants at the top give the pack its name, version, required extensions, skill folder, and skill names. An extension is a larger system capability, such as Slack access, scheduled tasks, memory, connectors, or todo tracking. A skill is a specific workflow stored in its own directory, such as “sync” or “prep.”

Without this file, the system would not know that this pack exists, which extensions it depends on, or which skill directories should be loaded. The result would be like having all the tools on disk but no inventory telling the application how to assemble them.

#### Function details

##### `pack`  (lines 40–46)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the pack manifest, which is the formal description the UFO system uses to load this chief-of-staff pack. Someone would use this when the system is discovering available packs and needs to know this pack’s name, version, extensions, and skills.

**Data flow**: It starts with the file’s constants: the pack name, version, list of extension names, the skills directory, and the skill names. For each skill name, it creates a SkillSpec pointing to that skill’s folder. It then puts all of that into a Pack object and returns it, without writing files or changing external state.

**Call relations**: When the larger pack-loading system calls this function, it asks, in effect, “What is inside this pack?” The function answers by creating SkillSpec objects for the individual skill folders, then handing those to Pack so the system receives one complete manifest it can use during setup.

*Call graph*: 2 external calls (__init__, __init__).


### Benchmark evaluation packs
DSQA and GDPVal recipes provide preset extension bundles for benchmark-specific evaluation modes and tool coverage.

### `packs/dsqa_eval/ufo_pack_dsqa_eval.py`

`config` · `startup or pack discovery`

This file is a small configuration file that tells the UFO system which extensions belong together for DSQA evaluation. A “pack” is a named bundle of capabilities. Instead of asking users or other code to remember a long list of extensions, this file gives each useful combination a clear name and version.

It starts with shared constants: one version number, three pack names, and a base set of extensions used by all packs. The base pack includes indexing, OpenAI embeddings, and OpenRouter access. The search pack builds on that by adding web-style research tools. The browser pack builds on the search pack by adding browser and Chrome sandbox support.

The three functions are simple factory functions. Each one creates and returns a `Pack` object, which is the manifest object used by the wider system to understand what should be loaded. Without this file, the DSQA evaluation setup would not have these convenient named bundles, and callers would need to manually assemble the right extension lists each time, which is easier to get wrong.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the smallest DSQA evaluation pack. This is for cases that need the basic indexing, embedding, and model-routing tools, but not web search or browser automation.

**Data flow**: It takes no input from the caller. It reads the file’s constants for the core pack name, version, and extension list, then uses them to create a `Pack` object. The result is a configured pack named `dsqa_core` that the rest of the system can load.

**Call relations**: When the system or a caller wants the core DSQA bundle, it calls this function. The function immediately hands the name, version, and extensions to `Pack.__init__`, which builds the manifest object returned to the caller.

*Call graph*: 1 external calls (__init__).


##### `search_pack`  (lines 17–18)

```
def search_pack() -> Pack
```

**Purpose**: Creates a DSQA evaluation pack that includes the core tools plus search and research capabilities. This is useful when evaluation needs to look up information beyond what is already indexed.

**Data flow**: It takes no input from the caller. It reads the search pack name, shared version, and expanded extension list from this file, then creates a `Pack` object. The result is a configured pack named `dsqa_search` with both base and search-related extensions.

**Call relations**: When code needs the search-enabled DSQA bundle, it calls this function. The function passes the prepared details to `Pack.__init__`, which turns them into the pack manifest used by the loader.

*Call graph*: 1 external calls (__init__).


##### `browser_pack`  (lines 21–22)

```
def browser_pack() -> Pack
```

**Purpose**: Creates the most capable DSQA evaluation pack in this file: core tools, search tools, and browser automation. This is for workflows that need to open or interact with web pages, not just search for them.

**Data flow**: It takes no input from the caller. It reads the browser pack name, shared version, and full extension list from this file, then creates a `Pack` object. The result is a configured pack named `dsqa_browser` that includes base, search, research, browser, and sandboxed Chrome support.

**Call relations**: When the system needs a browser-enabled DSQA bundle, it calls this function. The function delegates the actual manifest creation to `Pack.__init__`, giving it the name, version, and full list of extensions.

*Call graph*: 1 external calls (__init__).


### `packs/gdpval_eval/ufo_pack_gdpval_eval.py`

`config` · `config load / pack discovery`

This file is like a menu for a toolbox. Instead of making every user remember which individual extensions are needed for GDPVal evaluation work, it gives them four named choices. The core pack includes the basic pieces: indexing, OpenAI embeddings, and OpenRouter model access. The documents pack adds tools for working with documents, a REPL-style interactive environment, and coding support. The research pack adds web and research tools such as Perplexity, browser access, and a Chrome sandbox. The full pack combines all of those pieces.

The file does not run the evaluation itself. Its job is to describe valid pack configurations using the `Pack` type from `ufo.sdk.manifest`. A pack is a small manifest object: it has a name, a version, and a list of extensions to load. Without this file, someone setting up GDPVal evaluation would need to assemble these extension lists by hand, which is easy to get wrong and harder to share consistently.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the smallest GDPVal pack. Someone would use this when they only need the base capabilities: indexing, embeddings, and model routing.

**Data flow**: It starts with the fixed core pack name, the shared version string, and the base extension list defined at the top of the file. It passes those values into `Pack`, which returns a pack manifest object describing that minimal setup.

**Call relations**: When the system or a caller asks for the core GDPVal setup, this function builds it by handing the name, version, and base extensions to `Pack.__init__`. It does not call any other project logic; it simply produces the manifest object others can load.

*Call graph*: 1 external calls (__init__).


##### `documents_pack`  (lines 17–22)

```
def documents_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack aimed at document work. It includes the core tools plus extensions for documents, interactive repl use, and coding support.

**Data flow**: It reads the shared version, the documents pack name, the base extensions, and the document-specific extensions. It combines the two extension groups into one ordered list and gives that to `Pack`, which returns a manifest for the document-focused setup.

**Call relations**: When a caller wants GDPVal with document capabilities, this function assembles that choice and hands it to `Pack.__init__`. It builds on the same base ingredients as `core_pack`, but adds document-related tools before returning the pack.

*Call graph*: 1 external calls (__init__).


##### `research_pack`  (lines 25–30)

```
def research_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack aimed at research and web-based investigation. It includes the core tools plus extensions for search, browsing, and sandboxed browser use.

**Data flow**: It takes the research pack name, the shared version, the base extensions, and the research-specific extensions. It joins the base and research extension groups, then passes the result to `Pack`, producing a manifest for the research setup.

**Call relations**: When the research-flavored GDPVal setup is requested, this function prepares the extension list and delegates the actual pack object creation to `Pack.__init__`. It is parallel to `documents_pack`, but chooses research tools instead of document tools.

*Call graph*: 1 external calls (__init__).


##### `full_pack`  (lines 33–38)

```
def full_pack() -> Pack
```

**Purpose**: Creates the most complete GDPVal pack. It includes the core tools, document tools, and research tools together.

**Data flow**: It gathers the full pack name, the shared version, and all three extension groups: base, document, and research. It combines them into one extension list and passes that into `Pack`, which returns the complete pack manifest.

**Call relations**: When a caller wants every GDPVal capability available from this file, this function builds the all-in-one manifest. It hands the combined extension list to `Pack.__init__`, just like the smaller pack functions, but includes every extension group defined here.

*Call graph*: 1 external calls (__init__).


### SDK sample pack
The sample recipe demonstrates the public pack system with a sample extension, skill, and onboarding behavior.

### `packs/sample_pack/ufo_pack_sample.py`

`config` · `pack discovery and onboarding`

This is a conformance sample pack: a simple but real pack used to test the boundary between UFO core and installed packs. Its job is to show that a pack can be discovered, loaded, and run using only the public `ufo.sdk` interface, rather than private project internals. Think of it like a smoke alarm test button: it does not do much by itself, but if it fails, it tells you an important connection is broken.

The file names the pack, its version, the extension it bundles, the skill folder it contributes, and the onboarding step it wants UFO to run. A “skill” here is a packaged ability found at a filesystem path. An “onboarding step” is setup code that runs when the pack is introduced.

The important behavior is that onboarding writes to `ctx.store`, the pack’s scoped durable store, under the key `pack:onboarded`. Durable means the record is saved through the same storage path real extensions use, not just printed or mocked. Tests can then read that value back through the public API and know the whole pack-loading seam still works.

#### Function details

##### `_setup`  (lines 25–26)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: This is the pack’s onboarding action. It records that the sample pack has been onboarded by writing a small value into the pack’s scoped store.

**Data flow**: It receives an `ExtensionContext`, which is the object UFO gives setup code so it can access its own storage and runtime services. It writes the key `pack:onboarded` with the value `{"pack_onboarded": true}` into `ctx.store`. Nothing is returned, but the store is changed so later code or tests can confirm the onboarding step really ran.

**Call relations**: This function is not called directly by this file. The `pack` function attaches it to an `OnboardingStep`; later, when UFO loads and activates the sample pack, the onboarding machinery calls `_setup` and gives it the pack’s context.


##### `pack`  (lines 29–36)

```
def pack() -> Pack
```

**Purpose**: This is the public entry point UFO uses to learn what the sample pack contains. It returns a `Pack` object describing the pack name, version, bundled extension, contributed skill, and onboarding step.

**Data flow**: It reads the constants defined in the file, such as the pack name, version, bundled extension name, skill directory, onboarding step name, and setup function. It uses those values to build a `SkillSpec`, an `OnboardingStep`, and finally a `Pack`. The result is a complete pack description that UFO can load.

**Call relations**: UFO’s pack loader calls this function when it discovers the installed sample pack. Inside, it creates the skill description with `SkillSpec`, wraps `_setup` in an `OnboardingStep`, and hands both to `Pack` so the loader receives one clear object describing everything the pack contributes.

*Call graph*: 3 external calls (__init__, __init__, __init__).
