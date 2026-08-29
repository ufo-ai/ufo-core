# Core package import markers  `stage-22.1`

This stage is quiet behind-the-scenes support. It is not part of startup, the main work loop, or shutdown. Its job is to make Python recognize folders as packages, meaning folders that other code can import from by name. Each __init__.py file is like a label on a drawer: it does not do the work inside, but it lets the rest of the system find that drawer.

The top-level ufo marker opens the main core package. The access, auth, billing, ext, kinds, media, models, onboard, runtime, sandbox, schema, sdk, skills, sources, surfaces, and turns markers do the same for their own feature areas. The loop marker makes the main loop code importable, and loop/prompts does this for prompt-related code inside it. The tools marker also labels the tools package and gives readers a small signpost toward the tool registry, handler context, and built-in tools. Together, these files create the import map that lets the real code connect cleanly.

## Files in this stage

### Package shell
Top-level and platform-adjacent package markers establish importable namespaces for core UFO services.

### `core/src/ufo/__init__.py`

`other` · `import time`

In Python, a folder can become an importable package by containing an `__init__.py` file. This file is that marker for the `ufo` package. Think of it like a label on a drawer: the drawer may hold many useful tools, but this label simply tells Python, “you can open this as a named package.” Because the file is empty, it does not run setup code, expose shortcuts, or change how imports behave. Its value is structural: without it, some Python environments and tools might not recognize `core/src/ufo` as a package, which could make imports fail or make packaging and test discovery less reliable.


### `core/src/ufo/access/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, that means code elsewhere can refer to things under `ufo.access` using normal import paths.

Because the file is empty, it does not run setup code, expose shortcuts, or change how the access-related modules work. Its value is structural: it is like a label on a drawer that says “access-related code lives here.” Without it, depending on the Python version and packaging setup, imports involving `ufo.access` could fail or behave less predictably.


### `core/src/ufo/auth/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Here, that means code elsewhere can refer to the authentication area as `ufo.auth` and then import specific modules inside it. Think of it like a label on a drawer: the drawer may contain useful tools, but the label itself does not do the work. If this file were missing in projects or environments that rely on traditional package markers, imports from `ufo.auth` could fail or behave differently. Because the file is empty, it does not set up authentication, define shared objects, or run startup code. Its value is structural: it helps organize the codebase and makes the authentication namespace available.


### `core/src/ufo/billing/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a folder can be treated as an importable package when it has an `__init__.py` file. That lets the rest of the project refer to code in this directory using names like `ufo.billing.some_module` instead of raw file paths. Think of it like a label on a drawer: the label does not contain the tools, but it tells Python that the drawer belongs to the organized set of project modules. Because this file is empty, it does not create objects, change settings, connect to services, or run billing rules. Its value is structural: without it, some Python environments or packaging tools might not recognize the billing folder as part of the `ufo` code package, and imports could fail or behave inconsistently.


### `core/src/ufo/ext/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package. Here, it makes the `core/src/ufo/ext` directory available as `ufo.ext` to the rest of the project. Think of it like a label on a drawer: the drawer may contain useful tools, but this label mainly lets people find and open it by name. Because the file has no code, it does not run setup steps, create objects, or expose helper functions directly. Its value is structural: without it, depending on the Python version and packaging setup, imports that expect `ufo.ext` to be a regular package could fail or behave differently.


### Core concepts
Concept, loop, prompt, media, and model package markers make the central interaction building blocks importable.

### `core/src/ufo/kinds/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Here, it makes the `ufo.kinds` namespace available to the rest of the project.

There is no code inside this file, so it does not create objects, run setup steps, or expose helper functions. Its value is structural: it is like a label on a drawer saying “the files inside belong together.” Without it, depending on the Python version and packaging setup, imports from `ufo.kinds` might fail or behave differently. Keeping the file also gives the project a clear place to add package-level setup later if that ever becomes necessary.


### `core/src/ufo/loop/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a folder can be treated as an importable package when it has an `__init__.py` file. That means other parts of the project can refer to code under `ufo.loop` using normal import statements. Think of it like a label on a drawer: the label does not do the work, but it lets everyone find the things stored inside. Because this file is empty, it does not set up shared objects, run startup code, or expose shortcut names. Its main value is structural: it makes the `loop` folder a clear, importable part of the `ufo` code layout. If it were removed, imports might still work in some modern Python setups, but behavior can become less explicit and less predictable across tools, packaging systems, and older assumptions.


### `core/src/ufo/loop/prompts/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. You can think of it like a label on a drawer: the drawer may contain useful prompt-related files, and this label lets the rest of the program find them by name. Because the file has no code, it does not create prompt text, load settings, or run any logic. Its value is structural. Without it, depending on the Python version and packaging setup, imports that expect `ufo.loop.prompts` to be a normal package could fail or behave differently. So this file matters mainly because it keeps the project’s module layout clear and import-friendly.


### `core/src/ufo/media/__init__.py`

`other` · `cross-cutting`

In Python, a folder can act like a named package when it has an `__init__.py` file. This file is that marker for the `ufo.media` area of the project. Think of it like a label on a drawer: the label does not store the tools, but it tells Python that the drawer exists and can be opened by name.

Because this file is empty, it does not define any classes, functions, settings, or startup behavior. Its value is structural. It lets code elsewhere refer to media-related modules using imports such as `ufo.media.something`, assuming such modules exist alongside it. Without this file, some Python environments or tooling may not treat the folder as a normal package, which could make imports, packaging, or code discovery less predictable.

So the important thing to know is what this file does not do: it does not process media, open files, talk to external services, or configure anything. It simply reserves and declares the package namespace for media-related code.


### `core/src/ufo/models/__init__.py`

`other` · `import time`

This is an empty Python package marker file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, that means code elsewhere can refer to modules under `ufo.models` using normal import paths.

There are no functions, classes, settings, or side effects in this file. Its value is structural rather than behavioral: it helps organize the project’s model-related code under one namespace. A namespace is like a labeled drawer in a filing cabinet; even if this particular label contains no instructions, it lets people and tools reliably find the files inside.

Without this file, depending on the Python version and packaging setup, imports involving `ufo.models` might fail or behave differently. Keeping it present makes the package layout explicit and predictable.


### Execution environment
Onboarding, runtime, sandbox, and schema package markers prepare the namespaces used around setup and execution.

### `core/src/ufo/onboard/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means code elsewhere can refer to this area as `ufo.onboard` and import the real modules that live inside it. Think of it like a label on a drawer: the label does not contain the tools, but it tells Python that the drawer is part of the organized toolbox. Without this file, depending on the Python version and packaging setup, imports from this folder might fail or behave differently. There are no functions, classes, settings, or startup actions here; its value is structural.


### `core/src/ufo/runtime/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python projects, an `__init__.py` file tells Python that a folder should be treated as an importable package. That means code elsewhere can refer to things inside `core/src/ufo/runtime` using names like `ufo.runtime.some_module`.

Because this file contains no code, it does not start anything, configure anything, or change runtime behavior directly. Its value is structural: it helps organize the project into named areas. You can think of it like a label on a drawer. The label does not do the work stored inside the drawer, but without it, people and tools may not reliably know how to find what is inside.

If this file were removed, imports may still work in some modern Python setups because Python supports some packages without `__init__.py`. However, keeping it makes the package boundary explicit and helps compatibility with tools that expect traditional Python packages.


### `core/src/ufo/sandbox/__init__.py`

`other` · `import time`

This is an empty Python package file. In Python projects, a file named `__init__.py` tells Python that the folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools, but this label mainly lets the rest of the system find the drawer by name. Here, it makes the `ufo.sandbox` namespace available, so code elsewhere can import modules that live under `core/src/ufo/sandbox/`. Because the file is empty, it does not run setup code, expose shortcuts, or change how sandbox features work. If it were missing in projects or tooling that expect traditional Python packages, imports involving `ufo.sandbox` could fail or become less predictable.


### `core/src/ufo/schema/__init__.py`

`other` · `import time`

This is an empty Python package marker. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, it makes the `core/src/ufo/schema` directory available as `ufo.schema` to the rest of the codebase.

There is no code inside this file, so it does not define behavior, load settings, or create objects. Its value is structural: it gives the schema folder a clear place in the project’s import tree. Without it, depending on the Python version and packaging setup, imports that expect `ufo.schema` to behave like a normal package could fail or behave differently.

A simple analogy is a label on a filing cabinet drawer. The label does not contain the documents, but it tells everyone that this drawer is a named place they can refer to consistently.


### Capabilities and interfaces
SDK, skills, sources, surfaces, and tools package markers expose the main extension and interaction namespaces.

### `core/src/ufo/sdk/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools, but this label itself does not do the work. Without this file, depending on the Python version and packaging setup, code that tries to import `ufo.sdk` or modules under it might fail or behave differently. Because it contains no functions, classes, or setup code, importing this package does not trigger any special action here. Its value is structural: it gives the project a clear place for SDK-related code and makes that code reachable through normal Python imports.


### `core/src/ufo/skills/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder with an `__init__.py` file is treated as an importable package, meaning code elsewhere can refer to modules inside it using names like `ufo.skills.some_module`. Think of it like putting a label on a drawer: the drawer may contain useful tools, but this label is what lets the rest of the system find it by name.

Because the file is empty, it does not set up any objects, run startup logic, or expose shortcuts for the modules inside the `skills` folder. Its value is structural rather than behavioral. Without it, depending on the Python version and packaging setup, imports involving `ufo.skills` might fail or behave differently. Keeping it here makes the package boundary explicit and helps tooling, packaging, and readers understand that this directory is meant to hold a coherent group of skill-related code.


### `core/src/ufo/sources/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools, but the label itself does not do the work. Without this file, depending on the Python version and packaging setup, code elsewhere in the project might not be able to reliably import modules from `ufo.sources`. Because it contains no code, it does not create objects, run setup steps, or change program state. Its main value is structural: it helps organize source-related code under one namespace.


### `core/src/ufo/surfaces/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means code elsewhere can write imports that refer to `ufo.surfaces` and to files inside this directory.

Because the file is empty, it does not set up defaults, expose shortcuts, or run any startup code. Its job is more like putting a label on a drawer: it tells Python, and readers of the project, that the files in this folder belong together under the `ufo.surfaces` namespace.

Without this file, depending on the Python version and packaging setup, imports involving this folder might behave differently or fail in some environments. Keeping it here makes the package structure explicit and predictable.


### `core/src/ufo/tools/__init__.py`

`other` · `cross-cutting`

This is a package marker file. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package, meaning other code can refer to this area as `ufo.tools`. This particular file does not define any functions or classes. Its only content is a short documentation string explaining the purpose of the package: it contains the pieces used to define and run tools. In this project, “tools” likely means callable abilities the system can expose, organize, and execute, while a registry is like a directory that keeps track of which tools exist. The handler context is the surrounding information a tool needs when it runs, and the built-in tool set is the collection that comes with the project by default. Without this file, depending on the Python packaging setup, imports from `ufo.tools` could be less clear or fail in older tooling. Its main value is structural: it gives this part of the codebase a named home.


### Turn records
The turns package marker closes the import structure with the namespace for turn-related modules.

### `core/src/ufo/turns/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder with an `__init__.py` file is treated as an importable package, meaning other parts of the project can refer to code inside this directory using names like `ufo.turns.something`. Think of it like a label on a drawer: the label does not contain the tools, but it tells Python that the drawer exists and can be opened in an organized way. Because this file is empty, it does not run setup code, expose shortcut imports, or change how the rest of the package behaves. Its value is structural: without it, depending on the Python version and packaging setup, imports from the `ufo.turns` folder might fail or behave less predictably.
