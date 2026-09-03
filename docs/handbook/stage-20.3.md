# stage-20.3  `stage-20.3` (cross-cutting infrastructure)

This stage is quiet behind-the-scenes support. It is not part of startup, the main work loop, or shutdown. Instead, it makes sure Python can find the project’s main code areas when other files ask to use them. In Python, a package is a folder that can be imported, meaning other code can refer to modules inside it by name. These __init__.py files act like labels on storage boxes: they tell Python “this folder is part of the importable code structure.”

The top-level ufo marker opens the main package. The harness marker makes the testing or execution harness area importable. The host.ext marker exposes extension-related host code. The runtime marker opens the area used for execution-time logic, and runtime.turns marks the sub-area for turn-based runtime pieces. The schema marker makes schema-related definitions available. None of these files run meaningful code or change behavior. They simply connect the folder layout to Python’s import system so the real implementation files can be found cleanly.

## Files in this stage

### Package Namespace Markers
These files make the core UFO package namespaces importable without adding runtime behavior.

### `core/src/ufo/__init__.py`

`other` · `import time`

In Python, an `__init__.py` file is like a sign on a folder saying, “this folder is a package you can import from.” This particular file is empty, so it does not run setup code, expose shortcuts, or define shared names. Its main value is structural: it lets code elsewhere refer to things under `ufo` using normal import paths, such as importing submodules from the package. Without this file, some Python tools or older Python import rules might not recognize the directory as a regular package, which could make imports less predictable. Think of it as a blank cover page for a section of a handbook: it does not contain content itself, but it tells readers and tools that the pages inside belong together.


### `core/src/ufo/harness/__init__.py`

`other` · `import setup`

This is an empty package marker file. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, it makes `core/src/ufo/harness` available as `ufo.harness` to the rest of the project.

Nothing runs from this file directly, and it does not create classes, functions, or settings. Its value is structural: it gives the project a clear place for harness-related code, meaning code that likely helps set up or run parts of the system in a controlled way. An everyday analogy is a labeled folder in a filing cabinet: the label does not contain the documents, but it makes the folder recognizable and usable by the rest of the filing system.

Without this file, depending on the Python version and packaging setup, imports that expect `ufo.harness` to be a normal package could fail or behave differently. So even though it is empty, it helps keep the project’s module layout stable and predictable.


### `core/src/ufo/host/ext/__init__.py`

`other` · `import/package discovery`

This is an empty Python package file. In Python projects, a file named `__init__.py` tells Python that the folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools, and the label lets the rest of the system find them by name.

Here, the drawer is `ufo.host.ext`, which likely holds extension-related code for the host side of the system. Because this file is empty, it does not create objects, run setup steps, or change behavior when imported. Its value is structural: it makes imports predictable and gives the package a clear place to add shared package-level setup later if needed.

Without this file, depending on the Python version and packaging setup, imports from this folder might be less explicit or fail in some environments. Keeping it present helps the codebase communicate that `ext` is an intentional part of the project layout.


### `core/src/ufo/runtime/__init__.py`

`other` · `cross-cutting`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to modules under `ufo.runtime` using normal import paths. Think of it like putting a label on a drawer: the drawer may contain useful tools, but this label file itself does not perform any work. If this file were missing in environments that still rely on traditional Python package markers, imports from `ufo.runtime` could fail or behave differently. Since the file is empty, it defines no functions, classes, settings, or startup steps. Its value is structural: it helps organize the codebase and gives the runtime area a stable package name.


### `core/src/ufo/runtime/turns/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to code under `ufo.runtime.turns` using normal import paths. Think of it like a label on a filing cabinet drawer: the label does not contain the documents, but it tells the system that the drawer exists and can be opened. Because this file is empty, it does not run startup code, expose shortcut names, or change how the turn runtime works. Its value is structural: without it, depending on the Python version and packaging setup, imports from this folder could become less reliable or fail in environments that expect traditional package markers.


### `core/src/ufo/schema/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can contain an `__init__.py` file to say, “treat this folder as an importable module.” That matters because code elsewhere can then refer to this area using names like `ufo.schema` and import the schema pieces stored in nearby files. A schema is a description of the shape of data, like a form that says which fields are expected and what kind of values they should hold. This particular file does not create those rules itself; it simply makes the folder a recognized home for them. Without it, depending on the Python version and packaging setup, imports from this package could become less predictable or fail in some environments.
