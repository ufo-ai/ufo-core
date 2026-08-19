# Core UFO package namespace markers  `stage-21.4`

This stage is behind-the-scenes support for the whole project. It does not start the app, run the main loop, or shut anything down. Instead, it sets up the folder “signposts” Python needs so code can be imported by name. In Python, an __init__.py file marks a folder as a package, meaning other files can refer to it using paths like ufo.models or ufo.tools.

The top-level ufo marker opens the main package. The ext, loop, models, sandbox, schema, sdk, sources, and surfaces markers do the same for their own areas, such as extension code, the main loop, model code, sandbox code, data schemas, developer-facing SDK code, source connectors, and surface integrations. The loop.prompts marker makes prompt-related code inside the loop area importable. The tools marker also identifies the tools area and briefly documents that it contains the tool registry, tool-running context, and built-in tools. Together, these files act like labeled doors in a building: they do not do the work, but they let the rest of the system find the right rooms.

## Files in this stage

### Root namespace
The top-level package marker establishes the main `ufo` import namespace without runtime behavior.

### `core/src/ufo/__init__.py`

`other` · `import/package discovery`

This is an intentionally empty package file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. That means other parts of the system can write imports that start with `ufo`, and Python knows where to look. Think of it like a label on a drawer: the drawer may hold many useful tools, but this label simply makes the drawer findable. Because the file has no code, it does not set up state, define helper functions, or change behavior when imported. Its value is structural: without it, some Python environments or packaging tools might not recognize `core/src/ufo` as a proper package, which could make imports fail or behave differently.


### Extension and loop namespaces
These package markers expose extension, loop, and prompt-related subpackages for normal imports.

### `core/src/ufo/ext/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, it makes the `core/src/ufo/ext` directory available as `ufo.ext` to the rest of the codebase.

The folder name `ext` likely stands for “extensions” or external integration points, but this file does not define any extension behavior itself. Its job is more like putting a label on a drawer: the drawer may contain useful tools, but the label is what lets the system find and open it by name.

Without this file, depending on the Python version and packaging setup, imports that expect `ufo.ext` to be a regular package could fail or behave differently. Because the file is empty, importing `ufo.ext` has no side effects: it does not load settings, start services, register plugins, or change program state.


### `core/src/ufo/loop/__init__.py`

`other` · `import time`

In Python, a folder can act like a named section of the program when it is treated as a package. This file is the marker for the `ufo.loop` package. Even though it is empty, it still matters because it gives the project a clear place for code related to the system’s loop behavior, such as repeated processing or run-cycle logic, to live under one import path.

Think of it like a label on a drawer. The label does not store the tools itself, but it tells everyone where that category of tools belongs. Without this file in some Python setups, imports that expect `ufo.loop` to be a package could fail or behave differently. Since there are no functions, classes, or setup steps here, importing this package does not change program state or perform work.


### `core/src/ufo/loop/prompts/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, it makes `core/src/ufo/loop/prompts` available as the `ufo.loop.prompts` package.

Think of it like putting a label on a drawer: the drawer may contain useful items in other files, but this label is what lets the rest of the system refer to the drawer by name. Without this file, depending on the Python version and packaging setup, imports that expect `ufo.loop.prompts` to be a regular package could fail or behave differently.

Because the file is empty, it does not define any functions, classes, settings, or side effects. Its importance is structural rather than behavioral: it helps organize prompt-related code under a clear namespace.


### Core domain namespaces
These initializers mark model, sandbox, and schema areas as importable core subpackages.

### `core/src/ufo/models/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, that means code elsewhere can refer to this folder as `ufo.models`.

There are no classes, functions, settings, or side effects in this file. Its value is structural: it gives the project a clear place for model-related code, even if this particular file does not yet define anything. Think of it like a labeled folder in a filing cabinet. The label matters because it lets people and tools find what belongs there, even when the label itself does not contain documents.

Without this file, depending on the Python version and packaging setup, imports involving `ufo.models` might be less predictable or fail in some environments. Keeping it present makes the package layout explicit and stable.


### `core/src/ufo/sandbox/__init__.py`

`other` · `import time`

This is an empty Python package marker. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, it makes `core/src/ufo/sandbox` available as `ufo.sandbox` to the rest of the project.

There is no code inside this file, so it does not create objects, run setup steps, or change behavior directly. Its value is structural: it gives the project a clear place to group sandbox-related code. A sandbox usually means an isolated area where code can run with limits or separation from the main system, but the actual sandbox behavior lives in other files in this package, not here.

Without this file, depending on the Python version and packaging setup, imports that expect `ufo.sandbox` to be a regular package could fail or behave differently. Think of it like a labeled folder tab in a filing cabinet: the tab does not contain the documents, but it helps the rest of the system find the right folder.


### `core/src/ufo/schema/__init__.py`

`other` · `import/package discovery`

This is an empty Python package initializer. In Python, a file named `__init__.py` tells the language that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful documents, but the label itself does not do the work. Here, the label is for the `ufo.schema` package, which likely groups files that describe or validate structured data used elsewhere in the project. Because this file is empty, it does not define any functions, classes, settings, or side effects. Its value is organizational: without it, some Python tooling or older import rules might not recognize the folder as a package, and imports that expect `ufo.schema` to exist could fail.


### Integration and tool namespaces
These package markers expose SDK, source, surface, and tool-related namespaces, with tools also documenting its package role.

### `core/src/ufo/sdk/__init__.py`

`other` · `import time`

In Python, a folder often needs an `__init__.py` file to be treated as a package, meaning a named group of code that can be imported together. This file is that marker for the `core/src/ufo/sdk` folder. It is currently empty, so it does not run setup code, expose shortcut imports, or define any public objects of its own. Its value is structural: without it, some Python tooling or older import behavior might not recognize `ufo.sdk` as an importable package. You can think of it like a label on a drawer. The drawer may contain useful tools in other files, but this label tells Python, “this drawer belongs to the SDK part of the project.”


### `core/src/ufo/sources/__init__.py`

`other` · `import time`

This is an empty Python package marker. In Python, a folder often needs an `__init__.py` file to be treated as an importable package: a named area of code that other files can refer to. You can think of it like a label on a drawer. The drawer may contain useful tools in other files, but this label is what lets the rest of the program say, “look inside `ufo.sources`.”

Because the file is empty, it does not run setup code, expose shortcuts, or change how the source modules behave. Its value is structural: it keeps the package layout clear and import-friendly. Without it, depending on the Python version and packaging setup, imports that expect `ufo.sources` to be a normal package could fail or behave differently.


### `core/src/ufo/surfaces/__init__.py`

`other` · `import time`

In Python, an `__init__.py` file is like a label on a folder saying, “this folder is part of the program’s import system.” This particular file is empty, so it does not define any names, run any setup steps, or connect other modules together. Its value is structural: it lets code elsewhere refer to modules under `ufo.surfaces` using normal Python import paths. Without this file, depending on the Python version and packaging setup, imports from this directory might be less predictable or fail in environments that expect traditional packages. Think of it as a sign on a cabinet drawer. The drawer may contain useful tools in other files, but this sign tells Python that the drawer belongs to the organized set of project modules.


### `core/src/ufo/tools/__init__.py`

`other` · `cross-cutting`

This file does not contain working code. Its job is to act like a label on a drawer: it says that the `ufo.tools` package is where the project keeps its tool-related pieces. In this project, a “tool” likely means an action the system can call on demand, such as a built-in capability exposed through a registry. A registry is like a contact list: it lets the rest of the system look up which tools exist and how to call them. A handler context is the information passed along while a tool is being used, so the tool has the background it needs to do its job. Without this file, Python would not treat this directory in quite the same explicit package-like way, and newcomers would also lose a small but useful signpost explaining what this folder is about.
