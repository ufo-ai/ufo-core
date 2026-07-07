# Supabase — Persistent Database

Use Supabase instead of SQLite when the app needs multi-user data isolation or a database that persists independently of the sandbox.

## When to Use What

| | SQLite (default template) | Supabase |
|---|---|---|
| **Use when** | Single-user app, casual/prototype | Multi-user app, production data matters |
| **Data lives** | Inside the sandbox (`data.db`) — lives with the sandbox, not durable across new sandboxes | On Supabase's infrastructure, persists independently |
| **Setup** | Zero — template works out of the box | User connects Supabase account, agent sets up via MCP |

**Default to SQLite.** Only use Supabase when the user explicitly asks for it or says they want multi-user persistent data.

## Setup via MCP

All Supabase management is done via the Supabase MCP connector (source ID: `supabase`). Call `describe_external_tools` before calling any tool — the schemas are needed for correct arguments. If the connector is not connected, prompt the user to connect: `call_external_tool(tool_name='connect', source_id='supabase', arguments={})`.

### 1. Create or select a project

List the user's organizations and projects first:

```
call_external_tool(tool_name='list_organizations', source_id='supabase', arguments={})
call_external_tool(tool_name='list_projects', source_id='supabase', arguments={})
```

If they need a new project, follow the **strict cost-confirmation flow** — all three calls are required in order:

```
call_external_tool(tool_name='get_cost', source_id='supabase', arguments={type: "project", organization_id: "<org_id>"})
call_external_tool(tool_name='confirm_cost', source_id='supabase', arguments={type: "project", recurrence: "monthly", amount: 0})
call_external_tool(tool_name='create_project', source_id='supabase', arguments={name: "my-app", region: "us-east-1", organization_id: "<org_id>", confirm_cost_id: "<id from confirm_cost>"})
```

Skipping `get_cost` or `confirm_cost` will fail. If the project status isn't `ACTIVE_HEALTHY` immediately, poll with `get_project` until it is.

### 2. Get credentials and create `.env`

```
call_external_tool(tool_name='get_project_url', source_id='supabase', arguments={project_id: "<project_id>"})
call_external_tool(tool_name='get_publishable_keys', source_id='supabase', arguments={project_id: "<project_id>"})
```

Use the **legacy anon key** (JWT-format), not the `sb_publishable_` key — `supabase-js` expects the JWT-format key.

Write `SUPABASE_URL` and `SUPABASE_ANON_KEY` to `.env` in the project root (no `VITE_` prefix — these must stay server-side only).

### 3. Create schema

Use `apply_migration` for all DDL (CREATE TABLE, ALTER TABLE). Use `execute_sql` for DML (SELECT, INSERT, UPDATE, DELETE). The MCP enforces this distinction.

```
call_external_tool(tool_name='apply_migration', source_id='supabase', arguments={
  project_id: "<project_id>",
  name: "create_app_tables",
  query: "<SQL>"
})
```

See `references/supabase-template/migration.sql` for the pattern — tables use `user_id text` (populated by the Express backend from the session's `userId`).

Verify with:
```
call_external_tool(tool_name='list_tables', source_id='supabase', arguments={project_id: "<project_id>", schemas: ["public"], verbose: true})
```

### 4. Generate types (optional)

The MCP doesn't have a type generation tool. Manually define TypeScript types in `shared/schema.ts` based on your migration SQL, or use `list_tables` with `verbose: true` to inspect column types.

## Data Isolation

By default, use app-level filtering for data isolation:

- The Express backend filters by `user_id` in every query via `.eq('user_id', userId)`
- Derive the user identifier from the session cookie — the template ships `express-session`, so assign `req.session.userId ??= crypto.randomUUID()` on first request and use that as the id

If the user wants real authentication (login, signup, user accounts), they need to set up Supabase Auth in their Supabase project dashboard and implement the auth flow in the app. This is outside the scope of what the agent can automate — tell the user which dashboard steps are needed.

## Connecting from Express

The Supabase client lives server-side only. The frontend talks to Express API routes, which scope queries by the session's `userId`.

```typescript
// server/supabase.ts
import { createClient } from "@supabase/supabase-js";

const supabase = createClient(
  process.env.SUPABASE_URL!,
  process.env.SUPABASE_ANON_KEY!
);

export default supabase;
```

## Session Persistence

Sites are served at `http://localhost:<port>` inside the sandbox, so `localStorage`, `sessionStorage`, and cookies all work normally — use them for per-visitor state and sessions. For data that must outlive the sandbox, use Supabase (above).

## Modifying the Webapp Template

After copying the template, reference these files from `references/supabase-template/`:

- `migration.sql` — Example table schema with `user_id` for app-level filtering (adapt to your app, then delete)
- `.env.example` — Template for the two required env vars (`SUPABASE_URL`, `SUPABASE_ANON_KEY`)

Create `server/supabase.ts` to initialize the Supabase client (see "Connecting from Express" above). The SQLite storage layer (`server/storage.ts`) is unused with Supabase — replace CRUD routes to use the Supabase client instead.
