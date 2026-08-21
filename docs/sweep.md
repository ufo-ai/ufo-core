# Daily Brief setup

The `sweep` extension supplies the `daily-brief` Skill, `configure_daily_brief`,
`sweep_newspaper`, and four bounded scouts. It creates no application, schedule, or conversation.

## Install

The `assistant` and `assistant_hosted` packs include Sweep and its dependencies. Select one pack in
`ufo.toml`:

```toml
[pack]
name = "assistant"

[research]
search_provider = "perplexity"
```

Set the model keys before `serve` starts:

```bash
export UFO_ANTHROPIC_API_KEY=...
export UFO_OPENAI_API_KEY=...
```

Set the search-provider credential through its hidden credential prompt:

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
ufoctl ext install sites
ufoctl ext install research
ufoctl ext install perplexity
ufoctl ext install index_default
ufoctl ext install embed_openai
ufoctl migrate
```

## Create the application

Ask the main agent:

> Create a private Daily Brief application that reviews my work each weekday morning, publishes
> each edition in Radar, and keeps its homepage current.

The `create-application` flow creates an ordinary member-owned application. Its prompt loads the
`daily-brief` Skill and names the Sweep tool, shared Markdown result, homepage update, and draft-only
approval boundary.

Open one conversation with the new application and ask it:

> Build and bind the Daily Brief homepage in this conversation. Schedule the brief for 8:00 AM on
> weekdays. Keep this conversation as the task report.

The member-facing turn registers this private application conversation, then uses the normal
website and scheduled-task tools. It binds the homepage and creates one recurring task that reports
to this conversation. Each scheduled run calls `sweep_newspaper` once, writes and shares a Markdown
edition, and deploys the updated homepage under the same site name. Radar renders the shared
Markdown file as the run result.

The scheduled turn cannot bind another homepage or apply task and memory drafts. The member can
approve a named draft in a later turn in the same conversation.

## Checks

If a run fails, check these conditions:

1. The member who created the task has a seat.
2. The application has one active scheduled task.
3. The task reports to the registered conversation that owns the bound site.
4. `UFO_ANTHROPIC_API_KEY`, `UFO_OPENAI_API_KEY`, and the search-provider credential are valid.
5. `sweep`, `memory`, `objectives`, `todos`, `scheduled_tasks`, `sites`, and the search provider are
   active.

Removing `sweep` leaves the member-owned application, task, homepage, and conversation in place.
The next run fails because `sweep_newspaper` is unavailable.
