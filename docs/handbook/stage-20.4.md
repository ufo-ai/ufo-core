# Runtime package markers  `stage-20.4`

This stage is behind-the-scenes support for the codebase. It does not start the app, run the main loop, or shut anything down. Instead, it lays out the project’s runtime “neighborhoods” so Python can find them. In Python, an __init__.py file marks a folder as a package, meaning other code can import files from it by name.

The main runtime marker opens the ufo.runtime area. Inside it, separate markers reserve clear spaces for access, billing, extension APIs, kinds, media, prompts, skills, sources, surfaces, tools, and turns. Most of these files contain no working code. Their job is like putting labels on drawers, so later modules know where to store and find related parts.

Two tool markers add a little more explanation. The host tools package points to the host-side tool registry, handler context, and built-in tools. The runtime tools package names the shared tool contract, dispatch context, and registry that connect tool names to their implementations. Together, these marker files keep the runtime layout importable and organized.

## Files in this stage

### Package entry markers
Top-level markers establish importable host tooling and runtime namespaces before the runtime subpackages are introduced.

### `core/src/ufo/host/tools/__init__.py`

`other` · `import time`

This is a very small package marker file. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package. Here, it does not define any code of its own. Its docstring acts like a sign on a drawer: it says that this package contains the pieces related to tools. In this project, “tools” likely means callable capabilities that the host can offer to other parts of the system, such as built-in actions, a registry that keeps track of available tools, and context passed to tool handlers when they run. Without this file, depending on the Python packaging setup, imports from `ufo.host.tools` might be less clear or might not work in older tooling. The file matters mostly for organization and discoverability: it gives the `tools` package a named place in the codebase, even though the real behavior lives in neighboring files.


### `core/src/ufo/runtime/__init__.py`

`other` · `import time`

In Python projects, an `__init__.py` file is like a label on a folder that says, “this folder is part of the importable program.” This particular file is empty, so it does not define functions, classes, settings, or startup steps. Its value is structural: it helps Python and developer tools recognize `core/src/ufo/runtime` as a package namespace. Without it, some import styles or tooling may not treat the folder the same way, especially in environments that expect traditional Python packages. Think of it like a blank cover page for a section in a handbook: it does not contain instructions itself, but it makes the section easy to find and refer to.


### Runtime platform namespaces
Core runtime namespace markers cover access, billing, extension APIs, and kind definitions used by the platform.

### `core/src/ufo/runtime/access/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. That matters because other parts of the project can then refer to code inside this folder using a path like `ufo.runtime.access.some_module`.

There is no runtime logic here: no functions, classes, configuration, or side effects. Think of it like a label on a drawer. The label does not contain the tools, but it lets the rest of the workshop find the drawer reliably.

Without this file, depending on the Python version and packaging setup, imports from the `access` folder might fail or behave differently. Keeping it present makes the project structure explicit and helps tools, tests, and developers understand that `access` is meant to be a real part of the `ufo.runtime` package.


### `core/src/ufo/runtime/billing/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` is commonly used to tell the language, tools, and readers that a directory is meant to be treated as a package: a named group of related code. Here, that group is `ufo.runtime.billing`, which suggests it is the home for billing-related runtime behavior elsewhere in the project.

Because the file is empty, it does not set up billing, load configuration, define classes, or run any logic. Its value is organizational. It acts like a label on a folder in a filing cabinet: the label does not contain the documents, but it tells people and tools where a category begins.

Without this file, imports might still work in some modern Python setups because Python supports “namespace packages,” but many tools, older assumptions, and code readers still rely on `__init__.py` as the clear signal that this directory is an intended package boundary.


### `core/src/ufo/runtime/ext/__init__.py`

`other` · `import time`

This file is intentionally minimal. Its main job is to label the surrounding package: `ufo.runtime.ext` is where the project keeps its extension API. An extension API is the agreed doorway between the main platform and add-on code. It says, in effect, “Here is what outside or built-in extension code is allowed to use, and here is the shape of the things it can provide.”

There is no executable logic here. The file contains only a short package docstring, which acts like a sign on a cabinet drawer. The actual tools, types, and rules for extensions are expected to live in other files inside this package. Without this file, readers and tools would have less context about the purpose of the folder, and in some Python setups the folder might not be treated as an importable package in the expected way.

So this file matters less because of what it runs, and more because of what it declares: this part of the codebase is the boundary where the platform and its extensions meet.


### `core/src/ufo/runtime/kinds/__init__.py`

`other` · `import time`

This file is intentionally empty. In Python, an `__init__.py` file is commonly used to tell Python that a folder should be treated as a package, meaning a named collection of modules that can be imported together. Here, it gives the project a place called `ufo.runtime.kinds`, where related runtime “kind” definitions can live.

The file does not define any functions, classes, or settings. Its value is structural rather than behavioral. A helpful analogy is a labeled folder in a filing cabinet: the label does not contain the documents, but it makes the folder recognizable and usable by the rest of the system.

Without this file, depending on the Python version and packaging setup, imports from this directory might be less explicit or could fail in some environments. Keeping it present makes the package layout clear to both Python and human readers.


### Runtime capability namespaces
Capability package markers make media, prompts, skills, sources, surfaces, tools, and turns importable as runtime domains.

### `core/src/ufo/runtime/media/__init__.py`

`other` · `import/package discovery`

In Python, an `__init__.py` file is like a nameplate on a folder: it tells Python, and readers, that the folder is meant to be treated as a package of related code. This particular file is empty, so it does not set up any objects, run startup logic, or expose shortcuts for imports. Its value is structural. Without it, depending on the Python version and packaging setup, imports that expect `ufo.runtime.media` to be a regular package could fail or behave differently. In practical terms, this file helps the project keep its media runtime code grouped under a clear namespace, even though the actual work is done in other files inside the same folder.


### `core/src/ufo/runtime/prompts/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a file named `__init__.py` tells the language that the folder should be treated as an importable package. Here, it makes `core/src/ufo/runtime/prompts` available as a place where prompt-related modules can live and be imported using normal Python package paths. Think of it like a label on a drawer: the label does not store the tools itself, but it tells the rest of the workshop that this drawer exists and can contain prompt tools. If this file were removed, imports may still work in some modern Python setups, but keeping it makes the package structure explicit and compatible with tools or environments that expect traditional Python packages.


### `core/src/ufo/runtime/skills/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as a package, which is a named group of modules that can be imported together. Think of it like putting a label on a folder in a filing cabinet: the label does not contain the documents, but it lets people find and refer to the folder reliably.

Here, the folder is `ufo.runtime.skills`. Other files can import modules from this area because this file exists. Since the file is empty, it does not run setup code, expose shortcuts, or define shared names. That is often intentional: it keeps package loading simple and avoids hidden side effects when someone imports `ufo.runtime.skills`.

Without this file, depending on the Python version and packaging setup, imports involving this folder could become less predictable or fail in environments that expect traditional Python packages.


### `core/src/ufo/runtime/sources/__init__.py`

`other` · `import/package discovery`

This is an empty `__init__.py` file. In Python, a file with this name tells the interpreter that the folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools in other files, and this label lets the rest of the program refer to the drawer by name.

Because the file has no code, it does not create objects, run setup steps, or change behavior directly. Its value is structural. Without it, depending on the Python version and packaging setup, imports such as `ufo.runtime.sources...` might fail or behave differently. Keeping it here makes the package layout explicit and helps tools, tests, and readers understand that this directory is meant to contain source-related runtime modules.


### `core/src/ufo/runtime/surfaces/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package. Here, it makes the `ufo.runtime.surfaces` namespace available to the rest of the project.

The word “surfaces” likely refers to a group of runtime components stored in nearby files, but this particular file does not create, configure, or connect those components. Its job is more like putting a label on a drawer: the drawer may contain useful tools, but the label itself does not do the work.

Without this file, depending on the Python version and packaging setup, imports that expect `ufo.runtime.surfaces` to be a normal package could fail or behave differently. Keeping the file present makes the package structure explicit and stable for readers, tools, and import logic.


### `core/src/ufo/runtime/tools/__init__.py`

`other` · `import time`

This file does not define any working code by itself. Its main job is to act as the front door for the `tools` package and to document, in one sentence, what belongs there. In Python, an `__init__.py` file tells Python that a folder should be treated as a package, meaning other parts of the project can import from it as a named area of the codebase. Here, that area is for the system’s “tools”: pieces of functionality that can be called through a shared contract. The docstring points readers to the key ideas owned by this package: the contract that says what a tool must look like, the dispatch context that carries information needed when a tool is run, and the wire registry that likely maps tool identifiers to the code that performs them. Think of it like a label on a drawer: the drawer may contain many useful parts, but this file mainly tells you what kind of parts to expect inside.


### `core/src/ufo/runtime/turns/__init__.py`

`other` · `import time`

This is an empty package file. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable module, a named area of code. Here, it makes the `turns` folder available as `ufo.runtime.turns`.

There is no logic inside this file: no functions, classes, or setup steps. Its value is structural. It is like a label on a drawer: the label does not do the work, but it lets the rest of the system reliably find what is stored in that drawer.

Without this file, depending on the Python version and packaging setup, imports that expect `ufo.runtime.turns` to be a regular package might fail or behave differently. So this file helps keep the project’s module layout clear and stable, even though it does not directly process data or control execution.
