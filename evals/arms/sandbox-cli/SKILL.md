---
name: sandbox
description: Load when a task first requires running, building, or inspecting anything in /workspace, including executing a supplied script, surveying unfamiliar files, or using an installed sandbox command.
metadata:
  tools:
  - bash
  - read
  - write
  - edit
  - share_file
---
# Working in the sandbox

Every command and file operation runs in a disposable container that belongs to a conversation — a
subagent turn runs in the container of the turn that spawned it. The one writable tree is
`/workspace`; it is the durable truth — it survives across turns while the container itself is cache
that may be rebuilt between turns. Anything outside `/workspace` is off limits.

## Building up work

- Keep intermediate artifacts as files under `/workspace` with descriptive names, not in your head.
  A later turn (and a subagent sharing this workspace) reads them back.
- Use `bash` for anything a shell does — installing a package, running a script, inspecting output.
  Long pipelines belong in a saved script you run, not one giant command.
- Read a file before you `edit` it: an edit replaces one unique occurrence, so if the old string
  is not unique, read more context and widen it until it is.

## Calling tools from programs

A program or shell pipeline cannot call your model tools directly. Use these installed CLIs at that
boundary. When the request names this interface, invoke it; never fabricate its output or replace a
bridge call with the corresponding direct tool.

- One prompt-to-text Anthropic model call: `ufo llm [--model MODEL] [--max-tokens N] 'PROMPT'`.
  stdout is the response text. This is turn-time generation, not a website runtime API.
- Object or connector tool: first run `ufo tool TOOL --describe`. Then pipe one JSON object to the
  call, including every schema-required field: `printf '%s' '{"kind":"agent"}' | ufo tool object_list`. Every response is one JSON stdout envelope:
  `{"ok":true,"result":...}` or `{"ok":false,"error":"..."}`; failure exits nonzero. Only tools
  available to the current agent are callable.

## Handing a result back

A file reaches the user through `share_file`, which returns a time-limited download link — put that
link in your reply. Writing a file into `/workspace` does not deliver it; nothing leaves the sandbox
until it is shared. Save the finished artifact, then share the exact path only when the ask carries a
share trigger from the delivery register: the user asked for a file, a document, or a format, asked
for the artifact itself, or asked for proof, evidence, or a fuller explanation the artifact answers.
A verb alone is not a trigger — "send", "give me", and "write up" name the delivery, so answer inline
and leave the file in `/workspace` unshared. Without `share_file` in your tool set, the workspace is
the handoff: name the path in your result, and the parent shares it.
