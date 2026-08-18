# Daily brief setup

The `sweep` extension creates one private daily brief for each seated member at or after 8:00 AM
in that member's timezone. The member reads the brief in the portal. The extension is active for
all seated members when it is installed.

## Install

The `assistant` and `assistant_hosted` packs include `sweep`, `memory`, `objectives`, `todos`,
`research`, and `perplexity`. Select one pack in `ufo.toml`:

```toml
[pack]
name = "assistant"

[research]
search_provider = "perplexity"
```

Set the model keys before `serve` starts:

```bash
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
```

Set the Perplexity credential through the hidden credential prompt:

```bash
ufoctl credential set perplexity_api_key
```

For a lockfile deploy without an assistant pack, install the complete set, then apply the schema:

```bash
ufoctl ext install sweep
ufoctl ext install memory
ufoctl ext install objectives
ufoctl ext install todos
ufoctl ext install scheduled_tasks
ufoctl ext install research
ufoctl ext install perplexity
ufoctl ext install index_default
ufoctl ext install embed_openai
ufoctl migrate
```

Restart `ufoctl serve`. The extension registers the `daily-brief` agent and its Skill. New
workspaces receive the agent during onboarding. An existing workspace receives it before its first
extension job or member turn after the restart.

## Agent settings

Do not create the agent or paste a prompt. The extension declares these settings:

| Setting | Value |
|---|---|
| Name | `daily-brief` |
| Prompt | `Load the daily-brief skill for scheduled briefs and member follow-up. Follow it exactly.` |
| Final model | `claude-sonnet-5` |
| Reasoning | `high` |
| Scout model | `gpt-5.6-luna` |
| Sandbox | `small`, no direct internet |
| Visibility | Workspace |
| Tools | `load_skill`, `sweep_newspaper`, `update_todo_list`, `memory_update`, `set_homepage` |

The member portal reads the prompt, the models, the sandbox, and the visibility. An administrator
changes those in chat with the main agent, and the extension never replaces the edits. The tool list
is not one of them: it ships with the extension, no object verb writes it, and no portal panel reads
it. This table is where an operator reads it.

If the workspace already has a different agent named `daily-brief`, the registered agent uses
`daily-brief-sweep`. Its status shows `provisioned_by: sweep`.

The scheduled turn loads the Skill and calls `sweep_newspaper`. It returns task and memory drafts.
The Sweep hook refuses those mutation tools on the edition turn. They become usable when the
member approves a draft in a later conversation turn.

## Homepage

`set_homepage` lets the agent bind the portal homepage a member opens on its Home tab. Keep the
`sites` extension active: the allowlist is intersected with the live registry, so the name
contributes nothing without it.

- A site a member deployed stays that member's to bind. That member asks the agent for it in a
  conversation with the agent, and the agent binds it. Every other member is refused, because a
  homepage answers the agent's audience instead of the site's own visibility.
- A scheduled edition turn binds nothing. The Sweep hook refuses `set_homepage` there, as it
  refuses the mutation tools.
- The agent holds no file or deploy tool. A member builds and deploys the page, then asks the agent
  to bind it.

The portal homepage seed needs both `deploy_website` and `set_homepage`. This provision holds only
`set_homepage`, so the seed job skips it instead of admitting a build turn it cannot finish.

The tool list reaches a workspace that does not hold the agent yet: a new workspace at onboarding,
an older workspace at its first extension job or member turn after the restart. A workspace that
already holds the shipped row keeps the tool list that row was created with. A shipped row is
written once and never again (RFC 0030), so a later version of the extension reaches new workspaces
only, and no administrator path widens the list on a standing row.

## Timezone and delivery

Slack, terminal, and web chat record the latest valid IANA timezone for the speaking member. A
member with no recorded timezone uses UTC. The hourly job creates no more than three attempts and
one completed brief for a member's local date.

Check these conditions if a brief does not arrive:

1. The member has a seat.
2. The member has sent a message from a surface that supplies their timezone, or UTC is acceptable.
3. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and `perplexity_api_key` are valid.
4. The `sweep`, `memory`, `objectives`, `todos`, and search-provider extensions are active.
5. An agent with `provisioned_by: sweep` exists with the settings above.

Removing `sweep` stops new daily runs. The ordinary agent and its private conversation history
remain.
