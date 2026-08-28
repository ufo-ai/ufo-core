---
name: create-skill
description: "Load when a member asks to create, edit, repair, or save a reusable custom skill for the workspace."
---
# Create Skill

A saved skill belongs to the workspace. Later turns list it in `<available_skills>` while the saved
set is small, otherwise in the turn's `<saved_skills>` block; the skill kind's `skill_search`
action finds any saved skill and `load_skill` loads it. One name is one skill of the workspace:
every agent whose `use_workspace_skills` setting holds loads the same set, and applying a name the
workspace already holds edits that skill instead of making a second copy. A saved skill never
replaces a built-in skill.

## Author

1. Identify the behavior worth making repeatable and the member phrases that should load it. Check
   `<available_skills>`, `<saved_skills>`, and the skill kind's `skill_search` action for overlap.
   Ask only when a missing
   choice would change the workflow.
2. Choose a lowercase slug. Write `/workspace/<name>/SKILL.md`; its first characters must be `---`.
3. Use exactly this frontmatter shape:

   ```markdown
   ---
   name: concise-brief
   description: "Load when a member asks for a concise project brief with risks and next steps."
   ---
   ```

   The name must equal the directory and object name. Make the description a routing trigger of at
   most 50 words, beginning `Load when`; name member intent, not the workflow. To narrow the skill
   to specific agents, add `metadata:` with `agents: [<agent name>, ...]` — only those agents'
   turns list and load it; omit it for every agent.
4. Keep the body to procedure, judgment, and traps the agent would otherwise miss. Put repeated
   deterministic logic in `scripts/`, heavy conditional material in `references/`, and reusable
   output material in `assets/`; say exactly when to read each file.
5. Apply the finished directory as one `skill` object:

   ```yaml
   kind: skill
   name: concise-brief
   spec:
     files:
       SKILL.md: {from: concise-brief/SKILL.md}
       references/criteria.md: {from: concise-brief/references/criteria.md}
   ```

   `{from: <path>}` stores the file content, so the skill outlives the sandbox. Inline short text
   directly. Fix validation errors and re-apply.
6. Confirm that the skill was saved for the workspace and that every agent whose
   `use_workspace_skills` setting holds receives it.

## Revise

Use `object_get` to recover the file digests and the skill's `generation`, and `load_skill` to
mount the current content. Re-apply the same name with `{from: <path>}` for changed files,
`{sha256: <digest>}` for unchanged files, and the `generation` the get returned as a top-level
manifest key beside `kind`, `name`, and `spec`. Applying replaces the workspace's copy. Remove it
with `object_delete` using `kind: skill`.

## Traps

- No blank line or title may precede the opening `---`.
- Quote YAML descriptions; `:` and similar characters otherwise change their meaning.
- Bundle only UTF-8 text.
- Do not reuse a built-in skill name.
- A save refused for a stale generation means another writer saved first: `object_get` the skill
  again and re-apply from the current state — never retry the same manifest.
