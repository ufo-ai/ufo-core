# Runtime, evaluation, and internal extension package markers  `stage-21.7`

This stage is shared behind-the-scenes support. It does not run the debugger, evaluation tools, monitors, or replay system by itself. Instead, it gives Python clear “front doors” into these extension folders. In Python, an __init__.py file marks a folder as a package, meaning other parts of the system can import code from it.

Each file here opens one extension area. The debugger package marker lets the debugging extension be found. The evaluation environment marker also documents its purpose: fake, predictable mailbox and calendar connectors used for tests or evaluations. The monitors marker opens the monitoring extension. The Redis hub marker opens runtime infrastructure that other code can import. The REPL marker opens the interactive command extension, where a user can type commands and see results. The self-improvement marker describes offline replay of past work, judging it, and suggesting prompt changes for human approval. The sweep marker opens tools for running broad sets of experiments or checks. Together, these files are like labeled doors in a workshop: they do not build anything themselves, but they make every tool reachable.

## Files in this stage

### Debugging and Evaluation Markers
Package initializers that expose debugger support and predictable evaluation connectors to the wider system.

### `extensions/debugger/ufo_ext_debugger/__init__.py`

`other` · `import time`

This is an empty Python package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like putting a label on a box: the label may not contain instructions, but it lets other parts of the program find and open the box correctly. Here, the box is the `ufo_ext_debugger` debugger extension package. Without this file, some Python setups or tools might not recognize the folder as a package, which could make imports fail or make packaging and discovery less reliable. Because the file is empty, it does not create objects, run setup code, or change program behavior directly. Its value is structural: it supports clean organization and dependable importing of the debugger extension’s real code elsewhere in the package.


### `extensions/eval_env/ufo_ext_eval_env/__init__.py`

`other` · `test and evaluation setup`

This package is for running the system in a controlled evaluation setting. Instead of talking to real email or calendar services, it provides fake connector providers for a mailbox and a calendar. That matters because tests and evaluations need repeatable results: the same inputs should lead to the same outputs every time. Real mailboxes and calendars change constantly, can be slow, and may contain private data. A fake version is like a practice stage set: it looks enough like the real thing for the rest of the system to interact with it, but nothing live or risky is touched. This particular file does not contain working code. Its role is to identify the folder as a Python package and document, in one sentence, what the package is meant to contain.


### Monitoring and Runtime Infrastructure
Package markers for monitor-related modules and Redis-backed runtime infrastructure.

### `extensions/monitors/ufo_ext_monitors/__init__.py`

`other` · `import/package discovery`

In Python, a folder often needs an `__init__.py` file to be treated as a package, which means other parts of the project can import code from it using normal Python import paths. This file is empty, so it does not run setup code, expose helper functions, or change how the monitors work. Its value is structural: it tells Python and project tools that `extensions/monitors/ufo_ext_monitors` is a named place in the codebase. Without it, some import styles or packaging tools might not recognize this folder correctly, especially in environments that expect traditional Python packages. Think of it like a blank label on a drawer: the drawer’s contents are elsewhere, but the label lets the rest of the system find the drawer.


### `extensions/redis_hub/ufo_ext_redis_hub/__init__.py`

`other` · `import time`

This file is intentionally empty, but it still has a practical job. In Python, an `__init__.py` file tells the interpreter that a folder should be treated as an importable package. That means other code can refer to this extension using package-style imports, such as importing modules from `ufo_ext_redis_hub`.

You can think of it like a label on a folder in a filing cabinet. The label does not contain the documents, but without it the filing system may not recognize the folder as something that can be opened in the expected way.

For this Redis Hub extension, the actual work is likely in neighboring modules, not here. This file simply makes the package boundary clear and gives the project a stable place where package-level setup could be added later if needed. Because it is empty, importing the package has no side effects: it does not connect to Redis, load settings, register handlers, or run any startup code.


### Interactive Runtime Access
Package marker for the REPL extension used to import interactive runtime tooling.

### `extensions/repl/ufo_ext_repl/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells Python that the folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools, but the label itself does not do the work. Its presence lets other code refer to this extension package using normal Python import paths. Without it, depending on the Python version and packaging setup, the REPL extension might not be discovered or imported in the expected way. There are no functions, classes, settings, or startup actions here; it exists to support package structure.


### Replay and Sweeping Workflows
Package initializers for offline self-improvement workflows and sweep-oriented extension support.

### `extensions/self_improvement/ufo_ext_self_improvement/__init__.py`

`other` · `package import`

This file does not contain working code. Its main job is to introduce the package and explain its purpose in one sentence. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package. Here, it also acts like a label on a box: it says that everything inside this extension belongs to a “self-improvement” feature.

The feature described here is an offline replay evaluation loop. In plain terms, that means the system can look back at recorded past activity, called trajectories, and replay or inspect them without affecting live work. It then uses those records to evaluate how well prompts are working. If it finds a possible improvement, it does not silently change the system by itself. Instead, it opens a governed prompt change, meaning a proposed change that must be reviewed and approved by a human member.

What would break without this file is mostly packaging and clarity: Python may not recognize this directory as a package in older or stricter setups, and newcomers would lose the top-level explanation of what this extension is for.


### `extensions/sweep/ufo_ext_sweep/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a directory can act as a named package when it contains an `__init__.py` file. That lets other parts of the project import code from `extensions/sweep/ufo_ext_sweep` using normal Python import paths. Think of it like putting a label on a folder so the rest of the system knows the folder belongs in the project’s module map. Because the file is empty, it does not run setup code, expose shortcuts, or change any settings. Its value is structural: without it, some Python tools or older import setups might not recognize this directory as a package, which could make the sweep extension harder or impossible to import reliably.
