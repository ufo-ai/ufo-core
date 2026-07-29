# Content and business extension declarations  `stage-2.2.3`

This stage is shared startup support for optional feature bundles, called extensions. An extension is a folder of extra abilities that the main UFO system can discover and load when needed. The three empty __init__.py files act like “this is a usable box” labels for Python. They mark the documents, sites, and YC folders as importable packages, but they do not do any work themselves.

The real declarations are the manifest.py files. The documents manifest tells the system which document-focused skills are available, such as help for Word, PowerPoint, PDFs, spreadsheets, reviews, and design themes. The sites manifest advertises the website-building extension, including its tools, prompts, skills, and the subagent profile used for site work. The YC manifest describes a broader business bundle: tools, credentials, searchable source material, onboarding steps, and skill instructions for YC-oriented tasks. Together, these files are like catalog cards, letting the core system know what extra parts exist before it tries to use them.

## Files in this stage

### Document Extension Declaration
Package marker and manifest describing document-centered skills for office files, PDFs, spreadsheets, reviews, and themes.

### `extensions/documents/ufo_ext_documents/__init__.py`

`other` · `import time`

This file is a small but important signpost for Python. By existing as `__init__.py`, it tells Python that the `ufo_ext_documents` directory should be treated as a package, which means its contents can be imported using normal Python import paths. Think of it like a label on a folder in a filing cabinet: the label does not contain documents itself, but it lets people reliably refer to the folder by name.

There is no executable code here, no settings, and no functions. Nothing is initialized when this package is imported through this file. Its value is structural: without it, depending on the Python version and packaging setup, imports that expect `ufo_ext_documents` to be a regular package might fail or behave differently. In short, this file helps the document extension fit cleanly into the larger project’s Python module layout.


### `extensions/documents/ufo_ext_documents/manifest.py`

`config` · `extension discovery and skill registration`

This file is like the label on a toolbox. It does not perform document editing itself. Instead, it tells the larger UFO system which document-production tools are available in this extension, where to find them on disk, and what version of the extension is being offered.

The important idea is a “skill”: a folder containing instructions, scripts, and supporting files that the agent can load when it needs a certain ability. For example, there are skills for making Word documents, PowerPoint decks, PDFs, spreadsheets, reviewing documents, and creating visual themes. The shared “design-foundations” skill acts like a common style guide that other document skills can depend on.

Without this file, the system would not have a simple, reliable way to discover these document skills. The folders might exist, but the loader would not know that they belong to the documents extension or that they should be registered as loadable skills.

The file defines the extension name, its version, the root folder where the skills live, and the approved list of skill folder names. Its single function packages that information into a manifest, which is the structured description the rest of the system expects.

#### Function details

##### `manifest`  (lines 28–33)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the official description of the documents extension. It gives the loader the extension name, version, and the list of skill folders that should become available to the agent.

**Data flow**: It starts with constants in this file: the extension name, version number, skills folder path, and skill names. For each skill name, it creates a skill specification pointing to that skill’s folder. It then wraps all of those skill specifications into one manifest object and returns it to the caller.

**Call relations**: When the system asks this extension what it contributes, this function is the answer. It creates one SkillSpec for each listed skill so the loader can treat each folder as a loadable ability, then hands those SkillSpec objects to Manifest so the whole extension can be registered as one coherent package.

*Call graph*: 2 external calls (__init__, __init__).


### Site Extension Declaration
Package marker and manifest exposing website-building tools, prompts, skills, and subagent profile additions.

### `extensions/sites/ufo_ext_sites/__init__.py`

`other` · `import/package discovery`

This is an empty Python package file. In Python projects, a file named `__init__.py` tells Python, “treat this folder as an importable package.” That means code elsewhere can refer to modules inside `extensions/sites/ufo_ext_sites` using normal Python import paths. Think of it like a label on a drawer: the drawer may contain useful tools, but this label mainly helps the rest of the system find and open it correctly. Because the file is empty, it does not run setup code, expose shortcuts, or change any settings. Its value is structural: without it, some import systems or older Python tooling might not recognize this directory as a package, which could make site extension modules harder or impossible to load in certain environments.


### `extensions/sites/ufo_ext_sites/manifest.py`

`config` · `startup`

This file acts like the label on a toolbox. When the UFO system loads the sites extension, it needs a clear list of what the extension contains and how those pieces should be made available to an agent. Without this file, the system would not know that the extension can serve websites, delegate website creation to a specialized child agent, or load the website-building skill instructions.

The file first names the extension and gives it a version. It then reads a prompt section from a Markdown file. That prompt section is extra guidance added to the agent’s instructions, teaching it how to serve and check a site before presenting it as finished.

It also points to the skills folder and names the main skill, `website-building`. A skill is a bundle of instructions and supporting files that helps the agent do a particular kind of work. In this case, the skill includes shared website design guidance and project-specific templates.

The main `manifest` function packages all of this into a `Manifest` object: the normal site tools, the delegation tools, the specialized website-building subagent profile, the prompt section, and the skill location. The rest of the system can then load this one object instead of having to inspect the extension by hand.

#### Function details

##### `manifest`  (lines 29–37)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension’s manifest, which is the structured description of everything the sites extension contributes to the system. This is what lets the host application discover the extension’s tools, prompt text, subagent profile, and skill package.

**Data flow**: It reads constants already prepared in the file: the extension name and version, the collected site and delegation tools, the website-building subagent profile, the prompt text loaded from disk, and the path to the website-building skill. It wraps the prompt text in a `PromptSection`, wraps the skill path in a `SkillSpec`, and places everything into a `Manifest`. The result is a single manifest object that the loader can use.

**Call relations**: When the extension is being loaded, the host system calls `manifest` to ask, “What do you provide?” Inside that answer, it creates the small helper objects needed by the manifest: one for the prompt section and one for the skill. It then hands back the completed `Manifest`, which the host uses to register the tools, subagent, prompt guidance, and skill.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### YC Extension Declaration
Package marker and manifest defining YC-focused tools, credentials, sources, onboarding, and bundled skill instructions.

### `extensions/yc/ufo_ext_yc/__init__.py`

`other` · `import time`

In Python, a folder usually needs an `__init__.py` file to be treated as a package: a named bundle of code that can be imported elsewhere. This file is that marker for the `extensions/yc/ufo_ext_yc` package. Think of it like a label on a drawer: the label does not do the work inside the drawer, but without it, other parts of the program may not know how to find or refer to the contents cleanly. Because the file is empty, it does not set up defaults, expose shortcuts, or run startup code. Its value is structural: it tells Python and readers of the project that this directory is meant to be a coherent extension package, likely related to the `yc` extension area.


### `extensions/yc/ufo_ext_yc/manifest.py`

`config` · `extension load and onboarding`

This file is the registration form for the YC extension. Without it, the larger system would not know that the YC tools exist, what credential they need, how to index YC guidance, or what setup should happen when a workspace enables the extension.

The file defines three user-facing tools. `yc_auth` connects a workspace admin's YC account through a browser-based approval flow. `yc_read` uses that approved account for read-only YC and Bookface queries. `yc_index` saves a bounded YC or Bookface search into shared workspace memory, so it can be refreshed and searched later. The descriptions are deliberately detailed because they guide the assistant on when each tool is safe to use.

It also defines a credential slot, which is like a named safe-deposit box for the YC login. The credential is shared by the workspace's read-only YC tools, but it is not exposed to chat or the sandbox.

Finally, the file connects YC source indexing into the platform. During onboarding, it registers built-in YC guidance collections as shared sources. The `manifest()` function packages all of this into one `Manifest` object, which is the platform's standard way to discover and install an extension.

#### Function details

##### `setup_sources`  (lines 77–84)

```
async def setup_sources(ctx: ExtensionContext) -> None
```

**Purpose**: This function prepares the built-in YC guidance sources for a workspace. It makes sure each predefined YC guidance collection is registered as shared workspace knowledge.

**Data flow**: It receives an extension context, which is the system object used to register extension resources. It loops through the known YC guidance collections, wraps each collection name in a `YcSourceConfig`, and asks the context to register that source under the shared subject. It does not return a value; the result is that the workspace now has these YC guidance sources available.

**Call relations**: This function is attached to the manifest as an onboarding step. When the platform runs that onboarding step, it calls `setup_sources`; `setup_sources` then hands each collection to `ExtensionContext.register_source` so the platform can make the source available.

*Call graph*: calls 1 internal fn (register_source); 1 external calls (__init__).


##### `manifest`  (lines 87–110)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the YC extension's full declaration for the platform. It says what the extension is called, which credential it needs, which tools it offers, which source backend it uses, what onboarding should run, and where its skills live.

**Data flow**: It takes no input. It gathers the constants and tool definitions from this file, creates supporting objects such as the credential slot, source provider, onboarding step, and skill spec, and returns one `Manifest` object containing the complete extension setup. Nothing is directly changed when this function is called; it produces the description the platform will use to wire the extension in.

**Call relations**: The platform calls `manifest` when discovering or loading the extension. Inside it, the function creates the pieces the platform expects: `CredentialSlot` for stored YC access, `SourceProvider` for building YC sources from credentials, `OnboardingStep` for calling `setup_sources`, and `SkillSpec` for loading the bundled YC research skill.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, __init__).
