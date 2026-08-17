import { Button, ConfirmButton } from "@/components/ui/button";
import { ACTS } from "@/components/ui/table";
import type { ListingSpec } from "@/kernel/listing";
import { ownerLabel } from "@/lib/audience";
import { Moment } from "@/lib/moments";

export type Source = {
  name: string | null;
  backend: string;
  stream: string;
  account_id: string | null;
  base_url: string | null;
  backfill_days: number | "all" | null;
  owner_email: string | null;
  own: boolean;
  shared: boolean;
  consecutive_errors: number;
  next_sync_at: string;
};

export type SourcesPayload = { sources: Source[] };

function access(shared: boolean): string {
  return shared ? "Workspace" : "Only you";
}

// Mirrors the object's own spec: a row act submits `{...row.apply, <what it changes>}`, so every
// field the binding's identity is built from belongs here even when no column shows it. Omit one
// and it reaches the verb as its default, reading as an edit the member never made — which the
// verb then refuses, taking the act with it.
type SourceSpec = {
  provider: string;
  streams: string[];
  account_id: string | null;
  base_url: string | null;
  shared: boolean;
  backfill_days: number | "all" | null;
};

type SourceRow = {
  key: string;
  name: string | null;
  backend: string;
  streams: string;
  owner: string | null;
  access: string;
  errors: string;
  next_sync: string | null;
  shared: boolean;
  own: boolean;
  apply: SourceSpec | null;
};

function rows(payload: SourcesPayload): SourceRow[] {
  const bindings = new Map<string, Source[]>();
  const plain: Source[] = [];
  for (const entry of payload.sources) {
    if (entry.name === null) {
      plain.push(entry);
      continue;
    }
    bindings.set(entry.name, (bindings.get(entry.name) ?? []).concat(entry));
  }

  const bound: SourceRow[] = [...bindings].map(([name, streams]) => {
    const first = streams[0];
    const names = streams.map((entry) => entry.stream).sort();
    return {
      key: name,
      name,
      backend: first.backend,
      streams: names.join(", "),
      owner: first.owner_email,
      access: access(first.shared),
      errors: String(streams.reduce((total, entry) => total + entry.consecutive_errors, 0)),
      next_sync: streams.map((entry) => entry.next_sync_at).sort()[0],
      shared: first.shared,
      own: first.own,
      apply: {
        provider: first.backend,
        streams: names,
        account_id: first.account_id,
        base_url: first.base_url,
        shared: first.shared,
        backfill_days: first.backfill_days,
      },
    };
  });

  return bound.concat(
    plain.map((entry, index) => ({
      key: "plain-" + index,
      name: null,
      backend: entry.backend,
      streams: "—",
      owner: entry.owner_email,
      access: access(entry.shared),
      errors: String(entry.consecutive_errors),
      next_sync: entry.next_sync_at,
      shared: entry.shared,
      own: entry.own,
      apply: null,
    })),
  );
}

export const SOURCES: ListingSpec<SourcesPayload, SourceRow> = {
  read: "/workspace/sources",
  rows,
  rowKey: (row) => row.key,
  search: (row) => [row.name ?? "", row.backend, row.streams, row.owner ?? ""].join(" "),
  chips: [
    { label: "Only you", has: (row) => !row.shared },
    { label: "Workspace", has: (row) => row.shared },
  ],
  columns: [
    { field: "backend", label: "Source" },
    { field: "streams", label: "Streams" },
    {
      field: "owner",
      label: "Owner",
      render: (email, _row, { viewer }) => ownerLabel(email, viewer),
    },
    { field: "access", label: "Access" },
    { field: "errors", label: "Errors" },
    {
      field: "next_sync",
      label: "Next Sync",
      render: (at) => <Moment at={at} />,
    },
  ],
  empty:
    "Register a source in chat. The agent connects the account or credential it needs as part " +
    "of the request.",
  actions: (row, { act, busy }) =>
    row.apply === null || row.name === null || !row.own ? null : (
      <div className={ACTS}>
        <Button
          variant="row"
          disabled={busy}
          onClick={() =>
            act({
              verb: "apply",
              kind: "source",
              name: row.name,
              spec: { ...row.apply, resync: true },
            })
          }
        >
          Resync
        </Button>
        {row.shared ? null : (
          <Button
            variant="row"
            disabled={busy}
            onClick={() =>
              act({
                verb: "apply",
                kind: "source",
                name: row.name,
                spec: { ...row.apply, shared: true },
              })
            }
          >
            Share
          </Button>
        )}
        <ConfirmButton
          verb="Remove"
          variant="row"
          disabled={busy}
          onClick={() => act({ verb: "delete", kind: "source", name: row.name })}
        />
      </div>
    ),
};
