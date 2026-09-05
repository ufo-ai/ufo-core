# Pack recipes and product composition  `stage-3.1`

This stage is part of startup configuration. Before the product can run, the system needs a “pack”: a recipe that says which features, tools, and prompt instructions should be switched on together. Each file here defines one of those recipes for a different situation.

The local assistant pack builds a full developer-friendly assistant setup. The billing assistant pack adds billing support so developers can test onboarding without using the larger hosted setup. The hosted assistant pack describes the cloud-ready product shape, with managed-service features, skills, and guidance enabled. The assistant evaluation pack keeps the normal assistant core but swaps out integrations that do not work in tests, adding evaluation tools like a Docker workspace instead.

The DSQA and GDPVal evaluation files provide preset testing shapes, from small core setups to richer search, browser, document, research, or full configurations. The sample pack is a tiny working example that proves the pack mechanism works end to end. Together, these files act like menu choices for assembling the running product.

## Files in this stage

### Assistant product packs
Assistant pack recipes define the local, billing-enabled, evaluation, and hosted product shapes.

### `packs/assistant_billing/ufo_pack_assistant_billing.py`

`config` · `startup configuration`

This file is a small configuration module for choosing a particular bundle of system features, called a pack. A pack is like a recipe card: it says which extensions should be turned on when the system starts.

The normal local assistant pack does not include Metronome, the billing and usage-tracking service, because most development runs should not send billing data anywhere. The hosted assistant pack does include Metronome, but it also includes several hosted-only services that are not useful on a laptop. This file fills the gap. It starts with the regular local assistant pack and adds the Metronome extension on top.

That matters for one specific local testing need: hosted onboarding can offer an owner a “Set up billing” action, and this pack gives a local deployment enough pieces to actually exercise that path. Because it can ship usage and seat information to a billing provider, it is deliberately opt-in rather than the default. Developers are expected to point it at safe test credentials, such as a Metronome sandbox token and a Stripe test-mode key.

In short, this file does not implement billing itself. It tells the larger system, “run the assistant, and also include the billing extension.”

#### Function details

##### `pack`  (lines 24–25)

```
def pack() -> Pack
```

**Purpose**: This function builds and returns the pack definition for the optional assistant-with-billing setup. The system uses it to learn the pack name, version, and which extensions should be enabled.

**Data flow**: It takes no caller-provided input. It reads the file’s constants: the pack name, version, and the extension list made from the normal assistant extensions plus Metronome. It turns those values into a Pack object, which is the structured recipe the rest of the system can use at startup.

**Call relations**: When the pack is selected, the surrounding pack-loading code calls this function to get the configuration. Inside, it hands the name, version, and extension list to Pack.__init__, which creates the actual Pack object returned to the caller.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_dev/ufo_pack_assistant.py`

`config` · `config load / startup`

This file is like a packing list for a complete assistant product. Instead of implementing memory, web research, browser tools, apps, coding support, model providers, and connectors directly, it names the extensions that provide those abilities. When the system is configured to use the pack named “assistant,” this file tells it exactly which extension modules should be included.

The important point is that the pack itself does not add new skills or behavior. It does not teach the assistant how to browse, write code, remember things, or connect to outside services. Each named extension brings its own tools, setup instructions, and user-facing features. This file simply makes sure they all come up together as one coherent assistant.

The constants at the top give the pack its public identity: its name, version, and extension list. The `pack` function then wraps those values in a `Pack` object, which is the system’s standard description of an installable or activatable pack. Without this file, there would be no single “assistant” bundle for local deployment; someone would have to enable the long list of extensions one by one, with a greater chance of missing pieces or mixing incompatible choices.

#### Function details

##### `pack`  (lines 69–70)

```
def pack() -> Pack
```

**Purpose**: This function builds the pack description that the rest of the system can read. It turns the file’s name, version, and extension list into a `Pack` object, which is the standard container for this kind of configuration.

**Data flow**: It starts with the fixed values defined in this file: the pack name, the version string, and the list of extension names. It passes those into `Pack.__init__`, which creates a structured pack object. The result is returned to the caller so the system can activate this exact bundle of extensions.

**Call relations**: When the pack-loading part of the system asks this file what it provides, `pack` is the handoff point. It does not build any extensions itself; it simply calls `Pack.__init__` to package the declared choices into the form the rest of the system expects.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_eval/ufo_pack_assistant_eval.py`

`config` · `startup/config load`

This file exists so automated assistant evaluations can run in a controlled, realistic workspace without accidentally exposing or depending on real external services. A “pack” is a bundle of named extensions that the system can load. The normal assistant pack includes integrations such as Composio and Pipedream, which are broker services that connect to outside tools. In an evaluation run, those real service keys are usually not available, so leaving those integrations enabled would create confusing fake-looking options that only fail when the agent tries to use them.

The file starts from the standard assistant pack, filters out the real broker extensions, then adds two evaluation-specific extensions: `eval_env` and `docker`. The Docker extension lets each conversation use a real bind-mounted `/workspace` directory, meaning the workspace behaves like an actual folder connected into a container rather than a command-line path being rewritten behind the scenes. This matters because evaluations often test whether the assistant can inspect and modify files in a predictable environment.

The key idea is separation. Product deployments can use the normal assistant pack with real integrations. Evaluation deployments can choose this `assistant_eval` pack and get a safer, deterministic set of tools designed for testing.

#### Function details

##### `pack`  (lines 26–27)

```
def pack() -> Pack
```

**Purpose**: This function builds the pack description that the system loads for assistant evaluations. It names the pack, gives it a version, and lists the extensions that should be available in the eval environment.

**Data flow**: It reads the module constants `NAME`, `VERSION`, and `EXTENSIONS`. It passes those values into `Pack`, which creates a manifest object describing this pack. The returned `Pack` object is what the larger system can inspect and load.

**Call relations**: When the pack-loading system asks this module what it provides, `pack` creates the answer. Its only handoff is to `Pack.__init__`, which turns the plain name, version, and extension list into the formal pack object used by the rest of the system.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_hosted/ufo_pack_assistant_hosted.py`

`config` · `startup/config load`

A “pack” is like a prepacked toolbox for the assistant. Instead of asking each workspace to turn on dozens of features one by one, this file names the hosted assistant setup and lists everything that should come with it: chat apps, memory, web browsing, research, Slack and iMessage, coding tools, document generation, scheduled tasks, connectors, billing and metering support, feature flags, and more.

The important difference is that this pack points the assistant toward managed backends. For example, it uses Turbopuffer for the search index, Redis for live updates, E2B for sandboxed code execution, and Browserbase for hosted browser sessions. In plain terms, the heavy machinery runs in managed services rather than inside the assistant’s own local environment.

The file also adds special customer-support guidance to the assistant’s prompt. When a paying customer asks about ufo itself, such as Slack setup, billing, seats, invitations, or missing features, the assistant is told to consult a shipped read-only skill called `customer-onboarding-help` before using tools or general memory. This helps it answer product questions from approved documentation rather than guessing or relying on data that a customer workspace may not contain.

#### Function details

##### `pack`  (lines 97–104)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the pack definition for the hosted assistant. The rest of the system uses this definition to know the pack’s name, version, enabled extensions, included skills, and extra prompt instructions.

**Data flow**: It starts with constants already defined in the file: the pack name and version, the long list of extension names, the folder where skills live, the skill name `customer-onboarding-help`, and the customer-question prompt section. It turns the skill name into a `SkillSpec`, pointing at that skill’s directory, then places everything into a `Pack` object. The result is a complete description of the hosted assistant configuration; the function does not change outside state.

**Call relations**: When the pack system loads this file, it calls `pack` to obtain the hosted assistant’s manifest. Inside that moment, `pack` creates the skill specification and then hands all collected settings to the `Pack` constructor, which packages them into the standard form the wider system understands.

*Call graph*: 2 external calls (__init__, __init__).


### Evaluation suites
DSQA and GDPVal pack recipes provide tiered evaluation configurations for different capability levels.

### `packs/dsqa_eval/ufo_pack_dsqa_eval.py`

`config` · `startup / pack selection`

This file is like a small menu of toolkits for a DSQA evaluation setup. DSQA likely means a question-answering evaluation workflow, and a “pack” is a bundle of extensions that the UFO system can load together. Instead of making users remember a long list of extension names, this file gives them three clear choices.

The smallest choice is the core pack. It includes basic indexing, OpenAI embeddings, and OpenRouter access. The search pack builds on that by adding tools for web-style research, including Perplexity and a research extension. The browser pack builds on the search pack again by adding browser automation and a Chrome sandbox, so the system can work with web pages more directly.

The important idea is layering. Each larger pack contains everything from the smaller one, plus extra tools. This avoids duplicated definitions and keeps the setup predictable. Without this file, callers would need to manually assemble the right extension list every time, which is easy to get wrong and harder to update consistently.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the basic DSQA pack. Someone would use this when they need the evaluation system’s core abilities without web search or browser automation.

**Data flow**: It takes no input from the caller. It reads the fixed core pack name, version, and extension list from this file, then creates and returns a Pack object containing those values.

**Call relations**: When code wants the simplest DSQA setup, it calls this function. The function hands the chosen name, version, and extensions to Pack.__init__, which builds the actual pack object used elsewhere by the UFO system.

*Call graph*: 1 external calls (__init__).


##### `search_pack`  (lines 17–18)

```
def search_pack() -> Pack
```

**Purpose**: Creates a DSQA pack with research and search features included. Someone would use this when evaluation needs outside information-gathering tools, not just the core setup.

**Data flow**: It takes no input from the caller. It uses the predefined search pack name, version, and extension list, then returns a Pack object that includes the core extensions plus search and research-related extensions.

**Call relations**: When code needs DSQA with search capability, it calls this function. The function passes its prepared values to Pack.__init__, which turns them into a loadable pack for the rest of the system.

*Call graph*: 1 external calls (__init__).


##### `browser_pack`  (lines 21–22)

```
def browser_pack() -> Pack
```

**Purpose**: Creates the most capable DSQA pack, including search plus browser automation. Someone would use this when the evaluation workflow needs to open or interact with web pages through a controlled browser environment.

**Data flow**: It takes no caller input. It reads the predefined browser pack name, version, and extension list, then returns a Pack object containing the core, search, browser, and sandboxed Chrome extensions.

**Call relations**: When code needs the full DSQA setup with browser support, it calls this function. The function gives the complete pack definition to Pack.__init__, which creates the pack object that the UFO system can load.

*Call graph*: 1 external calls (__init__).


### `packs/gdpval_eval/ufo_pack_gdpval_eval.py`

`config` · `config load`

This file is like a menu for a tool setup. Instead of asking a user or another part of the system to list every capability one by one, it offers four named packs. Each pack has the same version number and a clear purpose.

The core pack includes the basic pieces: a default index, OpenAI embeddings, and OpenRouter access. In plain terms, that gives the system a baseline way to organize information, turn text into searchable numeric form, and talk to language models. The documents pack adds tools for working with documents, a REPL (an interactive command area), and coding support. The research pack adds web and research-oriented tools, including Perplexity, browser support, and a Chrome sandbox. The full pack combines everything.

Each function builds and returns a `Pack`, which is a manifest object from `ufo.sdk.manifest`. A manifest is a description of what should be included, rather than the running tools themselves. Without this file, someone setting up GDPVal evaluation would need to assemble these extension combinations manually, which is easier to get wrong and harder to keep consistent.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the smallest GDPVal evaluation pack. Someone would use this when they only need the shared base capabilities and not document or web research tools.

**Data flow**: It starts with the fixed core pack name, the shared version number, and the base extension list. It passes those into `Pack`, which produces a pack description. The output is that `Pack` object; nothing else is changed.

**Call relations**: When another part of the system wants the basic GDPVal setup, it calls this function. This function does not build the tools itself; it hands the name, version, and extension list to `Pack.__init__`, which creates the manifest object the wider system can load.

*Call graph*: 1 external calls (__init__).


##### `documents_pack`  (lines 17–22)

```
def documents_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack for document-heavy work. It includes the core capabilities plus document, interactive console, and coding-related extensions.

**Data flow**: It reads the shared version, the documents pack name, the base extension list, and the document-specific extension list. It joins the base and document extensions into one ordered bundle, then gives that bundle to `Pack`. The result is a pack description ready to be loaded.

**Call relations**: When the system or a user asks for the document-focused GDPVal setup, this function is the shortcut. It delegates the actual manifest creation to `Pack.__init__`, supplying the combined list so callers do not have to assemble it themselves.

*Call graph*: 1 external calls (__init__).


##### `research_pack`  (lines 25–30)

```
def research_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack for research and web-assisted work. It includes the core capabilities plus tools for search, browsing, and sandboxed browser use.

**Data flow**: It takes the research pack name, shared version, base extensions, and research-specific extensions. It combines the base and research extension names, then creates a `Pack` from that information. The returned value is the completed research pack manifest.

**Call relations**: When a research-oriented GDPVal environment is needed, callers use this function. It packages the right extension names and passes them to `Pack.__init__`, which turns the settings into the standard pack object used elsewhere.

*Call graph*: 1 external calls (__init__).


##### `full_pack`  (lines 33–38)

```
def full_pack() -> Pack
```

**Purpose**: Creates the most complete GDPVal evaluation pack. It includes the base tools, document tools, coding support, and research/browser tools all together.

**Data flow**: It reads the full pack name, shared version, and all three extension groups: base, document, and research. It combines them into one extension list and uses that list to make a `Pack`. The output is a single manifest describing the full setup.

**Call relations**: When callers want every GDPVal evaluation capability available at once, they call this function. Like the other pack builders, it relies on `Pack.__init__` to create the actual manifest object after this function chooses the correct name and extension bundle.

*Call graph*: 1 external calls (__init__).


### Sample pack
The sample pack demonstrates that pack loading and onboarding actions work end to end.

### `packs/sample_pack/ufo_pack_sample.py`

`config` · `pack discovery and activation`

This is a conformance sample pack: a deliberately small but real installed pack used to test the boundary between the core system and external packs. A pack is a bundle of extensions, skills, and setup steps that the system can load as one unit. This file matters because it uses only the public `ufo.sdk` interface, the same surface outside pack authors are expected to use. If that public interface breaks, this pack is meant to break too, making the problem visible in tests.

The file names the pack, gives it a version, points to one bundled extension called `sample`, and declares one skill stored under the pack’s `skills` directory. It also declares one onboarding step. An onboarding step is setup work that runs when the pack is activated, like a welcome checklist item that records “this pack has been set up.”

The important detail is that the onboarding step writes into `ctx.store`, the pack’s scoped durable store, rather than just printing a message or using a fake log. That means tests can later read the stored value back through the same public path the real system uses. In short, this file is both an example pack and a safety probe for the pack-loading seam.

#### Function details

##### `_setup`  (lines 25–26)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: This is the pack’s onboarding action. When run, it records that the sample pack has completed its setup step.

**Data flow**: It receives an `ExtensionContext`, which is the pack’s access point to services such as its scoped store. It writes the key `pack:onboarded` with the value `{"pack_onboarded": true}` into that store, then finishes without returning a value. The main change is a durable stored record that later code or tests can read back.

**Call relations**: This function is not called directly in this file. Instead, `pack` wraps it inside an `OnboardingStep`, handing it to the pack system so the system can run it when the sample pack’s onboarding steps are executed.


##### `pack`  (lines 29–36)

```
def pack() -> Pack
```

**Purpose**: This is the pack entry point. The pack loader calls it to learn what this sample pack contributes: its name, version, bundled extension, skill, and onboarding step.

**Data flow**: It reads the constants defined in the file, builds a `SkillSpec` pointing at the sample pack skill directory, builds an `OnboardingStep` that uses `_setup` as its action, and returns a `Pack` object containing all of that information. Nothing is written to storage here; it only describes the pack.

**Call relations**: When the pack system discovers this installed pack, it calls `pack` to get the pack definition. Inside, this function creates the small pieces the core loader understands: a skill specification, an onboarding step, and finally the full pack object that bundles them together.

*Call graph*: 3 external calls (__init__, __init__, __init__).
