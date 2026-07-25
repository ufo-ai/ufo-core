# Evaluation pack definitions  `stage-19.2`

This stage defines special “packs” used when the system is run for evaluation instead of normal real-world use. A pack is a named bundle of tools and abilities that the UFO system loads at the start of a run. Here, the goal is to make tests repeatable and safe, so the system gets controlled tools instead of unpredictable outside services.

The assistant evaluation file builds an evaluation version of the normal assistant setup. It keeps the usual assistant tools, adds fake email and calendar-style tools for testing, and removes real broker connections that would not behave reliably in an evaluation.

The DSQA evaluation file offers three preset bundles: a basic core setup, one with search, and one with browser access. These let evaluators choose how much outside information access the run should have.

The GDPVal evaluation file works the same way, but for GDPVal tasks. It provides core, document-focused, research-focused, and all-in-one packs. Together, these files act like a menu of safe, repeatable test configurations.

## Files in this stage

### Evaluation Pack Families
Deterministic evaluation pack declarations for assistant, DSQA, and GDPVal runs, each bundling eval-safe capabilities for its target benchmark.

### `packs/assistant_eval/ufo_pack_assistant_eval.py`

`config` · `startup/config load`

This file exists so evaluation runs can test the assistant in a controlled world instead of accidentally pointing it at real external services. A “pack” is a bundle of extensions, meaning named tool providers that the system can offer to the assistant. In normal product use, the assistant pack may include real broker providers such as Composio or Pipedream, which connect to outside apps. In an eval environment, those real providers would usually have no keys or credentials. Worse, they may still appear in tool listings even when not usable, so the assistant could waste attempts on them.

To avoid that, this file starts with the regular assistant pack’s extension list, filters out the real broker entries named `composio` and `pipedream`, and then adds `eval_env`. That `eval_env` extension is the deterministic fake environment used by tests, like a practice stage instead of a live workplace. The exported `pack()` function returns a `Pack` object with the eval pack name, version, and final extension list. Without this file, evaluation deployments could expose the wrong tools, making results noisy, misleading, or dependent on unavailable real-world services.

#### Function details

##### `pack`  (lines 22–23)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the pack description for the assistant evaluation environment. It tells the system the pack’s name, version, and which extensions should be available.

**Data flow**: It reads the module constants `NAME`, `VERSION`, and `EXTENSIONS`, where `EXTENSIONS` has already been built by removing real broker providers and adding the fake eval environment. It passes those values into `Pack`, and the result is a ready-to-use pack manifest object.

**Call relations**: When the system loads the selected pack, it calls `pack()` to get the manifest. `pack()` hands the final name, version, and extension list to `Pack.__init__`, which creates the object the rest of the system uses to know which tools belong in this eval deployment.

*Call graph*: 1 external calls (__init__).


### `packs/dsqa_eval/ufo_pack_dsqa_eval.py`

`config` · `startup`

This file is like a small menu of toolkits for DSQA evaluation. DSQA likely means a question-answering evaluation setup, and each “pack” is a bundle of extensions the system can load together. Without this file, users would have to remember and assemble the right extension names by hand, which is easy to get wrong.

The file starts by naming a shared version and a few pack names. It then builds the packs in layers. The core pack includes the basic extensions: a default index, OpenAI embeddings, and OpenRouter access. The search pack adds web or research-style search tools on top of that. The browser pack adds even more capability: browser automation and a Chrome sandbox, building on the search pack.

Each function returns a `Pack`, which is a manifest object from the UFO SDK. A manifest is a compact description of something the system can load. In everyday terms, each function hands back a labeled box containing a specific set of tools. The important behavior is that the packs are intentionally cumulative: browser includes search, and search includes core.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the smallest DSQA pack, meant for basic evaluation work. It includes the shared core extensions needed for indexing, embeddings, and model access.

**Data flow**: It reads the fixed pack name, version, and core extension list from the file. It puts those values into a new `Pack` object and returns that object to the caller.

**Call relations**: When some loader or registry asks for the core DSQA pack, this function builds it by calling `Pack.__init__`. It does not call the other pack functions; it simply produces the base bundle that the system can load.

*Call graph*: 1 external calls (__init__).


##### `search_pack`  (lines 17–18)

```
def search_pack() -> Pack
```

**Purpose**: Creates a DSQA pack with search and research capabilities added to the core tools. Someone would use this when evaluation needs information retrieval beyond the basic setup.

**Data flow**: It reads the search pack name, shared version, and search extension list. That list already includes the base extensions plus extra search-related extensions, and the function wraps them into a new `Pack` object.

**Call relations**: When the system needs the search-enabled DSQA setup, this function is called to construct the pack through `Pack.__init__`. It builds on the same constants used by the core setup, but it returns its own larger bundle.

*Call graph*: 1 external calls (__init__).


##### `browser_pack`  (lines 21–22)

```
def browser_pack() -> Pack
```

**Purpose**: Creates the most capable DSQA pack in this file, adding browser and sandboxed Chrome support on top of search. This is useful when evaluation needs to interact with web pages, not just search for them.

**Data flow**: It reads the browser pack name, shared version, and browser extension list. That list includes the core tools, the search tools, and browser-related tools, then packages them into a new `Pack` object.

**Call relations**: When a browser-enabled DSQA environment is requested, this function creates the corresponding `Pack` by calling `Pack.__init__`. It represents the top layer of the file’s pack hierarchy: core tools first, then search, then browser capability.

*Call graph*: 1 external calls (__init__).


### `packs/gdpval_eval/ufo_pack_gdpval_eval.py`

`config` · `config load`

This file is like a menu of toolboxes for running GDPVal evaluation work. Instead of making every user remember which extensions need to be switched on, it groups them into named packs. A pack is a manifest object: a small description that says “this bundle has this name, this version, and these extensions.”

The shared base pack includes indexing, OpenAI embeddings, and OpenRouter model access. On top of that, the document pack adds tools for working with documents, running a REPL-style interactive environment, and coding. The research pack instead adds web and research tools, including Exa search, browser support, and a Chrome sandbox. The full pack combines both document and research tools.

Without this file, someone setting up GDPVal would have to assemble these extension lists by hand, which is easy to get wrong. The important behavior is that all four packs use the same version number, and the larger packs are built by combining the smaller extension groups. That keeps the choices consistent and makes the intended setups clear.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the smallest GDPVal pack. Someone would use this when they only need the basic foundation: indexing, embeddings, and model routing.

**Data flow**: It starts with the fixed core name, shared version, and base extension list defined near the top of the file. It passes those values into `Pack`, which produces a pack object describing this core bundle. Nothing else is changed.

**Call relations**: When some outside setup code asks for the core GDPVal pack, this function builds it by calling `Pack.__init__`. It does not call the other pack functions; it is the simple base option that the larger pack definitions mirror.

*Call graph*: 1 external calls (__init__).


##### `documents_pack`  (lines 17–22)

```
def documents_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack for document-heavy work. It includes the basic foundation plus extensions for documents, interactive work, and coding.

**Data flow**: It takes the shared base extensions and adds the document-related extensions to the end of that list. It then gives the combined list, the document pack name, and the shared version to `Pack`, producing a pack object for document workflows.

**Call relations**: When setup code wants the document-focused bundle, this function constructs it directly through `Pack.__init__`. It reuses the same base extension constants as `core_pack`, but it does not call `core_pack`; it rebuilds the full extension list itself.

*Call graph*: 1 external calls (__init__).


##### `research_pack`  (lines 25–30)

```
def research_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack for research and web-based investigation. It includes the basic foundation plus search, research, browser, and sandboxed Chrome tools.

**Data flow**: It reads the shared base extensions and combines them with the research extension list. It passes the research pack name, shared version, and combined extensions into `Pack`, and the result is a manifest object for research workflows.

**Call relations**: When an external loader or setup step asks for the research bundle, this function creates it by calling `Pack.__init__`. It shares the same base ingredients as the other packs, then adds only the research-oriented tools.

*Call graph*: 1 external calls (__init__).


##### `full_pack`  (lines 33–38)

```
def full_pack() -> Pack
```

**Purpose**: Creates the most complete GDPVal pack. It includes the base tools, the document tools, and the research tools all in one bundle.

**Data flow**: It gathers all three extension groups: base, document, and research. It joins them into one ordered extension list, then passes that list with the full pack name and shared version into `Pack`, returning the finished full-pack manifest.

**Call relations**: When setup code wants every available GDPVal evaluation capability, this function builds the all-in-one pack through `Pack.__init__`. It does not call `documents_pack` or `research_pack`; instead, it combines the same constants directly so the final bundle is explicit.

*Call graph*: 1 external calls (__init__).
