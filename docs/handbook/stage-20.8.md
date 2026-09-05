# Developer, debugging, skill-creation, and sample extension markers  `stage-20.8`

This stage is mostly behind-the-scenes support for developers and extension authors. It is not part of the main work loop. Instead, it helps optional tools be recognized, imported, and checked during development.

Two files, the debugger and REPL package markers, simply tell Python that their folders are importable packages. A package is a folder Python can load code from. The debugger package is for debugging tools, and the REPL package is for an interactive “type a command, see a result” developer console. These files do not run useful behavior by themselves; they act like labels on drawers so the rest of the system can find the tools inside.

The sample skill probe is a tiny test script. When run, it prints a fixed success message, proving that the sample skill exists and can be executed. The self-improvement package marker introduces an extension for reviewing past work and suggesting prompt improvements for human approval. The skill-create marker labels support for agent-created or temporary skills.

## Files in this stage

### Developer Extension Markers
Package markers for developer-facing debugger and REPL extensions that make their modules importable without adding runtime behavior.

### `extensions/debugger/ufo_ext_debugger/__init__.py`

`other` · `import/package discovery`

In Python, a folder usually needs an `__init__.py` file to be treated as a package: a named bundle of code that other parts of the project can import. This file is that marker for the `ufo_ext_debugger` extension package. Think of it like a label on a drawer: the drawer may contain useful tools, but the label itself does not do the work. Because this file is empty, it does not set up the debugger, define defaults, expose shortcuts, or run any code when imported. Its main value is structural. Without it, some Python environments or tooling might not recognize the folder as an importable package, which could make the debugger extension harder or impossible to load in the expected way.


### `extensions/repl/ufo_ext_repl/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That matters here because the surrounding `ufo_ext_repl` directory likely contains the actual code for a REPL extension, where “REPL” means an interactive read-evaluate-print loop: a prompt where a person can type commands and immediately see results. This file is like a label on a drawer. The label does not contain the tools, but it lets the rest of the system know the drawer exists and can be opened by name. Without this file, some Python environments or packaging tools might not recognize the directory as a package, which could make imports fail or make the extension harder to discover. Since the file is empty, it performs no startup work, stores no settings, and changes no data.


### Sample Skill Probe
A minimal executable health check confirms the sample skill is present and runnable.

### `extensions/sample/skills/sample_skill/probe.py`

`entrypoint` · `startup or installation check`

This file solves a very simple problem: it gives the system an easy way to ask, “Can this sample skill be reached and run?” It does not load data, call other project code, or make decisions. It simply prints the text `sample-skill-probe-ok`.

That fixed message acts like a doorbell. If another tool runs this script and hears the expected “ring,” it knows the sample skill’s probe file exists and the Python environment is able to execute it. If the message does not appear, or the script cannot run, that points to a setup or packaging problem.

Because this is a probe, its value is in being boring and predictable. There are no inputs, no configuration options, and no hidden side effects beyond writing one line to standard output, which is the normal place command-line programs print text.


### Skill Improvement Markers
Package markers describe offline self-improvement support and agent-created or turn-scoped skill creation themes.

### `extensions/self_improvement/ufo_ext_self_improvement/__init__.py`

`other` · `startup/import`

This file does not contain running code. Its main job is to identify this folder as the home of the self-improvement extension and to give a short human-readable summary of that extension’s purpose.

The docstring explains the idea: the extension runs an offline replay evaluation loop. In plain terms, it looks back over saved records of earlier work, called trajectories, rather than acting live in the moment. It uses those records to judge how prompts could be improved. A prompt is the instruction text that guides an AI system’s behavior.

A key point is that the extension does not directly change prompts on its own. Instead, it opens governed prompt changes, meaning proposed changes that go through a controlled review process. A member must approve them before they become real. This matters because prompt changes can alter how the system behaves, so they should not happen silently or automatically.

Without this file, Python might not treat the directory as an importable package in some setups, and newcomers would lose this small but useful signpost explaining what the extension is for.


### `extensions/skill_create/ufo_ext_skill_create/__init__.py`

`other` · `import time`

This is a very small package entry file. It does not run any code, define any functions, or create any objects by itself. Its main job is to let Python treat the surrounding folder as an importable package, like putting a label on a drawer so the rest of the project can find what is inside. The docstring explains the package’s area of responsibility: it groups code related to authored skills, meaning skills owned or created by an agent, and per-turn runtime skills, meaning skills that exist or are used during one step of execution. Without this file, older Python tooling or readers browsing the codebase might have a harder time recognizing this folder as a coherent package. The important point is that the actual behavior lives in other files in this package; this file is the doorway and short description.
