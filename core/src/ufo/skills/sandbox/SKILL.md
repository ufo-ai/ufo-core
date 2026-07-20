---
name: sandbox
description: Load the first time a task has you run, build, or inspect anything in /workspace. E.g. execute a provided script; survey files you did not create.
metadata:
  tools:
  - bash
  - read
  - write
  - edit
  - share_file
---
# Working in the sandbox

Every command and file operation runs in a disposable per-conversation container. The one writable
tree is `/workspace`; it is the durable truth — it survives across turns while the container itself
is cache that may be rebuilt between turns. Anything outside `/workspace` is off limits.

## Building up work

- Keep intermediate artifacts as files under `/workspace` with descriptive names, not in your head.
  A later turn (and a subagent sharing this workspace) reads them back.
- Use `bash` for anything a shell does — installing a package, running a script, inspecting output.
  Long pipelines belong in a saved script you run, not one giant command.
- Read a file before you `edit` it: an edit replaces one unique occurrence, so if the old string
  is not unique, read more context and widen it until it is.

## Handing a result back

A file only reaches the user through `share_file`, which returns a time-limited download link —
put that link in your reply. Writing a file into `/workspace` does not deliver it; nothing leaves
the sandbox until you share it. Save the finished artifact, then share the exact path.
