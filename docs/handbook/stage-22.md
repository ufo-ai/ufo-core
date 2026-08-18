# Python package namespace markers  `stage-22`

This stage is quiet behind-the-scenes support. It is not part of startup, the main work loop, or shutdown. Instead, it sets up the project’s Python “package” layout. A package is a folder Python is allowed to import code from. These __init__.py files are like nameplates on office doors: they tell Python that the rooms exist, even though the nameplates do not do the work inside.

The ufo_control marker makes the control package importable. The main ufo marker does the same for the core runtime area. Inside it, ufo.ext opens the extension namespace, ufo.loop opens the loop-related namespace, and ufo.sandbox opens the sandbox namespace. The ufo.sandbox.proxy marker goes one level deeper, making proxy-related sandbox modules importable.

Together, these files create the project’s import map. Other code can reliably say “load this module from this package,” while these marker files add no runtime behavior of their own.

## Files in this stage

### Package namespace markers
Initializer files that establish importable namespaces for the control package and the core runtime subpackages without adding runtime behavior.

### `control/src/ufo_control/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can write imports that start with `ufo_control`, and Python will know this folder is the root of that package. Think of it like a label on a drawer: the drawer may contain many useful tools, but this label simply tells the system what the drawer is called. Because the file is empty, it does not run setup code, define shared values, or change how the package behaves when imported. Its value is structural: without it, some Python tools or environments may not recognize `ufo_control` as a normal package, which could make imports fail or behave inconsistently.


### `core/src/ufo/__init__.py`

`other` · `import time`

In Python, a folder usually needs an `__init__.py` file to be treated as a package: a named area of code that other files can import from. This file is empty, so it is like a label on a drawer rather than a tool inside the drawer. Its job is simply to say, “the files under `ufo` belong together and can be referred to as the `ufo` package.” Without this file, some Python environments or packaging tools might not recognize the directory in the expected way, which could make imports less reliable. Because it contains no code, it does not run setup steps, expose shortcuts, or change program behavior directly.


### `core/src/ufo/ext/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can act like an importable package when it contains an `__init__.py` file. That means code elsewhere can refer to this area as `ufo.ext` and import extension-related modules from inside it. Think of it like a label on a drawer: the label does not contain the tools, but it tells Python that the drawer exists and can be opened by name. Because the file is empty, it does not run setup code, expose shortcuts, or change how imports behave. Its value is structural: without it, some Python environments or tooling may not recognize `core/src/ufo/ext` as a regular package, which could make imports less predictable.


### `core/src/ufo/loop/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that a directory should be treated as an importable package. You can think of it like a label on a folder: it does not contain the papers itself, but it lets the rest of the program find and refer to the folder in a consistent way.

Here, the folder is `ufo.loop`, which likely contains code related to some kind of loop or repeated runtime activity elsewhere in the project. This file does not start that loop, configure it, or expose helper functions. Its job is only structural: it makes imports such as `ufo.loop.some_module` possible, depending on the Python version and packaging setup.

Without this file, some tooling, older Python environments, or package discovery systems might fail to recognize `ufo.loop` as a package. That could make imports less reliable even though the actual working code lives in neighboring files.


### `core/src/ufo/sandbox/__init__.py`

`other` · `import time`

This is an intentionally empty package file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. You can think of it like putting a label on a drawer: the drawer may contain useful tools, and the label lets the rest of the program find them by name. Because this file has no code, it does not create objects, run setup steps, or change program behavior directly. Its value is structural: without it, depending on the Python version and packaging setup, imports that expect `ufo.sandbox` to be a normal package could fail or behave differently. This matters because other sandbox-related modules can be grouped under the shared `ufo.sandbox` namespace.


### `core/src/ufo/sandbox/proxy/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to modules inside `ufo.sandbox.proxy` using normal Python import paths.

There is no code here, so it does not create objects, run setup steps, or change program behavior directly. Its value is structural: it tells Python and readers of the project that `proxy` is a named area of the sandbox system. You can think of it like a label on a drawer. The drawer may contain useful tools in nearby files, but this label is what lets the rest of the system find the drawer reliably.

Without this file, depending on the Python version and packaging setup, imports from this folder could become less predictable or fail in environments that expect traditional Python packages.
