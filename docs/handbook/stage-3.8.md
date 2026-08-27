# Knowledge, Automation, and Skill Package Roots  `stage-3.8`

This stage is shared behind-the-scenes support. It does not start jobs or run the main work loop. Instead, it provides “front doors” for extension packages. In Python, an __init__.py file tells the system that a folder can be imported as a package, like putting a label on a drawer so other code can find what is inside.

Most files here are simple labels. The app metrics, app radar, monitors, research, report digest, scheduled tasks, gbrain, objectives, and brief pipeline initializers make those extension folders importable. A few also add a short human-readable description, but they still do not perform runtime setup. The office-xlsx scripts initializer does the same for script modules inside a document skill.

Three package roots also describe their extension’s purpose. The memory package is for long-lasting facts, recall during prompts, page-based updates, and background indexing. The self-improvement package is for learning from workspace activity and proposing human-approved prompt changes. The skill-create package is for agent-authored skills and skills available during a turn.

## Files in this stage

### Observability Package Roots
Package initializers for extensions that expose metrics, application radar, and monitor-related modules for import.

### `extensions/app_metrics/ufo_ext_app_metrics/__init__.py`

`other` · `import time`

This is an empty Python `__init__.py` file. Its main job is structural: it tells Python that the surrounding folder should be treated as an importable package. You can think of it like a label on a drawer. The drawer may contain useful tools in other files, but this label is what lets the rest of the program find and open it by name.

Because the file has no code, it does not calculate metrics, configure anything, or start any background work. Still, it matters because imports such as `ufo_ext_app_metrics.some_module` depend on the package existing. Without this file, depending on the Python version and packaging setup, parts of the app metrics extension might not be discovered or imported in the expected way.


### `extensions/app_radar/ufo_ext_app_radar/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to code inside `extensions/app_radar/ufo_ext_app_radar` using normal Python import paths. Think of it like putting a label on a folder so the system knows it is meant to be opened as part of the program, not just stored as loose files. Because the file has no code, it does not run setup steps, expose helper functions, or change program state. Its value is structural: without it, some Python environments or tooling might not recognize this directory as a package, which could make imports fail or make the extension harder to discover.


### `extensions/monitors/ufo_ext_monitors/__init__.py`

`other` · `import time`

This is an empty Python package marker. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. That matters because other parts of the system can then refer to modules inside `extensions/monitors/ufo_ext_monitors` using normal Python import paths.

There is no startup logic, configuration, or monitor behavior here. Think of it like a label on a drawer: the label does not contain the tools, but it lets the rest of the workshop know the drawer exists and can be opened by name. If this file were removed, imports may still work in some modern Python setups because of “namespace packages,” but keeping it makes the package boundary explicit and more compatible with tools and older expectations.


### Briefing and Document Roots
Package roots that make the brief pipeline extension and document skill script modules importable.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/__init__.py`

`other` · `startup/import time`

This is a very small package initializer. In Python, an `__init__.py` file tells the runtime that a folder should be treated as an importable package, which means other code can refer to this extension by name. Here, the file only contains a short documentation string: “Brief pipeline extension.” That acts like a label on a folder in a filing cabinet. It does not run a pipeline, load settings, or expose functions, but it still matters because it gives the extension a clear package identity. Without this file, depending on the Python version and how the project loads extensions, imports or discovery code may not recognize this directory in the expected way. Its main job is therefore structural: it makes the rest of the extension’s files belong to one named Python package.


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/__init__.py`

`other` · `cross-cutting import structure`

This is an empty Python package marker. In Python projects, a file named `__init__.py` tells Python that the folder should be treated as an importable package. Think of it like putting a label on a drawer: the drawer may contain useful tools, and the label lets the rest of the system find them by name.

Here, the drawer is the `scripts` folder inside the Office XLSX document skill extension. The file does not define functions, classes, settings, or startup behavior. Its value is structural: it helps keep the project organized and allows nearby script files, if present, to be imported using normal Python package paths.

Without this file, depending on the Python version and import style used elsewhere, code that expects `scripts` to be a package might fail to import modules from this directory. So although it looks empty, it supports the surrounding extension layout.


### Knowledge and Memory Roots
Package initializers for knowledge, persistent memory, and research-oriented extension modules.

### `extensions/gbrain/ufo_ext_gbrain/__init__.py`

`other` · `import time`

This is an empty Python package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. You can think of it like a label on a drawer: it does not contain tools itself, but it lets the rest of the system know the drawer exists and can be opened by name. Here, that drawer is `ufo_ext_gbrain`, which appears to be an extension area for GBrain-related code. Because the file is empty, importing this package does not run setup code, define shortcuts, or expose helper objects. Its value is structural: without it, some Python tooling or older import behavior might not recognize the folder as a normal package, which could make imports less predictable.


### `extensions/memory/ufo_ext_memory/__init__.py`

`other` · `extension import and cross-cutting memory behavior`

This file contains only a short package description, but that description is important because it names the main responsibilities of the memory extension. In plain terms, this extension is about helping the system remember useful facts over time instead of treating every interaction as brand new. It says the extension stores durable facts, meaning information meant to last beyond a single request or page. It can recall those facts when a user submits a prompt, using a `user_prompt_submit` hook, which is a plug-in point where extra behavior can run at a specific moment. It can also derive memory from page changes through a `page_change` hook, so updates in the surrounding workspace can become remembered knowledge. Finally, it includes a memory-index job, which likely prepares or organizes stored memories so they can be searched and reused efficiently. Without this package marker and summary, Python would not treat this directory as a normal importable package in older tooling, and newcomers would have less immediate context for what the extension is for.


### `extensions/research/ufo_ext_research/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means code elsewhere can refer to this folder using package-style imports, such as importing research extension modules under `extensions.research.ufo_ext_research`. Think of it like putting a label on a binder: the label does not contain the papers, but it tells the system that the binder is a real, named collection. Nothing would run from this file directly, and there are no functions or classes inside it. Its value is structural: it helps Python and project tooling recognize the directory as part of the codebase’s module tree.


### Objectives and Automation Roots
Package roots for objective handling, digest reporting, and scheduled task automation extensions.

### `extensions/objectives/ufo_ext_objectives/__init__.py`

`other` · `import/package discovery`

This is the package’s front door. In Python, an `__init__.py` file tells the interpreter that a folder should be treated as an importable package. Here, the file contains only a docstring: a short piece of text saying, “The objectives extension.” That means its main job is identification, not action.

Think of it like a label on a drawer. The useful tools are likely in other files inside the drawer, but this label helps Python and human readers know what the drawer is for. Without this file, depending on the Python version and project setup, importing this extension as a normal package could be less clear or could fail in stricter packaging situations.

There are no functions, classes, settings, or startup steps here. Nothing is computed, loaded, or changed when this file is imported beyond Python recognizing the package and making the docstring available for documentation tools or introspection.


### `extensions/report_digest/ufo_ext_report_digest/__init__.py`

`other` · `import/package discovery`

This is an empty Python package file. In Python projects, a file named `__init__.py` is commonly used to tell Python that a folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may hold useful tools in other files, but this label lets the rest of the program find the drawer by name.

Here, the package is `ufo_ext_report_digest`, which appears to belong to a report digest extension. Nothing is defined, configured, or run in this file. Its importance is structural: without it, some Python versions, tools, or packaging setups might not recognize this directory as a proper package, and imports that expect `ufo_ext_report_digest` to exist could fail.


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/__init__.py`

`other` · `startup/import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a box: the label does not do the work inside the box, but it lets the rest of the system find and refer to that box by name. Here, the box is the `ufo_ext_scheduled_tasks` extension, which likely contains code elsewhere for running tasks on a schedule. Without this file, some Python tooling or older import setups might not recognize the directory as a package, and imports from this extension could fail. Because the file is empty, importing it has no side effects: it does not start jobs, load settings, or register tasks by itself.


### Learning and Skill Roots
Package initializers for self-improvement workflows and agent-authored skill creation capabilities.

### `extensions/self_improvement/ufo_ext_self_improvement/__init__.py`

`other` · `import time`

This file does not contain working code. Its job is to introduce the package for the self-improvement extension. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package, like putting a label on a drawer so the rest of the program can find what is inside.

The short package description explains the extension’s purpose: it runs an offline replay evaluation loop. In plain terms, that means it looks back at saved records of earlier work sessions, called trajectories, and uses them to judge whether prompts could be improved. A prompt is the instruction text given to an AI system. Instead of changing prompts automatically, this extension opens governed prompt changes, meaning proposed edits go through a controlled approval step. A member must review and approve them before they take effect.

Without this file, the folder may not behave as a normal Python package in some environments, and newcomers would lose the one-line signpost explaining what this extension is for.


### `extensions/skill_create/ufo_ext_skill_create/__init__.py`

`other` · `import/package discovery`

This is a package marker file. In Python, an `__init__.py` file tells the language that the surrounding folder should be treated as an importable package, a named bundle of related code. Here, the bundle is for the `skill_create` extension, which appears to deal with created skills: reusable abilities authored by an agent, plus temporary runtime skills that exist during a single turn of work.

There is no executable code in this file. It does not define functions, classes, settings, or startup behavior. Its main value is organizational: it gives the package a clear identity and lets other parts of the project import modules from `extensions.skill_create.ufo_ext_skill_create`. Without this file, depending on the Python version and import setup, the package might be harder or impossible to import reliably.

The docstring acts like a label on a folder in a filing cabinet. It tells future readers what kind of code they should expect to find inside, even though the actual behavior lives in other files.
