# Core and Control Package Manifests  `stage-24.6`

This stage is quiet behind-the-scenes support. It does not start the program, run the main loop, or shut anything down. Instead, it makes the project’s folder structure visible to Python. Each __init__.py file is like a label on a drawer, telling Python, “you can import code from here.”

The top-level markers create the main import areas: ufo_control for the control side of the system, and ufo for the core system. Under ufo, the other markers open named drawers for different kinds of core code. ufo.ext is for extensions. ufo.loop is for the main looping machinery, and ufo.loop.prompts is for prompt-related pieces used by that loop. ufo.skills marks skill modules, ufo.sources marks source modules, and ufo.surfaces marks surface modules. ufo.tools marks the tools area and notes that it contains the tool registry, handler context, and built-in tools.

Together, these files form the import skeleton that lets the rest of the codebase find its parts reliably.

## Files in this stage

### Top-level namespaces
Package markers that establish the control and core UFO namespaces for imports.

### `control/src/ufo_control/__init__.py`

`other` · `import time`

Python uses `__init__.py` files as package markers. This empty file tells Python that the `ufo_control` folder is meant to be treated as one importable unit, like a labeled drawer that can contain many related tools. Without it, some Python environments or packaging setups might not recognize `ufo_control` as a normal package, and imports from this part of the project could fail or behave differently. There is no startup code, no configuration, and no hidden side effect here. Its job is simply structural: it helps the rest of the control code be found and loaded using standard Python import paths.


### `core/src/ufo/__init__.py`

`other` · `import/package discovery`

In Python, a folder often needs an `__init__.py` file to be treated as a package, meaning a named group of importable code. This file is that marker for the `ufo` package. Think of it like a blank label on a folder: the label does not do the work, but it tells Python and developers that the folder is meant to hold related pieces of the system. Because the file is empty, it does not set up any objects, run any startup logic, or expose a public shortcut API. Its value is structural: without it, some tooling or older Python import behavior might not recognize `ufo` as a normal package, which could make imports less predictable.


### Extension and loop packages
Core package markers for extension hooks, loop code, and loop prompt assets.

### `core/src/ufo/ext/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. That matters because code elsewhere can then refer to things under `ufo.ext` using normal Python import paths.

There is no setup code, no exported helper, and no hidden side effect here. Its job is more like putting a label on a drawer: the drawer may contain useful extension-related modules, but this label file itself does not do the work. Without it, depending on the Python version and packaging setup, imports from this directory could be less predictable or fail in environments that expect traditional Python packages.


### `core/src/ufo/loop/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python projects, a file named `__init__.py` often acts like a label on a folder, saying: “the files in here belong together as one importable package.” Here, that package is `ufo.loop`, which likely contains code related to the project’s loop or runtime cycle elsewhere in the same folder.

Because the file is empty, it does not run setup code, expose shortcuts, define classes, or change any state. Its value is structural rather than behavioral. Without it, depending on the Python version and packaging setup, imports that expect `ufo.loop` to be a regular package might fail or behave differently. Think of it like a blank cover page for a chapter: it does not tell the story, but it helps the book stay organized.


### `core/src/ufo/loop/prompts/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, that means code elsewhere can refer to modules inside `core/src/ufo/loop/prompts` using normal Python import paths.

There is no logic, setup, or hidden behavior in this file. Its value is structural: it helps organize prompt-related code under a clear namespace. Without it, depending on the Python version and packaging setup, imports from this folder could become less predictable or fail in some environments.

An everyday analogy is a label on a drawer. The label does not contain the tools, but it tells the rest of the system, “this drawer is part of the organized workspace, and you can look inside it by name.”


### Skills and sources packages
Package markers for skill-related modules and source-related modules in the core namespace.

### `core/src/ufo/skills/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder with an `__init__.py` file is treated as a package, which means code elsewhere can refer to files inside it using import paths such as `ufo.skills.something`. Think of it like putting a label on a drawer: the drawer may contain useful tools, but this label simply makes the drawer recognizable to the rest of the system. Because the file is empty, it does not run setup code, expose shortcuts, or change how the skill modules work. Its value is structural: without it, some Python environments or packaging tools might not reliably recognize `ufo.skills` as an importable package.


### `core/src/ufo/sources/__init__.py`

`other` · `import time / package discovery`

This is an empty package marker file. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Here, that means code elsewhere can refer to modules under `ufo.sources` using normal Python import paths.

There is no logic, setup code, or hidden work happening in this file. Its value is structural: it gives the project a clean place for source-related code to live and be imported consistently. You can think of it like a label on a drawer. The label does not contain the tools, but it makes the drawer part of the organized cabinet so other people know where to look.

Without this file, depending on the Python version and packaging setup, imports involving `ufo.sources` might be less reliable or fail in environments that expect traditional package markers.


### Surfaces and tools packages
Package markers for user-facing surfaces and tool infrastructure, including the registry and built-in tools area.

### `core/src/ufo/surfaces/__init__.py`

`other` · `import/package discovery`

This is an empty Python package initializer. In Python projects, a file named `__init__.py` tells Python that the folder should be treated as an importable package. Here, it makes the `core/src/ufo/surfaces` directory available as `ufo.surfaces` to the rest of the codebase.

Because the file is empty, it does not define classes, functions, constants, or setup steps. Its value is structural: it acts like a label on a drawer in a filing cabinet. The actual surface-related code lives in other files inside this folder, but this file makes the folder itself recognizable to Python’s import system.

Without this file, depending on the Python version and packaging setup, imports that expect `ufo.surfaces` to be a regular package could fail or behave differently. So while it does not do visible work during execution, it helps keep the project’s module layout clear and importable.


### `core/src/ufo/tools/__init__.py`

`other` · `cross-cutting`

This is a package marker file. In Python, a folder with an `__init__.py` file can be imported as a package, which means other parts of the project can refer to `ufo.tools` as one named area. Here, the file does not run setup code or define any functions. Its only content is a short documentation string explaining the purpose of the package: it groups together the project’s tool system pieces. Those pieces include a registry, which is like a sign-up sheet for available tools; a handler context, which carries the information a tool needs while it runs; and the built-in tool set, which are the ready-made tools shipped with the project. Without this file, depending on the Python packaging setup, imports of this folder as a package could be less clear or may not work in older tooling. Its main value is organizational: it gives newcomers and Python itself a named home for tool-related code.
