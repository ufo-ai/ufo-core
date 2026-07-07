---
name: create-skill
description: "Create or modify a custom skill for this workspace. Load when the user wants to create a new skill, edit an existing skill's instructions or frontmatter, or capture a repeatable workflow as a reusable skill."
---
# Create Skill

This skill walks you through authoring an Agent Skill in the workspace and saving it so it persists
across turns. A saved skill is scoped to this workspace: later turns can load it with `load_skill`
and it appears in `list_skills`, but it never reaches another workspace and can never replace a
built-in skill.

## When to Use This Skill

Use this skill when the user asks you to:

- Create a new skill or capture a repeatable workflow as one
- Edit an existing custom skill's instructions or frontmatter
- Package a set of instructions and helper files into something reusable

## Agent Skill Format

A skill is a directory containing a `SKILL.md` file. It may bundle additional files alongside it:

```
my-skill/
├── SKILL.md            (required)
├── scripts/            (optional) scripts the skill runs in the sandbox
├── references/         (optional) documentation read into context as needed
└── assets/             (optional) templates or other files the skill uses
```

For a simple skill, only `SKILL.md` is needed. Add bundled files when the workflow involves
repeated scripting, large reference material, or reusable templates — things you would otherwise
recreate every time. Keep `SKILL.md` focused (aim under ~500 lines); move depth into `references/`
files and point to them from `SKILL.md`.

### SKILL.md Structure

`SKILL.md` is a YAML frontmatter block (delimited by `---`) followed by a markdown body.

```markdown
---
name: my-skill
description: "A clear description of what this skill does and when to use it."
---

# Skill Title

## When to Use This Skill

Describe the scenarios where this skill applies.

## Instructions

Step-by-step guidance for the agent to follow.

## Examples

Example inputs and expected outputs.
```

### Frontmatter Requirements

**Required fields:**

- `name`: must match the skill's directory name exactly. Use lowercase letters, numbers, and
  hyphens (for example `pdf-processing`, `code-review`). It must not be the name of a built-in
  skill — saving under a built-in name is refused.
- `description`: what the skill does and when to use it. Be specific and include the trigger phrases
  that should surface it, since this is what the agent reads to decide whether to load the skill.
  Descriptions often contain `:` or other characters YAML treats specially — **always wrap the
  description in double quotes** to be safe.

**Optional fields:**

- `metadata.depends`: a list of other skill names to load together with this one, when this skill
  builds on them.

## Instructions for Creating a New Skill

1. **Understand the requirement.** Ask the user what the skill should accomplish and when it should
   apply. If a detail would change the workflow, ask before writing.

2. **Check existing skills.** Call `list_skills` so you do not reuse a name that already exists and
   so you can see whether an existing skill already covers the need.

3. **Choose a name and write a clear description** following the requirements above.

4. **Create the skill directory and file** in the workspace with the `write` tool:
   - Directory: `/workspace/<skill-name>/`
   - File: `/workspace/<skill-name>/SKILL.md`

   **CRITICAL:** the very first characters of `SKILL.md` must be `---`. No title, description, or
   blank line before the opening frontmatter delimiter.

5. **Add any bundled files** (scripts, references, assets) under the skill directory. A bundled
   script runs in the sandbox and must not depend on anything outside it.

6. **Save the skill.** Once every file is in place, call `save_custom_skill` with `path` set to the
   skill directory (for example `/workspace/<skill-name>`). It validates the skill and persists it
   for this workspace. If validation fails, read the error, fix `SKILL.md`, and save again.

7. **Confirm to the user** that the skill was saved. It is available to `load_skill` and appears in
   `list_skills` on subsequent turns.

## Modifying an Existing Skill

To change a saved skill: recreate its directory in the workspace (author the updated `SKILL.md` and
any files under `/workspace/<skill-name>/`), then call `save_custom_skill` on that directory again
under the same name. Saving replaces the stored skill in place.

## Common Errors

**"SKILL.md must open with a YAML frontmatter block"** — the file does not start with `---` on line
one. The very first character must be the opening delimiter; no title or blank line before it.

**"invalid YAML in frontmatter"** — the `description` (or another value) contains a character YAML
treats specially, most often `:`. Wrap the value in double quotes. Quoting is always safe.

**"skill name ... must match its directory"** — the `name` in the frontmatter differs from the
directory name. Make them identical.

**"is already a core or pack skill and cannot be overridden"** — the name belongs to a built-in
skill. Choose a different name; a custom skill can never shadow a built-in one.
