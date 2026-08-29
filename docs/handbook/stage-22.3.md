# Feature and automation extension package markers  `stage-22.3`

This stage is quiet behind-the-scenes support. It does not run the agent’s main work. Instead, these __init__.py files act like labels on drawers in a workshop. In Python, such a file tells the system “this folder is a package,” meaning other code can import tools from it.

Each marker opens the door to a different extension area. Brief pipeline, report digest, documents, research, and web mark places for reading, writing, summarizing, and online work. Browser and UFO mark more specialized computer-use behavior. Coding, debugger, and REPL mark developer tools for writing, testing, and interactive commands. Memory marks durable fact storage, while objectives marks goal-related features. Monitors and scheduled tasks mark automation that watches or runs things over time. Skill create marks agent-authored skills, and self-improvement marks offline review that can suggest human-approved prompt changes.

Together, these files are the project’s extension signposts. They make optional capability folders discoverable without adding behavior themselves.

## Files in this stage

### Agent-facing workflows
Package markers for extensions that expose user-facing brief and browser interaction capabilities.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/__init__.py`

`other` · `import time`

This is the package marker for the brief pipeline extension. In Python, a folder becomes an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to this extension by its package name instead of treating it as just a folder on disk. The file does not define any functions, classes, or runtime behavior. Its only content is a short documentation string saying that this package is the “Brief pipeline extension.” Without this file, depending on the Python version and project setup, imports for this extension could be less predictable or fail in environments that expect traditional package files. Think of it like a label on a drawer: it does not contain the tools itself, but it tells the system that the drawer exists and what it is for.


### `extensions/browser/ufo_ext_browser/__init__.py`

`other` · `import/package discovery`

This is the package doorway for the browser extension area. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package, meaning other parts of the project can refer to it by name. Here, the file contains only a short documentation string. That string explains the purpose of the package: it groups together the sandbox browser tools, computer-use tools, and the browser subagent profile. In plain terms, this folder is meant to hold the pieces that let the system act through a browser-like environment, such as viewing pages or interacting with web interfaces. Nothing is executed here, and no functions or classes are defined. Its value is organizational: without it, imports and documentation around this extension package could be less clear, and older Python tooling may not recognize the folder as a package.


### Development tooling
Package markers for coding and debugging extensions that support software-development workflows.

### `extensions/coding/ufo_ext_coding/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder often needs an `__init__.py` file so Python treats that folder as an importable package, meaning other code can refer to modules inside it by name. Think of it like a label on a drawer: the drawer may contain useful tools, but this label mainly tells Python, “this drawer belongs to the project and can be opened.” Without this file, some Python setups or packaging tools might not recognize `extensions/coding/ufo_ext_coding` as a package, which could make imports fail or make the extension harder to distribute. There are no functions, classes, settings, or startup actions here. Its value is structural: it gives the coding extension a clear package boundary.


### `extensions/debugger/ufo_ext_debugger/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder with an `__init__.py` file is treated as an importable package, which means code elsewhere can refer to modules inside `ufo_ext_debugger` using normal import paths. Think of it like a label on a drawer: the drawer may contain useful tools, but this label is what lets the rest of the system find the drawer by name. Because the file is empty, it does not set up state, define helper functions, or run startup logic. Its importance is structural: without it, some Python environments or tooling might not recognize this directory as a package, and imports for the debugger extension could fail or behave inconsistently.


### Documents and memory
Package markers for extensions that handle document features and durable fact recall.

### `extensions/documents/ufo_ext_documents/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools, but the label itself does not do any work. Without this file, depending on the Python version and packaging setup, other parts of the project might not be able to reliably import modules under `extensions/documents/ufo_ext_documents`. Because the file is empty, it does not create objects, run setup code, change settings, or expose a public shortcut API. Its value is structural: it helps the document extension live in the project as a named Python package.


### `extensions/memory/ufo_ext_memory/__init__.py`

`other` · `cross-cutting`

This file does not contain executable code. Its job is to introduce the memory extension to Python and to readers of the project. In Python, an `__init__.py` file tells the interpreter that a folder should be treated as a package, meaning its code can be imported as one unit. Here, the package-level note explains the extension’s main responsibilities: keeping long-lived facts, recalling those facts when a user submits a prompt, deriving memory-related information when a page changes, and running a memory-index job. In plain terms, this extension acts like a notebook for the system. It can remember useful facts beyond a single interaction, look them up when the user says something new, update understanding when the surrounding page changes, and organize remembered material so it can be found later. Without this package marker, other parts of the project could have trouble importing the memory extension cleanly. Without the behaviors described here, the system would be more forgetful and less able to use past information in future interactions.


### Runtime control
Package markers for monitoring, objective management, and interactive REPL support.

### `extensions/monitors/ufo_ext_monitors/__init__.py`

`other` · `import time`

In Python, an `__init__.py` file is a small signpost that says, “this folder is a package.” A package is a named group of Python files that can be imported together. This particular file is empty, which means it does not set up any values, run startup logic, or expose helper functions directly. Its main job is structural: it allows code elsewhere to refer to this directory using normal Python import paths. Without it, depending on the Python version and packaging setup, imports involving `extensions.monitors.ufo_ext_monitors` could fail or behave differently. You can think of it like a blank cover page in a binder: it does not contain instructions itself, but it makes the collection recognizable as one organized unit.


### `extensions/objectives/ufo_ext_objectives/__init__.py`

`other` · `import time`

This is the package entry file for the objectives extension. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package. That means other parts of the project can refer to this extension using its package name, rather than treating it as just a loose directory of files.

Here, the file contains only a short documentation string: “The objectives extension.” It does not import other modules, create objects, register features, or run setup code. Its main value is structural. It is like a label on a folder in a filing cabinet: the label does not do the work inside the folder, but it makes the folder recognizable and usable by the rest of the system.

Without this file, depending on the Python version and packaging setup, code that expects `ufo_ext_objectives` to be a normal package might fail to import it or behave differently. The actual objective-related logic lives in other files in this package.


### `extensions/repl/ufo_ext_repl/__init__.py`

`other` · `package import`

This file is intentionally empty. In Python projects, a file named `__init__.py` is commonly used to say, “this folder is a package,” meaning other code can import modules from it. Think of it like a label on a drawer: the label does not contain the tools, but it tells the system that the drawer belongs to the organized set of project parts.

For this extension, the actual REPL-related behavior lives elsewhere in the `ufo_ext_repl` package. This file does not start the REPL, register commands, load settings, or expose helper functions. Its main value is structural: it keeps the package layout clear and compatible with tools that expect Python packages to contain an `__init__.py` file.

If this file were removed, modern Python might still import the directory in some situations using “namespace packages,” but older tools, packaging systems, or project conventions could fail to recognize it properly. So while it looks like nothing, it helps make the extension reliably discoverable.


### Research outputs
Package markers for research-oriented extensions and report digest generation.

### `extensions/report_digest/ufo_ext_report_digest/__init__.py`

`other` · `import time`

This is an empty Python `__init__.py` file. In Python projects, a file with this name tells Python that the folder should be treated as an importable package, like putting a label on a drawer so the rest of the program knows where to find things inside it. Without this file, depending on the Python version and project setup, imports from `ufo_ext_report_digest` might not work reliably. There is no setup code, no shared constants, and no functions here. Its value is structural: it helps the report digest extension exist as a named module that other files can refer to.


### `extensions/research/ufo_ext_research/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder often needs an `__init__.py` file to be treated as an importable package, meaning other parts of the project can refer to code inside it using a dotted name like `ufo_ext_research.some_module`. Think of it like a label on a drawer: the label does not contain the tools, but it tells Python that the drawer is part of the organized toolbox. Because the file is empty, it does not run setup code, expose shortcuts, or change how the research extension works. Its main value is structural: without it, some Python environments or tools might not reliably recognize this directory as a package, which could make imports fail or make packaging and discovery less predictable.


### Automation and capability growth
Package markers for scheduled automation, self-improvement review, and agent-authored skill creation.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/__init__.py`

`other` · `import time`

This file is intentionally empty. In Python, an `__init__.py` file tells the interpreter that a folder should be treated as a package, which is a group of importable code files. Think of it like a label on a drawer: the drawer may contain useful tools in other files, and this label lets the rest of the project find them reliably. Without this file, some Python setups or packaging tools might not recognize `ufo_ext_scheduled_tasks` as an importable package, which could stop the scheduled tasks extension from loading correctly. There is no runtime behavior here, no setup code, and no hidden logic. Its value is structural: it makes the package boundary explicit.


### `extensions/self_improvement/ufo_ext_self_improvement/__init__.py`

`other` · `import time`

This file does not contain executable logic. Its main value is as the front door label for the self-improvement extension. In Python, an `__init__.py` file tells the language that a folder should be treated as an importable package, which means other parts of the system can refer to this extension by name.

The docstring says what this package is about: an offline replay evaluation loop. In plain terms, that means the extension looks back at saved records of earlier work, called trajectories, rather than acting live in the middle of a user request. It uses those records to judge whether prompts could be improved. A prompt is the instruction text given to an AI model to guide its behavior.

Importantly, the description also says changes are governed and require approval from a member. So this extension is not silently rewriting how the system behaves. It prepares proposed prompt changes and routes them through a human decision point. Without this package marker and summary, the extension would be harder to import and harder for newcomers to recognize as the part of the project focused on safe, reviewable self-improvement.


### `extensions/skill_create/ufo_ext_skill_create/__init__.py`

`other` · `import time`

This file does not define any executable code. In Python, an `__init__.py` file tells the interpreter that a folder should be treated as a package, meaning other parts of the project can import code from it in an organized way. Here, the package is named `ufo_ext_skill_create`, and its docstring describes the package’s theme: skills created by an agent, represented as objects, plus skills available during each turn of runtime. In plain terms, this is like a label on a toolbox drawer. The drawer may contain the real tools in other files, but this label tells readers and Python what kind of tools belong there. Without this file, depending on the Python version and import setup, code elsewhere might not be able to import this folder as a normal package, or the package’s purpose would be less clear to newcomers.


### UFO and web adapters
Package markers for UFO-specific and general web extension behavior.

### `extensions/ufo/ufo_ext_ufo/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package. Think of it like putting a label on a drawer: the drawer may contain useful tools, but this label is what lets other parts of the program refer to the drawer by name. Because the file is empty, it does not run setup code, expose shortcuts, or change how the package works. Its main value is structural: without it, some Python environments or packaging tools might not recognize `extensions/ufo/ufo_ext_ufo` as a package, and imports from this folder could fail or behave differently.


### `extensions/web/ufo_ext_web/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder often needs an `__init__.py` file to be treated as an importable package, meaning other code can refer to modules inside it using names like `ufo_ext_web.something`. Think of it like putting a label on a folder so the rest of the system knows, “this folder is part of the program.”

Because this file has no code, it does not run setup steps, define settings, or expose helper functions. Its value is structural: without it, some Python tools or older import setups might not recognize `extensions/web/ufo_ext_web` as a proper package. That could make imports fail even if the real code files are present nearby.

So this file matters mostly during loading and packaging. It helps the web extension fit cleanly into Python’s module system, but the actual web extension behavior lives in other files.
