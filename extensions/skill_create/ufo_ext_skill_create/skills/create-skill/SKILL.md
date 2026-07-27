---
name: create-skill
description: "Load when a member asks to create, edit, repair, or save a reusable custom skill for the current agent."
---
# Create Skill

A saved skill belongs to the current agent. Later turns of this agent see it in
`<available_skills>` and can load it; another agent in the same workspace does not. Each agent may
own a different skill under the same name. A saved skill never replaces a built-in skill.

## Author

1. Identify the behavior worth making repeatable and the member phrases that should load it. Check
   `<available_skills>` for overlap. Ask only when a missing choice would change the workflow.
2. Choose a lowercase slug. Write `/workspace/<name>/SKILL.md`; its first characters must be `---`.
3. Use exactly this frontmatter shape:

   ```markdown
   ---
   name: concise-brief
   description: "Load when a member asks for a concise project brief with risks and next steps."
   ---
   ```

   The name must equal the directory and object name. Make the description a routing trigger of at
   most 50 words, beginning `Load when`; name member intent, not the workflow.
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
6. Confirm that the skill was saved for this agent and that other agents do not receive it.

## Revise

Use `object_get` to recover the file digests and `load_skill` to mount the current content. Re-apply
the same name with `{from: <path>}` for changed files and `{sha256: <digest>}` for unchanged files.
Applying replaces this agent's copy only. Remove it with `object_delete` using `kind: skill`.

## Traps

- No blank line or title may precede the opening `---`.
- Quote YAML descriptions; `:` and similar characters otherwise change their meaning.
- Bundle only UTF-8 text.
- Do not reuse a built-in skill name.
