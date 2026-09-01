# Document Writing Subagent Definition  `stage-10.3.8`

This stage is a small behind-the-scenes setup point for the document extension. It does not do the main document processing itself. Instead, it makes sure the system has a clearly defined helper available when it needs prose written or edited.

The package marker file, `__init__.py`, is like putting a label on a folder so Python can recognize it as part of the program. Because of that label, other parts of the system can import document-extension code from this folder.

The main working piece is `subagent.py`. It defines a dedicated writing subagent, which is a smaller assistant started by the larger system for one focused job. In this case, the job is drafting and improving text. The file spells out the subagent’s name, which model it should use, what instructions it follows, what tools it may use, and what its inputs and outputs should look like. Keeping this definition separate helps the system call on a prose-writing helper reliably without mixing it up with document review or command-line processing tools.

## Files in this stage

### Document Writing Subagent Package
Package setup and configuration for the dedicated prose-writing subagent.

### `extensions/documents/ufo_ext_documents/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a directory often needs an `__init__.py` file to be treated as an importable package, like putting a label on a folder so the rest of the program knows it can look inside. Here, the folder appears to belong to a documents extension, and this file makes that extension’s Python modules available under the `ufo_ext_documents` package name. Because the file is empty, it does not run setup code, expose shortcuts, or change any state when imported. If it were missing in environments that require package marker files, imports from this directory could fail or behave differently.


### `extensions/documents/ufo_ext_documents/subagent.py`

`config` · `subagent setup`

This file is a profile card for a specialized writing helper. Instead of letting a general assistant improvise how to work on prose, the project gives it a clear job, a fixed prompt, a chosen model, and a small safe toolbox. The result is like assigning a writer a desk with only the supplies they need: draft files, editing tools, search inside the workspace, and the ability to load the writing workflow skill.

The file names the profile "writing" and pins it to the model "gpt-5.6-terra", so this child assistant uses a deliberate writing-focused model choice rather than whatever model the parent assistant is using. It reads its instructions from a nearby Markdown prompt file, `prompts/subagent_writing.md`.

Two small data models describe the conversation contract. `WritingTask` is what the parent sends in: a plain objective plus skills to preload. By default, it preloads `writing-drafts`, the workflow skill this extension owns. `WritingResult` is what comes back: a freeform result string.

The allowed tools are intentionally narrow: read, write, edit, glob, grep, and load_skill. There is no shell, programming REPL, web access, or file-sharing tool. That matters because this subagent is meant to work on prose in the shared workspace, not execute scripts, fetch outside information, or act like a second coding assistant.
