# Environment documents

An environment document is the set of overrides one turn tree pins by digest: the model each target
runs on, the prompt it reads, the tool offer it sees, the skills it can load, and files seeded into
its sandbox. `ufo --environment` uploads the document once and pins it on every turn of the tree,
including each spawned child, so a prompt, tool-description, or skill experiment is one document per
arm against a shared stack — no host process, no per-arm deploy.

Overrides narrow the platform's grants; they cannot widen them. A rewrite touches only text the
model reads, a removal only shrinks the offer, and every surviving tool keeps the handler,
capability context, and authorization the platform bound to it. A `run` tool is the one addition a
document may make, and its implementation is a command in the turn's own sandbox.

## Pinning one

`ufo --environment <path|sha256:digest>` resolves before the first turn:

| Argument | What the client does |
| --- | --- |
| `sha256:…` | Pins it as given. Uploads nothing. |
| A path | Uploads each `files` value that is a local path to `POST /surface/ufo/environment/file` and rewrites the entry to the returned digest, then uploads the document to `POST /surface/ufo/environment/document` and pins the digest it answers. |

The digest rides every turn as the `x-ufo-environment` header. Documents are stored
content-addressed and canonicalized to sorted-key JSON before hashing, so one content is one digest
whichever serialization it was written in, and a recovered turn replays byte-identically. A digest
with no stored document fails the turn: an arm whose overrides did not apply must never measure as
the default.

The flag is independent of `--model` and `--remote`. An eval stack matrix pins one per `[[run]]`
block with `environment = "arm.yaml"`; the Terminal-Bench harness passes it into each task box.

## Format

JSON or YAML. Unknown keys are refused everywhere.

```yaml
main:                                   # the member-facing agent's turns
  model: z-ai/glm-5.3-flash
  prompt:
    replace:
      - old: "Answer in the member's language."
        new: "Answer in the member's language, in at most three sentences."

profiles:                               # turns spawned as that profile
  coding:
    prompt:
      text: |                           # a whole replacement instead of edits
        You are a coding subagent…
    tools:
      bash:
        description: Run one shell command in the task's checkout.

tools:                                  # wherever any turn's offer holds the name
  web_search:
    enabled: false
  count_todos:                          # a name no offer holds: this adds the tool
    description: Count TODO markers under a directory.
    input:
      path: {type: string, description: Directory to scan.}
    run: rg -c TODO "$INPUT_PATH" | wc -l

skills:
  coding:                               # in-place edits against the live SKILL.md
    replace:
      - old: "never explore a repository yourself."
        new: ""
  triage-notes: |                       # a name no tier holds: this adds the skill
    ---
    name: triage-notes
    description: Load when the member asks to triage a failing run.
    ---
    # Triage notes
    …

files:
  data/case.tar: ./case.tar             # authored: a local path; stored: its digest
```

### Top-level keys

| Key | Reaches |
| --- | --- |
| `main` | The member-facing agent's turns. |
| `profiles.<name>` | Turns spawned as that profile. |
| `tools.<name>` | Every turn, wherever that name is offered. A `run` entry joins every offer. |
| `skills.<name>` | Every turn. Replaces the deploy's skill of that name, edits it in place, or adds it. |
| `files.<path>` | Every turn's sandbox, written before the model runs. |

A target whose document holds no block for it runs unchanged. A `main` or `profiles.<name>` entry
for a tool replaces the top-level entry for that name whole; the two are not merged field by field.

### A target block

| Field | Value |
| --- | --- |
| `model` | The model id this target runs on. It outranks the tree-wide `x-ufo-model` pin for this target alone and reaches even an own-account profile, which bills the workspace. |
| `prompt` | `text` for a whole replacement, or `replace` for a list of `{old, new}` edits — exactly one of the two. `text` is faithful where the assembled prompt is static (a profile's), `replace` where it is rendered per turn (the member agent's). |
| `tools` | Tool overrides for this target only, same shape as the top-level `tools`. |

### A tool override

| Field | Meaning |
| --- | --- |
| `description` | Replaces the tool's description. |
| `parameters.<field>` | Replaces one input field's description. The tool's schema is otherwise untouched. |
| `enabled: false` | Withholds the tool. Takes no other field. |
| `run` | Defines the tool as a command in the turn's own sandbox: `input` fields arrive as `INPUT_<NAME>` environment variables and stdout is the result. It replaces the named tool's implementation where the offer holds the name, and adds the tool where it does not. |
| `input.<name>` | One `run` tool's input field: `type` (`string`, `integer`, `number`, `boolean`), `description`, and `required` (default true). Requires `run`. |

### A skills entry

The whole replacement `SKILL.md` as a string, or `replace` with a list of `{old, new}` edits against
the existing one — frontmatter included, so a routing-description ablation is one edit. The edited
text must still parse as the skill it names. Replacing or adding a name drops it from the deploy's
bundled set, so the sandbox mounts the document's content instead of the image's copy; the skill's
other bundled files are kept. A name carrying `/` keeps the whole path as its registry name and
takes its own name from the last segment.

### A files entry

A workspace-relative destination mapped to a source. An authored document names a local path,
resolved relative to the document; a stored document holds only the `sha256:` digest the client
uploaded it as, so the same archive across arms and runs lands on one key. Live per-conversation
copies are `ufo cp`'s job, not a document's.

## What fails loud

| Condition | Result |
| --- | --- |
| A `replace` anchor matching zero times or more than once | The turn fails. Edits apply in order, so an arm stays anchored to the live text and fails when the text drifts under it. |
| `main` or `profiles.<name>` naming a tool that target's offer does not hold, without `run` | The turn fails. |
| `parameters` naming a field the tool does not take | The turn fails. |
| `replace` under `skills.<name>` for a skill the deploy does not hold | The turn fails. Adding a skill takes the full text instead. |
| A `skills` name a member authored | The turn fails. A member's skill is the member's. |
| A `run` entry with no description, for a name no offer holds | The turn fails. |
| A top-level `tools.<name>` for a name a turn does not offer | Skipped for that turn. |

A spawned turn whose profile prompt is replaced with `text` still has the finish contract appended,
so the child can end its turn.

There is no deployment gate: a member can already create an agent with an arbitrary prompt, so a
per-turn document grants nothing new, and the turn row's digest is the audit record.

## Limits

| Limit | Value |
| --- | --- |
| Document | 2,000,000 bytes |
| Each file | 64,000,000 bytes |

The schema is `core/src/ufo/host/environment.py`; application is
`TurnEnvironment.assemble` in `core/src/ufo/host/assemble.py`. The design record, and the recorded
ablation arms this vocabulary was derived from, is `docs/rfcs/0043-harness-runtime-boundary.md`.
