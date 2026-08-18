# Extension package import markers  `stage-4.6`

This stage is quiet behind-the-scenes support. It does not start tools, run the main work, or shut anything down. Instead, these __init__.py files act like nameplates on folders. In Python, that nameplate tells the system “this folder is a package,” meaning other code can import and use files inside it.

Most files simply make an extension importable: documents, pipedream, redis hub, repl, research, scheduled tasks, sites, Slack, sources, sweep, ufo, and web. The nested document script markers do the same for document-review, PowerPoint, and Excel skill script folders, so their helper scripts can be reached in the normal Python way. The browser marker is the front door for browser-related tools, including sandbox browser or computer-use features and a browser subagent profile. The self-improvement marker also records that its package reviews past workspace activity offline and suggests prompt changes for human approval. The skill-create marker notes support for agent-authored skills and skills available during each runtime turn. Together, these small files make the extension shelves visible before the real tools are loaded.

## Files in this stage

### Browser and document fronts
Top-level browser and document extension package markers make these user-facing extension areas importable.

### `extensions/browser/ufo_ext_browser/__init__.py`

`other` · `cross-cutting`

This file does not contain executable code. Its main job is to identify this folder as a Python package, so other parts of the project can import browser-extension features from it. The short module note explains the package’s purpose: it groups together tools for using a sandbox browser or computer-like environment, plus a browser-focused subagent profile. In plain terms, this package is like a labeled drawer in a toolbox. The drawer itself does not do the work, but it tells readers and Python where the browser-use tools live. Without this file, imports may be less clear or may not work in older Python packaging situations, and newcomers would lose a useful signpost explaining why this directory exists.


### `extensions/documents/ufo_ext_documents/__init__.py`

`other` · `startup/import time`

This file is intentionally empty. In Python, an `__init__.py` file tells Python that a directory should be treated as a package, meaning its contents can be imported using package-style names. Here, it makes the `ufo_ext_documents` folder usable as the home for the documents extension. Without this file, some Python environments or tooling might not recognize the folder as an importable package, which could make imports fail or make the extension harder to discover. Think of it like a label on a drawer: the drawer may contain useful tools, but the label tells the rest of the system how to find and refer to them.


### Document skill scripts
Document-related skill script folders are marked as importable packages without adding runtime behavior.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/__init__.py`

`other` · `import/package discovery`

This is an empty Python `__init__.py` file. Its main job is structural: it tells Python that the surrounding `scripts` directory should be treated as an importable package. Think of it like putting a label on a drawer so the rest of the project can refer to what is inside the drawer by name.

Because the file has no code, it does not perform any document review work itself. It does not read files, call services, define settings, or create objects. Its value is in making the folder fit into Python’s package system, which helps nearby script modules be found and imported consistently.

Without this file, imports may still work in some modern Python setups, but they can become less predictable depending on how the project is run, packaged, or tested. Keeping the file avoids ambiguity and makes the folder’s purpose clear to both Python and human readers.


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package. That matters here because this directory likely holds script modules for the Office PowerPoint (`pptx`) document extension. Without this file, some tooling or older Python import rules might not recognize the folder as a package, which could make imports fail or behave differently.

There is no setup logic, no hidden side effect, and no runtime behavior inside this file. Think of it like a label on a drawer: it does not do the work itself, but it tells the rest of the system, “this drawer belongs to the Python module structure, and files inside it can be found by package-style imports.”


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker file. In Python projects, a file named `__init__.py` tells Python that the folder should be treated as an importable package, rather like putting a label on a drawer so the rest of the system knows where to find its contents. Here, it belongs to the `office-xlsx` skill area, inside a `scripts` folder, so its main job is to make any script modules in that folder available through normal Python imports. Nothing is initialized, configured, or run from this file. If it were missing, imports that depend on this folder being recognized as a package could fail or behave differently, depending on the Python version and how the project loads extensions.


### Automation and runtime utilities
Automation, data hub, REPL, research, and scheduled-task extension packages are made importable for later runtime use.

### `extensions/pipedream/ufo_ext_pipedream/__init__.py`

`other` · `import setup`

This is an empty Python package file. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package. You can think of it like a label on a drawer: the drawer may hold useful tools in other files, but this label is what lets the program find the drawer by name. Without this file, some Python setups or packaging tools might not recognize `ufo_ext_pipedream` as a package, which could make imports from the Pipedream extension fail. Because the file is empty, it does not define any functions, classes, settings, or startup behavior. Its value is structural: it helps the extension fit into Python’s module system.


### `extensions/redis_hub/ufo_ext_redis_hub/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder often needs an `__init__.py` file to be treated as an importable package, meaning other parts of the project can refer to code inside this folder using normal import paths. Think of it like putting a label on a drawer: the drawer may contain useful tools, but this label mainly tells Python, “this drawer is part of the system.”

Because the file is empty, it does not run setup code, expose shortcut imports, or define any functions or classes. Its value is structural rather than behavioral. Without it, depending on the Python version and packaging setup, code may have trouble importing the Redis Hub extension as a package. Keeping it empty also means importing the package has no hidden side effects, which is usually easier to reason about.


### `extensions/repl/ufo_ext_repl/__init__.py`

`other` · `import time`

This is an empty Python package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Here, that means the `ufo_ext_repl` extension can be found and loaded by name from elsewhere in the project.

Because the file is empty, it does not set up state, expose helper functions, or run startup code. Its value is structural: it is like putting a label on a folder so the rest of the system knows the folder belongs to a particular module. Without it, depending on the Python version and import style used by the project, imports for this extension could be less reliable or fail in some packaging situations.


### `extensions/research/ufo_ext_research/__init__.py`

`other` · `import/package discovery`

In Python, an `__init__.py` file is used to tell Python that a folder should be treated as a package, meaning a named bundle of code that other files can import. This particular file is empty, so it does not run setup code, define shortcuts, or expose helper functions. Its value is structural: without it, some Python tools or older import styles might not recognize `extensions/research/ufo_ext_research` as an importable package. You can think of it like a label on a drawer. The drawer may contain useful items elsewhere, but this label is what lets the rest of the system refer to the drawer by name. Because the file has no contents, there is no hidden behavior to worry about during startup or runtime.


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker file. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package. That matters because the scheduled-tasks extension likely has other files in this folder that contain the real code, and those files need a stable package name so the rest of the project can refer to them. Think of it like a label on a drawer: the label does not contain the tools, but it makes the drawer recognizable and reachable. If this file were removed in environments that require package markers, imports for `ufo_ext_scheduled_tasks` could fail or behave differently. Since the file is empty, it performs no startup work, registers no tasks, and changes no settings by itself.


### Agent growth packages
Self-improvement, site support, and skill-creation packages declare import boundaries for agent development workflows.

### `extensions/self_improvement/ufo_ext_self_improvement/__init__.py`

`other` · `startup/import`

This file does not contain working code. Its job is to identify this folder as the home of the self-improvement extension and to give readers a quick summary of the extension’s purpose. The extension is described as an offline replay evaluation loop. In plain terms, that means it looks back at saved records of what happened in the workspace, rather than acting live while a user is working. Those saved records are called trajectories: step-by-step traces of actions, decisions, and results. The extension uses those traces to find possible improvements to prompts, which are the instructions given to an AI system. Importantly, it does not silently change those prompts on its own. It opens governed prompt changes, meaning proposed changes that go through a controlled approval process. A member must approve them before they take effect. Without this package file, Python would not treat this directory as a normal importable package in the same way, and readers would lose the high-level signpost explaining what this extension is for.


### `extensions/sites/ufo_ext_sites/__init__.py`

`other` · `import time`

Python uses files named `__init__.py` to recognize a folder as an importable package. In everyday terms, this file is like a label on a drawer: it tells Python, “the files inside this drawer belong together and can be referred to by this package name.” This particular file is empty, so it does not run setup code, expose shortcuts, or define any functions or classes. Its value is structural. Without it, depending on the Python version and import style used by the project, other parts of the system might not be able to reliably import code from `extensions/sites/ufo_ext_sites`. Keeping it empty also has a useful meaning: importing the package itself has no side effects, so it will not unexpectedly start work, load resources, or change settings.


### `extensions/skill_create/ufo_ext_skill_create/__init__.py`

`other` · `import time`

This is the package doorway for the skill-creation extension. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package, like a labeled drawer that other code can open. Here, the file does not define functions or run setup code. It only contains a brief description of the package’s theme: skills that an agent has created and owns, represented as objects, plus temporary or per-turn skills used while the system is running. Without this file, older Python tooling or code that expects a traditional package might not recognize this directory as something it can import cleanly. The important thing to know is that the real behavior lives in other files in this package; this file mainly provides identity and a small human-readable summary.


### Collaboration and web packages
Slack, source, sweep, core UFO, and web extension packages finish the import-marker layer for collaboration and web-facing capabilities.

### `extensions/slack/ufo_ext_slack/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder with an `__init__.py` file is treated as an importable package, meaning other code can refer to it by name, such as `ufo_ext_slack`. Even though there is no code inside this file, it still matters because it gives the Slack extension a clear home in the project’s module structure. Think of it like a label on a drawer: the label does not store the tools itself, but it tells everyone that this drawer is part of the organized workspace. Without this file, depending on the Python version and import setup, code that expects `ufo_ext_slack` to behave like a regular package might fail to import it or might behave differently. Any actual Slack-related behavior lives in other files under this package.


### `extensions/sources/ufo_ext_sources/__init__.py`

`other` · `import/package discovery`

This is an empty package-start file. In Python, a file named `__init__.py` tells the interpreter, “treat this directory as an importable package.” That matters because the surrounding project can then refer to code inside `extensions/sources/ufo_ext_sources` using normal Python import paths. Think of it like a label on a folder in a filing cabinet: the label does not contain the documents, but it lets the system find and organize them correctly. Since the file has no code, it does not run setup steps, expose helper functions, or change any data. Its value is structural: without it, some Python tooling or older Python import behavior might not recognize this directory as a package.


### `extensions/sweep/ufo_ext_sweep/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a folder can act like a named package when it contains an `__init__.py` file. That lets other parts of the project refer to code inside this folder using normal import paths, such as importing modules from `ufo_ext_sweep`. Think of it like a label on a drawer: the label does not contain the tools, but it tells Python that the drawer is meant to be opened as one organized unit. Nothing runs here, no settings are created, and no functions or classes are exposed directly from this file. Its value is structural: without it, some Python environments or tooling might not recognize this directory as an importable package.


### `extensions/ufo/ufo_ext_ufo/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Think of it like a label on a box: the label does not contain the tools, but it lets the rest of the system find and open the box correctly. Because this file is empty, it does not run setup code, expose shortcuts, or change how the extension works. Its value is structural: without it, depending on the Python version and packaging setup, imports from `extensions/ufo/ufo_ext_ufo` might fail or behave differently. This matters because extension code often needs a stable package name that other parts of the project can refer to.


### `extensions/web/ufo_ext_web/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That lets other parts of the project refer to code in this directory using package-style imports, such as importing something from `extensions.web.ufo_ext_web`. Think of it like a label on a drawer: the label does not contain the tools, but it tells Python that the drawer is part of the organized project structure. Without this file, some Python environments or packaging tools might not recognize the folder as a package, which could make imports fail or make the web extension harder to distribute correctly. Since the file is empty, it performs no setup, exposes no shortcuts, and changes no runtime state.
