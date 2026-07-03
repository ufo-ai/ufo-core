create table if not exists workspace (
    id uuid primary key,
    created_at timestamptz not null,
    updated_at timestamptz not null
);

create table if not exists member (
    id uuid primary key,
    workspace_id uuid not null references workspace (id),
    email text not null,
    created_at timestamptz not null,
    updated_at timestamptz not null,
    unique (workspace_id, email)
);

create table if not exists surface_identity (
    workspace_id uuid not null references workspace (id),
    member_id uuid not null references member (id),
    surface text not null check (surface in ('cli')),
    external_id text not null,
    created_at timestamptz not null,
    updated_at timestamptz not null,
    primary key (surface, external_id)
);

create table if not exists agent (
    id uuid primary key,
    workspace_id uuid not null references workspace (id),
    name text not null,
    prompt text not null,
    model text not null,
    created_at timestamptz not null,
    updated_at timestamptz not null,
    unique (workspace_id, name)
);

create table if not exists conversation (
    id uuid primary key,
    workspace_id uuid not null references workspace (id),
    surface text not null check (surface in ('cli')),
    queue_key text not null,
    member_id uuid not null references member (id),
    created_at timestamptz not null,
    updated_at timestamptz not null,
    unique (surface, queue_key)
);

create table if not exists turn (
    id uuid primary key,
    workspace_id uuid not null references workspace (id),
    conversation_id uuid not null references conversation (id),
    agent_id uuid not null references agent (id),
    seq integer not null check (seq >= 1),
    status text not null check (status in ('queued', 'running', 'done', 'failed', 'cancelled')),
    inbound text not null,
    terminal jsonb,
    created_at timestamptz not null,
    updated_at timestamptz not null,
    unique (conversation_id, seq),
    check ((status in ('queued', 'running')) = (terminal is null))
);

create table if not exists ledger (
    id uuid primary key,
    workspace_id uuid not null,
    turn_id uuid not null references turn (id),
    dimension text not null check (dimension in ('tokens')),
    amount numeric not null check (amount > 0),
    priced_usd numeric not null check (priced_usd >= 0),
    model text not null,
    created_at timestamptz not null,
    updated_at timestamptz not null
);

create index if not exists ledger_turn on ledger (turn_id);
