# Core non-runtime package markers  `stage-20.3`

This stage is quiet behind-the-scenes support. It does not start the system, run the main work, or shut anything down. Instead, it puts name tags on important folders so Python can import code from them. In Python, an `__init__.py` file marks a folder as a package, meaning other files can refer to it by name.

The top-level `ufo/__init__.py` opens the main `ufo` package. Under it, `harness/__init__.py` marks the test or execution harness area, with smaller package markers for `harness.auth`, `harness.models`, and `harness.sandbox` so authentication, model, and sandbox code can be imported cleanly. `host/__init__.py` is a signpost for the environment layer: the tools, extensions, skills, and prompts an agent can use during a turn. `host.ext` and `host.kinds` are marked as importable sub-areas. Finally, `onboard`, `schema`, and `sdk` are also marked as packages, preparing space for onboarding, data-shape definitions, and developer-facing SDK code.

## Files in this stage

### Top-level package root
Establishes the root UFO namespace that all other core package areas import through.

### `core/src/ufo/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a directory often needs an `__init__.py` file to be treated as a package, which means its contents can be imported using names like `ufo.something`. Think of it like a label on a folder: the label does not contain the documents, but it tells Python that the folder is part of the project’s importable code structure. Because this file is empty, it does not run setup code, expose shortcuts, or change how the rest of the package behaves. Its value is structural: without it, some Python tools or environments might not recognize `core/src/ufo` as a package, which could make imports fail or behave inconsistently.


### Harness namespaces
Marks the harness package and its authentication, model, and sandbox subareas as importable namespaces.

### `core/src/ufo/harness/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can write imports that start with `ufo.harness` and reach the files inside this directory.

There is no executable code here, no settings, and no functions. Its value is structural: it tells Python and readers of the project that the `harness` directory is meant to be a named part of the system. You can think of it like a label on a drawer. The label does not contain the tools, but it makes the drawer part of the organized workspace.

Without this file, depending on the Python version and import setup, code that expects `ufo.harness` to be a normal package might fail to import cleanly or behave differently. So this file matters mainly because it supports reliable package organization.


### `core/src/ufo/harness/auth/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a file named `__init__.py` tells the language, and the tools around it, that the folder should be treated as an importable package. Here, that means code elsewhere can refer to modules inside `ufo.harness.auth` in a clean, organized way.

There is no authentication logic in this file itself. It does not check passwords, issue tokens, configure permissions, or expose helper functions. Its value is structural: it creates a named place in the project for authentication-related harness code to live. You can think of it like a label on a drawer. The drawer may contain useful tools, but the label itself mainly helps people and the program find them.

Without this file, depending on the Python version and packaging setup, imports from this folder might be less reliable or less explicit. Keeping it here makes the package layout clear and leaves room for future shared setup if the `auth` package ever needs it.


### `core/src/ufo/harness/models/__init__.py`

`other` · `import time`

This is an empty Python package marker file. In Python projects, a file named `__init__.py` tells Python that the folder should be treated as an importable package. Here, it makes `ufo.harness.models` available as a package path, so code elsewhere can import files that live inside this `models` directory.

There is no logic, setup, or data defined here. Its value is structural: it is like putting a label on a drawer so the rest of the system knows where to look for a certain category of code. Without this file, depending on the Python version and project setup, imports from this folder might be less explicit or fail in environments that expect traditional package markers.


### `core/src/ufo/harness/sandbox/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Here, it makes `ufo.harness.sandbox` a valid package path, so files inside this folder can be reached by other code using normal Python imports. Think of it like a label on a drawer: the drawer may contain useful tools, but this label is what lets the rest of the workshop find it by name. Because the file is empty, it does not run setup code, expose shortcut imports, or change any state. If it were missing in some Python environments or packaging setups, imports that rely on `ufo.harness.sandbox` being a package could fail.


### Host namespaces
Defines the host environment package area and its extension and kind subpackages as importable locations.

### `core/src/ufo/host/__init__.py`

`other` · `cross-cutting`

This file does not contain executable logic. Its job is to label a folder as a Python package and explain, in one sentence, what kind of code lives there. In this project, the `ufo.host` package is described as the “environment layer.” In plain terms, that means it is the boundary around what an agent has available when it is taking a turn: outside capabilities, callable tools, reusable skills, and the prompts that shape its behavior.

Think of it like the workbench around a craftsperson. The agent is the craftsperson, and this package is where the project organizes the tools, instructions, and helper abilities placed on the bench before work begins. Without this file, Python package discovery may be less clear in some setups, and newcomers would lose a useful signpost telling them what this area of the codebase is meant to represent.

Because it only contains a documentation string, it has no runtime behavior beyond being imported as a package marker.


### `core/src/ufo/host/ext/__init__.py`

`other` · `import/package discovery`

This file does not contain executable code, but it still has a job. In Python projects, an `__init__.py` file tells Python that a folder should be treated as an importable package. Here, it makes the `core/src/ufo/host/ext` folder available as `ufo.host.ext` to the rest of the project.

Think of it like a label on a drawer. The drawer may contain useful tools in other files, and the label lets the program find that drawer by name. Without this file, depending on the Python version and packaging setup, imports that expect `ufo.host.ext` to be a normal package could fail or behave differently.

Because the file is empty, it does not set up shared variables, expose shortcuts, or run any startup code. Its purpose is purely organizational: it reserves a clear place in the codebase for host extension-related modules.


### `core/src/ufo/host/kinds/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools, but the label itself does not do any work. Without this file, some Python setups or packaging tools might not recognize `core/src/ufo/host/kinds` as a package, which could make imports from this area fail or behave inconsistently. Because the file is empty, it does not run setup code, expose shortcuts, or change any state when imported. Its value is structural: it helps organize the project’s code into a clear namespace for “host kinds,” while leaving the actual definitions and logic to other files in the same package.


### Supporting core namespaces
Marks the onboarding, schema, and SDK package areas that support non-runtime core integrations.

### `core/src/ufo/onboard/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to code inside this directory using names like `ufo.onboard.some_module` rather than dealing with raw file paths. Think of it like a label on a drawer: the drawer may contain useful tools, but this label only tells Python that the drawer exists and can be opened through the normal import system. Because the file is empty, it does not run setup code, expose shortcut imports, or change how the onboard subsystem behaves. If it were missing in environments that still rely on traditional package markers, imports from `ufo.onboard` could fail or behave differently.


### `core/src/ufo/schema/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can act like an importable package when it contains an `__init__.py` file. That means other parts of the project can write imports that refer to `ufo.schema` and reach the schema code stored in this directory. A schema is usually a formal description of data shape, like a template that says what fields are expected and what kind of values they should contain. This file does not create those templates itself; it simply makes the surrounding folder visible as a named package. Think of it like a label on a drawer: the label does not contain the documents, but it lets people find and refer to the drawer reliably. Without this file, depending on the Python version and packaging setup, imports involving `ufo.schema` could fail or behave inconsistently.


### `core/src/ufo/sdk/__init__.py`

`other` · `import/package discovery`

In Python, a folder can be treated as an importable package when it has an `__init__.py` file. This file plays that role for the `ufo.sdk` package. Think of it like a label on a drawer: it tells Python, “the files in this drawer belong together and can be reached through this package name.”

Because the file is empty, it does not create any shortcuts, run any setup work, or expose any public objects directly. Its value is structural rather than behavioral. Without it, depending on the Python version and packaging setup, imports that expect `ufo.sdk` to behave as a normal package could fail or behave differently. Keeping the file present makes the package layout explicit and predictable for both Python and human readers.
