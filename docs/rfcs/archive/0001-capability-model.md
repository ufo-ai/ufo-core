---
rfc: 0001
title: "Capability model (proposal): core capabilities, gated on backends"
status: proposed
date: 2026-07-06
---

# Capability model (proposal): core capabilities, gated on backends

Status: **proposal, not adopted.** This revises the core/extension boundary in `spec.md` (§Principles,
§Extension system). Nothing here is built yet; it exists so the direction can be decided from a
concrete shape.

## The problem it fixes

Today a *capability* is spread across an extension: the tool def, its args/description, its prompt
section, its skill, and its subagent membership all live in the extension, while the pluggable part
(which search API, which browser transport) lives in a *separate* backend seam. Two costs fall out:

- **Inter-extension dependencies.** A subagent profile owned by one extension can only use another
  extension's tool by naming it — which is why `subagent_tool_grants` exists. A capability's reach
  is wired across extension boundaries.
- **Split definition.** `search` is already three pieces: the `exa` backend (`search_providers`),
  the `research` tools, and the grants that attach those tools to profiles. The tool's def, prompt,
  and source-fidelity all live away from where the capability reads.

The pluggable part was always the **backend** (which search API, which CDP transport, which index).
The tool, its prompt, and its skill are the stable *what* — and the source research docs treat them
as native. So: keep the *what* in core; make the *how* the extension; gate the *what* on the *how*.

## The model

A capability lives in **core** — its tool defs, args, descriptions, prompt section, skill(s), and
subagent membership. Its **presence** is gated on a named capability being *satisfied*. A capability
is satisfied one of two ways:

- **backend-satisfied** — a provider extension registered the backend seam it needs (search →
  `search_providers`, browser → `cdp_providers`, memory → `indexes`+`embeds`, connectors →
  `auth_proxies`/broker). These seams **already exist**; this proposal adds no backend machinery.
- **toggle-satisfied** — a pack or config declares it on, for capabilities with no external backend
  (skill authoring, todos). This is the razor's lever: "an agent that won't author skills" simply
  runs a pack that doesn't enable `skill_authoring`.

Extensions keep two jobs: **provide backends** (the honest pluggable part), and **add their own
net-new tools/skills** for the long tail (a niche integration core shouldn't carry). The `tools` /
`skills` / `subagents` / `subagent_tool_grants` Manifest points survive **only for that long tail** —
the common capabilities no longer ride them.

## Mechanism (one uniform rule)

```
# a tool/skill/prompt-section declares the capabilities it needs
ToolDef(..., requires=("search",))          # requires: tuple[str, ...] = ()

# satisfied set, computed once per turn from the resolved runtime
satisfied  = {backend-derived caps}          # search_provider present → "search", cdp → "browser", …
satisfied |= active_pack.capabilities        # toggle caps a pack/config turns on

# the turn's tool set (main and subagents draw from it)
tools = tuple(t for t in CORE_TOOLS if set(t.requires) <= satisfied)
```

`prompt_sections` render iff their capability is satisfied; core skills are in the registry iff
satisfied; a subagent profile lists a core tool name and simply doesn't get it when the capability is
off — the same graceful degradation the grants intersection gives today. **One rule** covers tools,
prompts, skills, and subagent membership. A backend auto-satisfies its capability; a pack/config
satisfies toggle capabilities explicitly.

## Capability catalog

| Capability | Core tools | Gate | Satisfied by |
|---|---|---|---|
| `search` | `search_web`, `search_vertical`, `fetch_url`¹ | backend | `search_providers` (exa/pplx) |
| `browser` | `navigate`, `computer`, `read_page`, `get_page_text`, `find`, `form_input`, `tabs_*`, `upload_file`, `wait_for_download`, `browser_task`, `wide_browse` | backend | `cdp_providers` (sandbox-cdp/browserbase) |
| `memory` | `memory_search`, `memory_update` | backend | `indexes` + `embeds` |
| `connectors` | `list_external_tools`, `describe_external_tools`, `call_external_tool` | backend | `auth_proxies` / connector broker |
| `connect_account` | `connect_account` | backend | any registered `OAuthProvider` present |
| `mcp` | (dynamic mcp tools) | backend | configured MCP servers |
| `repl` | `js_repl`, `xlsx_repl` | always | sandbox (node/python always present) |
| `sites` | `start_server`; `deploy_website`/`publish_website`² | mixed | sandbox / a deploy backend |
| `scheduled_tasks` | `schedule_task`, `cancel_task`, `list_tasks` | always | core DBOS scheduler |
| `todos` | `update_todo_list`, `update_todo_status` | toggle | pack/config |
| `skill_authoring` | `save_custom_skill` | toggle | pack/config |
| `sessions` | `load_sessions` | toggle | pack/config (reads core transcripts; no backend) |

¹ `fetch_url` additionally gates on the backend's `supports_fetch`. ² `start_server` needs only the
sandbox; `deploy_website`/`publish_website` need a deploy backend.

## What changes in the codebase

- **Tool defs + prompt sections + skills move from the capability extensions into core**, tagged
  with `requires`. The extensions shrink to their **backend** registration (exa keeps
  `search_providers`; the browser extension keeps its BUA engine as a `cdp_providers` consumer;
  memory keeps `indexes`/`embeds` consumption; connectors keeps the broker/`auth_proxies`).
- **`skill_create` reverses.** `save_custom_skill` + `UserSkillStore` + the `user_skill` table return
  to core as a `skill_authoring`-gated capability; the create-skill teaching skill becomes a core
  skill gated the same way. (The just-built extension is discarded.)
- **`load_sessions` / `connect_account` stay in core**, gated (`sessions` toggle; `connect_account`
  on a provider being present) — the razor is expressed by the gate, not by relocation. The
  half-built `sessions`/`connect_account` extensions are discarded.
- **`subagent_tool_grants` is retained but demoted** — used only when a long-tail *extension* tool
  needs to reach a profile. Core capabilities no longer need it; core profiles list core tool names.
- **`ToolDef.subagent_default`** stays (broadcast half), unaffected.
- **Packs become the capability lever**: a pack names its backend extensions (as now) and its
  toggle capabilities. `assistant` / `assistant_hosted` gain a `capabilities` field for the
  toggle-gated set.

## Doctrine delta (spec.md)

- §Principles: "if a capability can be an extension, it is not core" becomes **"a capability lives
  in core; its *backend* is the extension, and its presence is gated on that backend (or a pack
  toggle). A genuinely novel capability core shouldn't carry may still ship as a full extension."**
- §Extension system: the `tools`/`skills`/`subagents`/`prompt_sections` Manifest points are
  re-scoped to "long-tail, net-new capabilities"; the backend seams (`indexes`, `embeds`,
  `carriers`, `cdp_providers`, `search_providers`, `auth_proxies`, `models`, `hubs`) become the
  primary extension surface. Add `ToolDef.requires` and the capability-gating rule to §Agent loop.

## Migration (incremental, one capability at a time)

`search` is the cheapest first proof — its tools **already** call `ctx.search_provider` and are
`None`-guarded, so relocation is mechanical: move the three tool defs + the `<external_tools>`/search
prompt section into core, tag `requires=("search",)`, satisfy `search` when a `search_provider`
resolves, drop the tools from the `research` extension (it keeps its subagent profiles + prompt, or
those move too). Feel the ergonomics, then carry the pattern to `browser`, `memory`, `connectors`,
and finally reverse `skill_create` / `load_sessions` / `connect_account`. Each step is independently
verifiable and leaves main green.

## Open decisions

1. **Toggle mechanism** — a pack `capabilities: tuple[str, ...]` field, a `[capabilities]` config
   list, or both? (Recommend: pack field, since a pack is already the product-config unit.)
2. **Default for toggle capabilities** — `skill_authoring` default-off (opt-in, matches the razor);
   `todos` default-on? Or all toggle caps explicit in every pack?
3. **Long-tail path** — confirm extensions keep `tools`/`skills` for net-new capabilities (keeps the
   third-party ecosystem open, and `subagent_tool_grants` alive for that tail). Pure-core (delete the
   grants seam, backends-only extensions) is the alternative.
4. **Scope of the first cut** — adopt for `search`+`browser` only and reassess, or commit to the full
   catalog?
