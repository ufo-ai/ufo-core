# Core, control, and pack import package markers  `stage-19.4`

This stage is quiet behind-the-scenes support. It does not start the program, run the main loop, or shut anything down. Instead, it sets up the “addresses” Python uses to find code. In Python, an __init__.py file marks a folder as a package, meaning other code can import from it by name.

The root markers create the main import doors: ufo_control for control code, ufo for the core system, and ufo_pack_yc for the YC pack. Inside the core tree, more markers divide the project into stable neighborhoods. ufo.ext is for extensions. ufo.loop and ufo.loop.prompts support the main loop and its prompt text. ufo.models holds model-related code. ufo.sandbox and ufo.sandbox.proxy mark isolated execution and proxy areas. ufo.schema, ufo.sdk, ufo.skills, ufo.sources, and ufo.surfaces mark their own import areas. ufo.tools also names the tools area and briefly says it contains the tool registry, handler context, and built-in tools.

## Files in this stage

### Top-level package roots
These markers establish the primary import boundaries for the control and core UFO packages.

### `control/src/ufo_control/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker. In Python projects, an `__init__.py` file tells Python that a folder should be treated as an importable package. That means other files can write imports such as `import ufo_control...` and Python will know where that package begins. Think of it like a label on a binder: the label does not contain the documents, but it tells you the binder is a named collection you can open and use. Because this file has no code, it does not start anything, configure anything, or change program behavior directly. Its value is structural: without it, some tooling or older Python import setups might not recognize `ufo_control` as a package, which could break imports elsewhere in the project.


### `core/src/ufo/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a directory with an `__init__.py` file is treated as a package, which means code elsewhere can import modules inside it using names like `ufo.some_module`. Think of it like putting a label on a folder so the Python import system knows the folder belongs on the project’s map. Because this file contains no code, it does not set up configuration, run startup logic, or expose helper functions. Its importance is mostly structural: without it, depending on the Python version and packaging setup, imports from the `ufo` package might fail or behave differently. It exists so the rest of the project can treat `core/src/ufo` as a coherent Python namespace.


### Core extension and loop namespaces
These package markers make extension code, loop orchestration code, and loop prompt assets importable.

### `core/src/ufo/ext/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means code elsewhere can refer to modules under `ufo.ext` using normal import paths. Think of it like a label on a drawer: the drawer may contain useful tools, but this label itself does not do the work. Without this file, some Python setups or packaging tools might not recognize the directory as part of the project’s import structure, which could make extension modules harder or impossible to import consistently. There are no functions, classes, settings, or side effects here. Its value is structural: it helps define the shape of the codebase.


### `core/src/ufo/loop/__init__.py`

`other` · `import time`

In Python, an `__init__.py` file is the front door to a folder of code. Even when it is empty, it tells Python that the folder should be treated as an importable package. Here, the package is named `ufo.loop`, which likely contains code related to loop or event-loop behavior elsewhere in the same directory. This file does not run setup code, expose shortcuts, or define shared values. Its main value is structural: without it, some Python tooling or older import rules might not recognize the folder as a package, and imports that expect `ufo.loop` to exist could fail. Think of it like a label on a drawer: the drawer may hold useful tools in other files, but this label lets the rest of the project find the drawer reliably.


### `core/src/ufo/loop/prompts/__init__.py`

`other` · `import time`

This is an empty Python package file. In Python, a file named `__init__.py` tells the language that the folder should be treated as an importable package. You can think of it like putting a label on a drawer: the drawer may contain useful things, but this label is what lets the rest of the program find and refer to it in an organized way.

Here, the drawer is `ufo.loop.prompts`, which likely contains code or resources related to prompts used during a loop in the larger system. This file does not create prompts, load files, configure settings, or run any logic. Its value is structural: without it, some Python environments or tooling might not recognize the folder as a package, and imports that expect `ufo.loop.prompts` to exist could fail or behave inconsistently.


### Core model, sandbox, and schema namespaces
These markers define importable boundaries for model definitions, sandbox infrastructure, sandbox proxy code, and schemas.

### `core/src/ufo/models/__init__.py`

`data_model` · `import time`

This is an empty package marker file. In Python, a file named `__init__.py` tells the language that the surrounding folder should be treated as an importable package. Here, it makes `core/src/ufo/models` available as `ufo.models` to the rest of the project.

Because the file is empty, it does not create classes, run setup code, or expose shortcuts for other modules. Its value is structural: it helps organize the project so model code can live under a clear namespace. Think of it like a label on a drawer. The label does not contain the tools, but it makes the drawer recognizable and usable by the rest of the workspace.

Without this file, depending on the Python version and packaging setup, imports that expect `ufo.models` to be a regular package could fail or behave differently. Keeping it present makes the package boundary explicit.


### `core/src/ufo/sandbox/__init__.py`

`other` · `import time`

This is an empty Python package marker. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That is the main job here: it tells Python and project tools that `core/src/ufo/sandbox` is a named part of the codebase. Without this file, some import styles or packaging tools might not recognize the `sandbox` folder consistently, especially in older Python setups or stricter build environments.

There is no runtime logic in this file. It does not create objects, load settings, or run sandbox code. Think of it like a label on a drawer: the label does not contain the tools, but it lets people and systems know the drawer exists and can be opened by name.


### `core/src/ufo/sandbox/proxy/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder with an `__init__.py` file is treated as an importable package, meaning code elsewhere can refer to modules inside `ufo.sandbox.proxy` using normal import paths. Think of it like putting a label on a drawer: the drawer may hold useful tools in other files, but this label is what lets the rest of the system find the drawer by name. Without this file, depending on the Python version and packaging setup, imports from this folder could fail or behave differently. Since it has no code, it does not create objects, run setup steps, or change state. Its main value is structural: it defines the package boundary for the sandbox proxy area of the project.


### `core/src/ufo/schema/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can act like an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to code under this directory using names like `ufo.schema.some_module`. Think of it like putting a label on a drawer: the drawer may contain useful papers, but this label mainly tells Python, “this drawer is part of the organized system.” Because the file is empty, it does not create objects, run setup code, or change behavior at runtime. Its value is structural: without it, depending on the Python version and packaging setup, imports from `ufo.schema` might fail or behave differently than expected.


### Core SDK and capability namespaces
These markers expose SDK, skill, source, surface, and tool-related code as stable importable packages.

### `core/src/ufo/sdk/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, that means code elsewhere can refer to modules under `ufo.sdk` using normal import paths.

Because the file is empty, it does not run setup code, expose shortcuts, or define shared values. Its job is more like putting a label on a drawer: it says “the SDK pieces live here,” but it does not contain the tools itself. Without this file, depending on the Python version and packaging setup, imports from `ufo.sdk` could become less predictable or fail in environments that expect traditional Python packages.


### `core/src/ufo/skills/__init__.py`

`other` · `import time`

This is an empty Python package marker file. In Python, a folder can contain an `__init__.py` file to say, “treat this folder as an importable package.” That matters because code elsewhere can then refer to modules under `ufo.skills` in a clean, organized way.

There is no executable logic here: no functions, classes, or setup code. Its value is structural. Think of it like a label on a drawer: the label does not do the work, but it lets people and tools know that the drawer contains a particular category of things. Without this file, depending on the Python version and packaging setup, imports involving `ufo.skills` might be less reliable or might not work in some environments.

Because it is empty, it has no runtime side effects. Importing `ufo.skills` simply succeeds as a package import and does not initialize any skill objects or load extra code.


### `core/src/ufo/sources/__init__.py`

`other` · `import time`

This is an empty Python package file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may hold useful tools in other files, and this label lets the rest of the program refer to that drawer by name. Here, the drawer is `ufo.sources`, which likely contains code related to different input sources elsewhere in the same directory. Because this file is empty, it does not create objects, run setup steps, or change behavior directly. Its importance is structural: without it, some Python environments or tooling might not recognize `ufo.sources` as a package, and imports that expect that package path could fail.


### `core/src/ufo/surfaces/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools, but the label itself does not do the work. Here, the drawer is `ufo.surfaces`, which is likely where surface-related modules live elsewhere in the project. Because this file is empty, importing `ufo.surfaces` does not set up extra state, expose shortcut names, or run custom startup code. Its value is structural: without it, some Python tooling or older import setups might not recognize this directory as a package, and imports that expect `ufo.surfaces` to exist could fail.


### `core/src/ufo/tools/__init__.py`

`other` · `import time`

This is a package marker file. In Python, an `__init__.py` file is what turns a folder into an importable package, meaning other code can refer to the folder as `ufo.tools`. This particular file does not run any setup code, define functions, or expose extra names. Its main value is orientation: the docstring acts like a label on a drawer, saying that the `tools` package is where the project keeps its tool registry, the context passed to tool handlers, and the built-in tool set.

In practical terms, this file helps make the surrounding folder a clear, named part of the system. Without it, depending on the Python version and packaging setup, imports involving `ufo.tools` could be less explicit or fail in some environments. More importantly for a newcomer, it provides a quick clue about the purpose of the package before they open the files inside it.


### Pack namespace marker
This marker establishes the YC pack as an importable UFO pack package.

### `packs/yc/ufo_pack_yc/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker file. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. That matters because other parts of the project may want to refer to code inside `packs/yc/ufo_pack_yc` using normal Python import paths. Think of it like putting a label on a folder in a filing cabinet: the label does not contain the documents, but it lets people find and refer to the folder reliably. Since this file contains no code, it does not run any setup steps, create any objects, or change program behavior directly. Its main value is structural: without it, some Python environments or tools might not recognize this directory as a package, which could make imports fail or behave inconsistently.
