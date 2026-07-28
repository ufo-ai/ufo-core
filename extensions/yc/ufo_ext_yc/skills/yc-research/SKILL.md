---
name: yc-research
description: Research ycombinator (YC) guidance, Bookface knowledge, companies, founders, investors, deals, events, launches, and community posts. Use for YC or Bookface questions, fundraising research, YC-network discovery, founder discounts, batch logistics, or any answer that should be grounded in current YC sources.
---

# YC Research

Use indexed YC manuals and Startup Library pages for broad synthesis. Use `yc_read` when the answer depends on current Bookface state, access-scoped results, or a YC skill.

## Workflow

1. Search indexed memory first. Prefer official YC manuals for current policy, logistics, legal, finance, and program guidance. Treat Startup Library talks and essays as educational or historical.
2. Use `yc_read` with `search` for current Bookface records. Search companies and founders separately; search investors before asking which companies they funded.
3. Use `yc_index` only when the member asks to retain a bounded search in shared memory. Choose one of companies, founders, investors, deals, meetups, forum, launches, alumni groups, or jobs; state the query and bound. Repeating the same entity, query, and bound reuses its source.
4. Keep candidates, chats, routes, follows, and staff as live reads; never add them to shared memory.
5. Use Bookface `deals` only for founder discounts and benefits. Call sales opportunities a sales pipeline and investment opportunities a fundraising pipeline.
6. For community knowledge, search forum posts with several keyword variants. Distinguish Launch Bookface posts from public Launch YC records.
7. Use `skills_list`, then `skills_read`, when YC publishes task-specific guidance. Never guess a skill name.
8. Use `ask` for an informational question that spans several YC sources. Never request a mutation through `yc_read`.
9. Cite the returned Bookface or YC Library link beside every material claim. State when results are stale, access-limited, conflicting, or absent.

## Search discipline

- Search result excerpts before loading more context.
- Read the strongest three to six relevant records for comparisons.
- Prefer relevance over popularity; use engagement only as a secondary signal.
- Keep private Bookface content inside the workspace response. Do not copy it into public artifacts unless the user explicitly asks and has authority.
- Never expose `yc_cli_credentials`, `~/.yc`, access tokens, or refresh tokens.

## Credential setup

The workspace shares one read-only YC identity. If the credential is unset, call `yc_auth` with `start` and give a workspace admin its browser URL and code. After they say they approved it, call `complete`; if it remains pending, repeat the same URL and code. Never ask for credential JSON or a token.
