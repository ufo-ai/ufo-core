---
name: first-run
description: "Load when a member starts a workspace: this is our first app, what should I do first, help me get set up, what could you set up for us, connect Slack and show my team, or an opening message where nothing is connected."
---

# First run

Offer four proven applications, then start the one the member selects.

## Read the workspace

Read tools named in the opening message. Search memory for the company's domain, work, goals, and
tools. Call `object_list(kind="agent")` so you do not repeat an application. The member's words
override intake or memory. Check connector availability only when it changes what you can build.
If the company or its work is unclear, and one open-web fact would make a recipe specific, make one
small search.

Write memory only for new domain facts found by research. Connector availability, kickoff choices,
and created applications already have their own records; do not mirror them into memory.

## Offer four

Use one single-select `ask_user` question with these options, in this order:

| Label | Description |
|---|---|
| AI news review | Finds material AI changes, explains why they matter here, and cites each source. |
| Pull request babysitter | Posts current pull-request blockers to Slack. Requires GitHub and Slack. |
| Competitive intel digest | Reports verified product, pricing, positioning, hiring, and funding changes. |
| What we learned | Turns work in available tools into decisions, lessons, friction, and open questions. |

Do not use a multi-select. Do not offer more applications. Do not offer an application that already
exists. If the opening message selects one recipe, skip this question.

## Start it

Read only the selected recipe:

- AI news review: [references/recipes/ai-news-review.md](references/recipes/ai-news-review.md)
- Pull request babysitter: [references/recipes/pull-request-babysitter.md](references/recipes/pull-request-babysitter.md)
- Competitive intel digest: [references/recipes/competitive-intel.md](references/recipes/competitive-intel.md)
- What we learned: [references/recipes/what-we-learned.md](references/recipes/what-we-learned.md)

Load `create-application` and follow its interview and create rules. The recipe settles the job, so
go straight to the interview. Prefill the job. Add the recipe's one setup question when memory and
the opening message do not answer it. Ask no proposal question, open no guided-build board, and show
no multi-select.

## Move forward

After the application is created, state what it does and what access it still needs. Offer one next
act through one single-select `ask_user` question with two choices: do it, or finish. Prefer making
the new application usable by attaching or connecting its required account. If it needs no repair,
offer the next best application from the remaining context.

Stop first-run behavior when the member starts real work.
