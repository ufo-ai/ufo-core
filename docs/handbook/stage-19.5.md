# User-facing capability extension package markers  `stage-19.5`

This stage is shared behind-the-scenes support. It does not run the main product by itself. Instead, it puts “front doors” on many extension folders so Python can recognize and import them as packages. A package is simply a folder that Python treats as a named bundle of code.

Most files here are __init__.py markers. The browser marker identifies the browser extension and points to browser, computer-use, and browser-agent tools. Its bua marker opens a smaller browser-use area for imports. Coding, documents, knowledge graph, research, sites, Slack, UFO, web, and YC each have similar markers so their tools can be found by the rest of the system. The documents extension also marks script folders for document review, PowerPoint, and spreadsheet skills, making those helper scripts importable. The memory marker also describes its role: storing long-term facts, recalling them during prompts, updating memory from pages, and indexing memory. The skill-create marker describes support for making and exposing user-created skills. Together, these files act like labels on tool drawers, letting later stages open the right drawer when needed.

## Files in this stage

### Browser package markers
Browser-facing package markers establish the main browser extension namespace and its browser-use automation subpackage.

### `extensions/browser/ufo_ext_browser/__init__.py`

`other` · `import time`

This is the package marker for the browser tool extension. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package. Here, it does not define any functions, classes, or setup code. Its main job is to give a short human-readable summary of the package: this area contains tools for working with a sandbox browser or computer-use environment, along with a browser-focused subagent profile. You can think of it like a label on a drawer. The drawer holds the actual tools elsewhere, while this file tells readers and Python that the drawer exists and what kind of things belong in it. If this file were removed, depending on the Python version and packaging setup, imports from this folder might become less explicit or fail in environments that expect traditional Python packages.


### `extensions/browser/ufo_ext_browser/bua/__init__.py`

`other` · `import/package discovery`

This is an empty Python package file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. You can think of it like a label on a drawer: the drawer may contain useful tools, but this label simply makes the drawer recognizable to the rest of the program.

Because this file has no code inside it, it does not create objects, run setup steps, or change program behavior directly. Its value is structural. Without it, depending on the Python version and import style, other parts of the project might not be able to reliably import modules from `extensions/browser/ufo_ext_browser/bua`. That could break code that expects `bua` to be a normal package.

So this file matters not because of what it runs, but because it helps organize the project and keeps imports predictable.


### Coding and document packages
These markers expose coding and document-oriented extension packages, including importable script folders for document review and Office file workflows.

### `extensions/coding/ufo_ext_coding/__init__.py`

`other` · `import time`

In Python, a folder often needs an `__init__.py` file to be treated as a package, meaning a named group of code that can be imported elsewhere. This file is that marker for the `ufo_ext_coding` extension package. It is currently empty, so it does not run setup code, expose shortcut imports, or define any functions or classes.

Its value is structural rather than behavioral. Without it, depending on the Python version and import style, code that tries to import `ufo_ext_coding` or modules inside it might fail or behave differently. You can think of it like a label on a filing cabinet drawer: the label does not contain the documents, but it tells the system that the drawer is an organized place to look.


### `extensions/documents/ufo_ext_documents/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the label does not contain the tools, but it tells the system that the drawer exists and can be opened by name.

Here, the drawer is `ufo_ext_documents`, which appears to be an extension area for document-related functionality. Even though this file has no code, it still matters because imports elsewhere may rely on the package name being valid. Without it, depending on the Python version and packaging setup, other parts of the project might fail to import document extension modules cleanly.

There are no functions, classes, settings, or startup actions here. Its job is structural: it helps organize code and gives the package a clear boundary.


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/__init__.py`

`other` · `package import/discovery`

This is an empty Python `__init__.py` file. Its job is not to perform document review directly, but to tell Python that the surrounding `scripts` directory should be treated as an importable package. In everyday terms, it is like putting a label on a folder so the rest of the system knows the folder is part of the organized project structure, not just a loose collection of files. Without this file, some Python environments or tooling might not reliably find or import script modules from this directory, especially in older packaging styles. There are no functions, classes, settings, or side effects here. Its importance is structural: it supports package discovery and keeps imports predictable for the document-review skill’s script code.


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/__init__.py`

`other` · `import time`

This is an empty Python package marker. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools, but this label mainly helps Python find and organize them. Here, the drawer is the `scripts` folder inside the PowerPoint-related document extension. Without this file, some Python environments or import styles might not reliably recognize the folder as a package, which could make nearby script modules harder or impossible to import. Because the file is empty, it does not define settings, start any process, or change behavior when loaded beyond the package-recognition effect.


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/__init__.py`

`other` · `import time`

This file does not contain any executable code, but it still has a useful job. In Python projects, an `__init__.py` file tells Python that a folder should be treated as a package, meaning its contents can be imported as part of the project’s module structure. Think of it like a label on a drawer: the drawer may be empty at the front, but the label tells the rest of the system where to look for related tools inside it. Without this file, some Python environments or import styles might not recognize `scripts` as a package, which could make imports from this directory fail or behave differently. Because it is empty, it does not set up state, define helpers, or run startup code. Its purpose is structural: it supports organization and import compatibility for the Office XLSX document skill scripts.


### Knowledge and recall packages
These package markers make knowledge graph, memory, and research capabilities importable for higher-level user workflows.

### `extensions/knowledge_graph/ufo_ext_knowledge_graph/__init__.py`

`other` · `import time`

In Python, an `__init__.py` file is like a label on a folder saying, “this folder is part of the program and can be imported.” This particular file is empty, so it does not define settings, functions, or startup behavior. Its value is structural: it lets the knowledge graph extension live under a clear package name, `ufo_ext_knowledge_graph`, and allows other files to import pieces from that package in the usual Python way. Without this file, some Python environments or tooling might not treat the folder as a normal package, which could make imports less predictable. Think of it as the front door sign for the extension’s code folder: it does not do the work inside the building, but it helps the rest of the system find the building correctly.


### `extensions/memory/ufo_ext_memory/__init__.py`

`other` · `startup and cross-cutting`

This file does not contain working code. Its main job is to identify this folder as the home of the memory extension and to give a short summary of the extension’s responsibilities. In plain terms, the memory extension is about helping the system remember useful information over time. It can store durable facts, bring relevant memories back when a user submits a prompt, derive memory from page changes, and run a background-style job that builds or updates an index so memories can be found later. An index is like a library catalog: it does not replace the books, but it helps you find the right one quickly. Without this package file, Python tooling may not treat the folder as an importable package in older or stricter setups, and readers would lose this small signpost explaining what the extension is for.


### `extensions/research/ufo_ext_research/__init__.py`

`other` · `import time`

In Python, a folder becomes an importable package when it contains an `__init__.py` file. This file is that marker for the `ufo_ext_research` research extension. Think of it like a label on a folder: it tells Python, “this folder is a named part of the program.” Without it, depending on the Python setup and packaging rules, other parts of the system might not be able to reliably import modules from this directory. The file is empty, so it does not run setup code, define helper functions, or expose a simplified public interface. Its value is structural rather than behavioral: it helps the project organize research-related extension code under a clear package name.


### Sites and skill authoring
These entries expose site-oriented capabilities and the skill-authoring extension that supports creating and surfacing saved workspace skills.

### `extensions/sites/ufo_ext_sites/__init__.py`

`other` · `import/setup`

This is an empty Python package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means code elsewhere can refer to this folder by name, such as importing modules that live under `extensions/sites/ufo_ext_sites`.

Because the file has no code, it does not run setup steps, create objects, or change any settings. Its value is structural: it tells Python and project tools, “this directory is part of the program’s module tree.” A simple analogy is a label on a filing cabinet drawer. The label does not contain the documents, but it lets people and systems know the drawer has a name and can be found reliably.

Without this file, some Python environments or tooling might not recognize the directory as a package, which could make imports fail or behave inconsistently. So even though it looks empty, it helps keep the extension site code organized and importable.


### `extensions/skill_create/ufo_ext_skill_create/__init__.py`

`other` · `import time`

This file does not contain runnable logic. Its main job is to identify this folder as a Python package and to document, in one place, the purpose of the extension. The extension is about skill authoring: helping users create new skills, treating skills as a known kind of object in the system, and making sure skills saved in a workspace can be used at runtime.

In plain terms, a “skill” here is likely a reusable capability or instruction set that the system can call on later. The docstring says this extension covers both the creation workflow and the runtime side, where saved skills are merged into the registry for the current turn. A registry is like a catalog: it tells the system what skills are available right now.

Without this package marker, Python would not treat this directory as an importable package in the usual way. Without the short description, newcomers would have less immediate context for why this extension exists and what pieces they should expect to find nearby.


### Messaging and core extension packages
Slack and UFO package markers provide importable namespaces for messaging integration and the core UFO extension area.

### `extensions/slack/ufo_ext_slack/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the system can refer to code inside `extensions/slack/ufo_ext_slack` using normal Python import paths. Think of it like putting a label on a drawer: the drawer may hold many useful tools, but this label is what lets the rest of the workshop find it reliably. Because the file is empty, it does not set up Slack behavior, load settings, or run any startup work. Its value is structural: without it, some Python environments or tooling might not recognize this directory as a package, which could make imports from the Slack extension fail.


### `extensions/ufo/ufo_ext_ufo/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools in other files, but this label is what lets the rest of the program find the drawer by name. Because the file is empty, it does not run setup code, expose helper functions, or define shared values. Its main job is structural: without it, some Python environments or packaging tools might not recognize `extensions/ufo/ufo_ext_ufo` as a normal package, which could make imports fail or behave differently.


### Web and YC workflows
The final package markers expose general web-extension modules and YC-specific workflow support.

### `extensions/web/ufo_ext_web/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to this folder by name, such as importing modules under `ufo_ext_web`.

There is no code here because the package does not need any startup work, shared constants, or convenience imports at this level. Its job is more like a label on a drawer: it tells Python, and human readers, that the files inside belong together as the web extension part of the project.

Without this file, some Python environments or tooling might not recognize the directory as a normal package, which could make imports less reliable. So even though it looks empty, it helps keep the project structure clear and importable.


### `extensions/yc/ufo_ext_yc/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a directory with an `__init__.py` file is treated as a package, which means other parts of the project can import modules from `extensions/yc/ufo_ext_yc` using normal Python import paths. Think of it like a label on a folder: the label does not contain instructions, but it tells Python, “this folder belongs to the program and can be used as a module namespace.” Without this file, some Python setups or tooling might not recognize the directory as an importable package, especially in older or stricter environments. Because the file is empty, it does not run setup code, expose shortcuts, or change package behavior when imported.
