# Pack bundle definitions  `stage-3.2`

This stage is shared setup support. It defines “packs,” which are ready-made recipes for turning on groups of features together. Instead of asking users or tests to list every extension and skill one by one, the system can load a named pack and get the right bundle.

The local assistant pack is the basic recipe for running assistant features in development. The billing assistant pack adds billing setup support so developers can test that flow locally. The assistant evaluation pack starts from the normal assistant, removes real outside service connectors, and adds fake test tools plus Docker support, so evaluations are safer and repeatable. The hosted assistant pack selects features meant to run on managed cloud services.

Other packs serve special workflows. The chief-of-staff pack declares the extensions and skills for a manager-assistant style workflow. The DSQA and GDPVal evaluation packs provide different tool bundles for different evaluation needs, such as search or browser use. The sample pack is a small proof case for the public SDK, showing that packs can be found, loaded, and run.

## Files in this stage

### Assistant bundles
Core assistant pack definitions for local development, billing-enabled local testing, evaluation runs, and hosted deployments.

### `packs/assistant_dev/ufo_pack_assistant.py`

`config` · `config load / startup`

A “pack” is like a prepacked toolbox. Instead of asking a user or deployer to enable dozens of separate features one by one, this file gives them one name, “assistant”, that brings up a full assistant setup. The long comment at the top explains what is inside: memory, search and research tools, connectors to outside services, browser and computer-use tools, document generation, todos, scheduled tasks, coding help, debugging tools, and more.

The important point is that this pack does not implement those features itself. It only names the extensions that should be active. Each extension brings its own tools, setup instructions, and behavior. This file is therefore more like a shopping list than a workshop: it says what to include, but the actual work happens in the listed extension packages.

It also matters that this is the local assistant pack, not a hosted managed version. The comment says it uses the core system’s own local carrier and index, with no managed infrastructure. Without this file, there would be no single simple “assistant” configuration that reliably starts this exact collection of capabilities together.

#### Function details

##### `pack`  (lines 51–52)

```
def pack() -> Pack
```

**Purpose**: This function builds and returns the pack definition for the local assistant. Someone uses it when the system needs to know the pack’s name, version, and the list of extensions to activate.

**Data flow**: It reads the file’s constants: the pack name, the version string, and the extension list. It passes those values into the Pack constructor, which turns them into a structured pack object. The result is a Pack object that the rest of the system can use to activate this bundle.

**Call relations**: When the pack system loads this file, it calls this function to get the pack description. The function then hands the name, version, and extension list to Pack.__init__, which creates the actual object representing the assistant pack.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_billing/ufo_pack_assistant_billing.py`

`config` · `config load`

A “pack” here is a named bundle of system extensions that tells the UFO platform which capabilities to turn on. The ordinary local assistant pack does not include billing, because most developers should not accidentally send usage data to a real billing provider. The hosted assistant pack does include billing, but it also brings in hosted-only services that are not useful on a laptop.

This file creates the missing middle option: an assistant development setup with Metronome billing added. Metronome is the external billing and usage-tracking service. With this pack selected, local runs can exercise the owner-facing “Set up billing” path, the billing activation job, and the jobs that ship usage and seat information.

The file keeps things deliberately small. It imports the existing assistant pack, reuses all of its extensions, and adds one more extension: `metronome`. Then its `pack()` function returns a `Pack` object with the name `assistant_billing`, a version, and the combined extension list. Because this can talk to real billing systems, it is opt-in and expected to be pointed at sandbox or test credentials, not production keys.

#### Function details

##### `pack`  (lines 25–26)

```
def pack() -> Pack
```

**Purpose**: This function builds the pack definition for the assistant-with-billing setup. The platform calls it when it needs to know this pack’s name, version, and enabled extensions.

**Data flow**: It starts with the constants in this file: the pack name, version, and the extension list made from the normal assistant extensions plus `metronome`. It passes those values into `Pack`, which creates a structured pack description. The result is returned to the caller so the system can enable the right capabilities.

**Call relations**: When the pack is loaded, this function is the small handoff point between the plain Python file and the UFO pack system. It calls `Pack.__init__` to turn the simple name, version, and extension list into the official pack object that the rest of the startup/configuration flow can use.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_eval/ufo_pack_assistant_eval.py`

`config` · `startup / pack selection`

This file exists so evaluation runs get the right tool environment without accidentally exposing or depending on real outside services. In a normal product setting, an assistant might have connectors for services such as Composio or Pipedream. In an evaluation setting, those real services are not useful because there may be no real API keys, and failed attempts to use them would waste the agent’s turns. So this pack deliberately leaves those real broker extensions out.

Instead, it adds an evaluation environment extension and a Docker extension. The evaluation environment provides deterministic fake services, such as email, calendar, and code search, so tests can be repeatable. “Deterministic” means the same conversation can see the same predictable world each time. The Docker extension lets an evaluation run use a real bind-mounted `/workspace` directory inside a container, rather than faking or rewriting file paths.

The file is mostly a small manifest: it names the pack, gives it a version, builds the extension list, and exposes a `pack()` function that returns a `Pack` object. A useful analogy is a travel kit checklist: it starts with the usual assistant kit, removes tools that do not belong in the test lab, and adds the special tools needed for controlled experiments.

#### Function details

##### `pack`  (lines 26–27)

```
def pack() -> Pack
```

**Purpose**: Creates the manifest object for the `assistant_eval` pack. The system uses this to learn the pack’s name, version, and which extensions should be available during evaluation runs.

**Data flow**: It reads the file-level constants for the pack name, version, and prepared extension list. It passes those values into `Pack`, which produces a pack description object. The result is returned to the caller; nothing else is changed.

**Call relations**: When the pack system loads this module, it calls `pack()` to ask, “What pack do you provide?” The function hands the name, version, and extension list to `Pack.__init__`, which packages that information into the standard shape the rest of the system expects.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_hosted/ufo_pack_assistant_hosted.py`

`config` · `config load`

Think of this file as a packing list for a hosted version of the assistant. When a workspace chooses the pack named “assistant_hosted”, the system needs to know which capabilities to turn on and where to find any included skills. This file answers those questions in one place.

The pack includes many assistant features: memory, research tools, browser and computer-use tools, Slack integration, document generation, scheduled tasks, coding help, model providers, usage metering, and more. The important difference is that several heavy services are provided by managed backends. For example, search indexing uses Turbopuffer, live frame sharing uses Redis, sandbox execution uses E2B, and browser runs use Browserbase-hosted Chrome. In plain terms, this pack is for running the same broad assistant experience, but with infrastructure supplied by hosted services rather than by the local core system.

It also ships one read-only skill, “customer-onboarding-help”. That skill contains curated onboarding facts for hosted workspaces, such as signup, invitations, Slack installation, billing, seats, and limits on what must not be disclosed. This matters because a new hosted customer may not yet have enough workspace memory for the assistant to answer those questions reliably.

#### Function details

##### `pack`  (lines 65–71)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the pack description that the larger system can activate. Someone would use this when the system needs to load the hosted assistant’s name, version, enabled extensions, and included skill folders.

**Data flow**: It starts with the constants in this file: the pack name, version, list of extension names, the skills directory, and the skill names. It turns each skill name into a SkillSpec pointing at its folder, then puts everything into a Pack object. The result is a complete pack manifest that says, “this is what the hosted assistant includes.”

**Call relations**: During pack loading, the system calls this function to get the hosted pack’s manifest. Inside, it creates SkillSpec entries for the shipped skill content and hands those, along with the extension list, to Pack so the rest of the system can activate the right services and capabilities.

*Call graph*: 2 external calls (__init__, __init__).


### Manager workflow pack
The chief-of-staff pack manifest declares the extensions and skills needed for the manager-assistant workflow.

### `packs/chief_of_staff/ufo_pack_chief_of_staff.py`

`config` · `startup / pack discovery`

This file is like the label and packing list on a toolbox. The toolbox is a “chief of staff” assistant that helps a manager work from Slack, review meeting notes and channels, prepare one-on-one briefs, sort follow-up work, and improve its own written judgment over time. Without this file, the system would not know that this pack exists, what support services it depends on, or where to find its skills.

The file names the pack, gives it a version, and lists the extensions it needs. These extensions are the surrounding services the pack relies on, such as Slack access, memory storage, scheduled tasks, connectors to outside tools, todo tracking, and self-improvement workflows. It also points to a local skills folder and names four skills: setup, sync, prep, and triage.

The only function, `pack`, turns those simple constants into a `Pack` object that the UFO platform can load. For each skill name, it builds a `SkillSpec`, which is a small description telling the platform where that skill lives on disk. In short, this file does not perform the chief-of-staff work itself; it tells the system how to assemble the pieces that will.

#### Function details

##### `pack`  (lines 40–46)

```
def pack() -> Pack
```

**Purpose**: This function builds and returns the official manifest for the chief-of-staff pack. The platform uses it to learn the pack’s name, version, required extensions, and skill locations.

**Data flow**: It starts with the constants in this file: the pack name, version, extension list, skills directory, and skill names. It turns each skill name into a path inside the skills folder, wraps each path in a `SkillSpec`, and then puts everything into a `Pack` object. The result is a complete pack description that can be loaded by the rest of the system.

**Call relations**: When the system discovers this pack, it calls `pack` to get the pack description. Inside that moment, `pack` creates `SkillSpec` entries for the four skill folders and hands them to `Pack`, so the platform can later enable the right extensions and find the workflows that do the real chief-of-staff work.

*Call graph*: 2 external calls (__init__, __init__).


### Evaluation packs
Benchmark-oriented pack definitions provide ready-made extension bundles for DSQA and GDPVal evaluation scenarios.

### `packs/dsqa_eval/ufo_pack_dsqa_eval.py`

`config` · `startup or pack discovery`

This file is like a small menu of toolkits. A “pack” is a named bundle of extensions, where each extension adds a capability to the UFO system. Instead of making every caller remember which pieces are needed for DSQA evaluation, this file gives them three clear choices.

The smallest pack, `dsqa_core`, includes the shared base tools: a default index, OpenAI-based embedding support, and OpenRouter model access. The next pack, `dsqa_search`, builds on that by adding web-style search and research tools. The largest pack, `dsqa_browser`, builds on the search pack and adds browser automation plus a Chrome sandbox, so the system can interact with web pages more directly.

All three packs share the same version number. That matters because it gives the surrounding system a stable label for what set of capabilities it is loading. Without this file, code that wants a DSQA evaluation environment would need to recreate these extension combinations by hand, which would be repetitive and easier to get wrong.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the basic DSQA pack. This is the lightest setup, meant for cases that need indexing, embeddings, and model access but not extra search or browser tools.

**Data flow**: It starts with the fixed core pack name, the shared version string, and the base extension list. It puts those values into a new `Pack` object, which is the system’s simple record for “this named bundle contains these capabilities.” The result is returned to whoever is collecting available packs.

**Call relations**: When the system asks for the core DSQA bundle, this function creates the pack description by handing the name, version, and extension list to `Pack`. It does not call other local helpers because the bundle is fully described by the constants in this file.

*Call graph*: 1 external calls (__init__).


##### `search_pack`  (lines 17–18)

```
def search_pack() -> Pack
```

**Purpose**: Creates the DSQA pack that can use search and research tools. Someone would use this when an evaluation needs to look beyond stored/indexed information and gather information through search-style extensions.

**Data flow**: It reads the fixed search pack name, the shared version string, and the search extension list. That list contains the base capabilities plus search-related additions. It wraps those details in a new `Pack` object and returns it.

**Call relations**: When the surrounding pack-loading system wants the search-capable DSQA setup, this function produces the pack description. It hands the prepared constants to `Pack`, which turns them into the standard object the rest of the system understands.

*Call graph*: 1 external calls (__init__).


##### `browser_pack`  (lines 21–22)

```
def browser_pack() -> Pack
```

**Purpose**: Creates the fullest DSQA pack, including browser automation. This is useful when an evaluation needs not only search tools but also the ability to open and interact with web pages in a controlled browser environment.

**Data flow**: It takes the fixed browser pack name, the shared version string, and the browser extension list. That list includes the search setup plus browser and sandboxed Chrome support. It creates and returns a `Pack` object containing those choices.

**Call relations**: When the system needs the browser-enabled DSQA environment, this function packages the right extension set into the standard `Pack` form. It passes the final name, version, and extensions directly to `Pack` so the rest of UFO can load the bundle consistently.

*Call graph*: 1 external calls (__init__).


### `packs/gdpval_eval/ufo_pack_gdpval_eval.py`

`config` · `startup or pack discovery`

This file is like a menu of toolkits for GDPVal evaluation. A pack is a small manifest: it gives a bundle a name, a version, and a list of extensions to turn on. Extensions are optional capabilities, such as indexing, document tools, research tools, a browser, or a coding sandbox.

The file starts by naming common building blocks. Every pack includes the same base extensions, which appear to provide the minimum shared abilities: a default index, OpenAI-style embeddings, and OpenRouter model access. Then it defines two extra groups. One group adds document and coding tools. The other adds research and browser-like tools.

The four functions create different combinations of these groups. The core pack is the smallest version. The documents pack adds document-oriented tools. The research pack adds web and research-oriented tools. The full pack includes everything. This matters because different evaluation runs may need different levels of capability. Without this file, callers would have to remember and repeat the exact extension lists themselves, which is easy to get wrong and harder to update consistently.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the smallest GDPVal pack. Use this when an evaluation only needs the shared base capabilities and not document, browser, or research extras.

**Data flow**: It takes no input from the caller. It reads the fixed core pack name, version, and base extension list from this file, then creates and returns a Pack object containing those values.

**Call relations**: When some outside loader asks for the core GDPVal setup, this function builds the Pack by calling Pack.__init__. It does not hand work to other local functions; it simply returns the finished manifest for the rest of the system to use.

*Call graph*: 1 external calls (__init__).


##### `documents_pack`  (lines 17–22)

```
def documents_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack for work that needs document and coding-related tools. It keeps the base capabilities and adds extensions for documents, a REPL, and coding.

**Data flow**: It takes no caller input. It reads the document pack name, shared version, base extensions, and document extension group, joins the two extension groups together, and returns a Pack object with that combined list.

**Call relations**: When a caller wants the document-focused GDPVal setup, this function prepares the manifest and passes the combined settings into Pack.__init__. The returned Pack can then be loaded by the wider system to activate those extensions.

*Call graph*: 1 external calls (__init__).


##### `research_pack`  (lines 25–30)

```
def research_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack for research-style work, such as using search, browsing, or a browser sandbox. It is useful when the evaluation needs outside information-gathering tools.

**Data flow**: It takes no caller input. It reads the research pack name, shared version, base extension list, and research extension list, combines the extensions, and returns a Pack object describing that bundle.

**Call relations**: When an outside part of the system requests the research-focused GDPVal pack, this function builds it by calling Pack.__init__. It hands back the manifest so the system can load the listed research extensions.

*Call graph*: 1 external calls (__init__).


##### `full_pack`  (lines 33–38)

```
def full_pack() -> Pack
```

**Purpose**: Creates the most complete GDPVal pack. It includes the base tools, the document and coding tools, and the research and browser tools all together.

**Data flow**: It takes no caller input. It reads the full pack name, shared version, and all three extension groups from this file, combines the groups into one ordered list, and returns a Pack object with the complete configuration.

**Call relations**: When a caller wants every GDPVal evaluation capability enabled, this function assembles the full manifest and passes it to Pack.__init__. The returned Pack is the all-in-one option used by the larger pack-loading flow.

*Call graph*: 1 external calls (__init__).


### SDK sample pack
The sample pack demonstrates pack discovery and execution through the public SDK with a sample extension, skill, and onboarding step.

### `packs/sample_pack/ufo_pack_sample.py`

`config` · `pack discovery and onboarding`

This is a conformance sample pack: a deliberately simple but real pack that tests the boundary between UFO core and installed packs. A pack is a bundle of add-ons, such as extensions, skills, and setup steps, that can be activated together. This file matters because it proves that a pack can use only the public `ufo.sdk` API and still plug into the system correctly. If this broke, the project would lose confidence that outside pack authors can build against the supported interface.

The file names the pack, its version, the extension it includes, the skill directory it contributes, and the onboarding step it wants UFO to run. The main `pack()` function returns a `Pack` object, which is like a shipping label listing everything inside the bundle. It says: include the `sample` extension, expose this skill folder, and run this setup function during onboarding.

The setup function, `_setup`, receives an `ExtensionContext`, which is the pack’s doorway into UFO services. It writes a small marker into the pack’s scoped store: `pack:onboarded` with `{"pack_onboarded": true}`. That is important because it is not just a pretend log message. It goes through the same durable storage path normal code uses, so tests can later read it back and confirm the whole pack seam really worked.

#### Function details

##### `_setup`  (lines 25–26)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: This asynchronous setup step records that the sample pack’s onboarding ran successfully. It is used as the pack-level onboarding action, so the system can later confirm that pack setup wrote to real storage.

**Data flow**: It receives an `ExtensionContext`, which gives access to the pack’s scoped store. It writes the key `pack:onboarded` with the value `{"pack_onboarded": true}` into that store. It returns nothing, but it changes durable state by leaving behind that marker.

**Call relations**: The `pack()` function registers `_setup` inside an `OnboardingStep`. Later, when the pack is activated and onboarding runs, UFO calls this setup function through that registered step. `_setup` then hands its result to the store by calling `ctx.store.put`, so the proof of onboarding is saved where the rest of the system can read it.


##### `pack`  (lines 29–36)

```
def pack() -> Pack
```

**Purpose**: This is the pack entry point: it tells UFO what this installed pack contains. It creates and returns a `Pack` that bundles the sample extension, one skill, and one onboarding step.

**Data flow**: It reads the constants defined in this file, such as the pack name, version, bundled extension name, skill directory, and onboarding name. It turns the skill directory into a `SkillSpec`, turns the setup function into an `OnboardingStep`, and combines everything into a `Pack`. The output is that complete `Pack` object for UFO to load.

**Call relations**: UFO’s pack loading code looks for and calls this function when it discovers the sample pack. Inside, it builds the smaller pieces first: `SkillSpec` describes the contributed skill folder, `OnboardingStep` wraps `_setup` as a setup action, and `Pack` collects those pieces with the bundled extension. The returned `Pack` is then used by the loader to activate exactly what this sample pack contributes.

*Call graph*: 3 external calls (__init__, __init__, __init__).
