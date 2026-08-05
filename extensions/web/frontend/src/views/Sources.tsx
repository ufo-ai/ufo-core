import { Button } from "@/components/ui/button";
import type { ListingSpec } from "@/kernel/listing";
import { day } from "@/lib/moments";

type Source = {
  name: string | null;
  backend: string;
  stream: string;
  account_id: string | null;
  base_url: string | null;
  owner_email: string | null;
  shared: boolean;
  consecutive_errors: number;
  next_sync_at: string;
};

type SourcesPayload = { sources: Source[] };

type SourceSpec = {
  provider: string;
  streams: string[];
  account_id: string | null;
  base_url: string | null;
  shared: boolean;
};

type SourceRow = {
  key: string;
  name: string | null;
  backend: string;
  streams: string;
  owner: string;
  access: string;
  errors: string;
  next_sync: string | null;
  shared: boolean;
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
      owner: first.owner_email || "—",
      access: first.shared ? "shared" : "private",
      errors: String(streams.reduce((total, entry) => total + entry.consecutive_errors, 0)),
      next_sync: day(streams.map((entry) => entry.next_sync_at).sort()[0]),
      shared: first.shared,
      apply: {
        provider: first.backend,
        streams: names,
        account_id: first.account_id,
        base_url: first.base_url,
        shared: first.shared,
      },
    };
  });

  return bound.concat(
    plain.map((entry, index) => ({
      key: "plain-" + index,
      name: null,
      backend: entry.backend,
      streams: "—",
      owner: entry.owner_email || "—",
      access: entry.shared ? "shared" : "private",
      errors: String(entry.consecutive_errors),
      next_sync: day(entry.next_sync_at),
      shared: entry.shared,
      apply: null,
    })),
  );
}

export const SOURCES: ListingSpec<SourcesPayload, SourceRow> = {
  read: "/workspace/sources",
  rows,
  rowKey: (row) => row.key,
  search: (row) => [row.name ?? "", row.backend, row.streams, row.owner].join(" "),
  chips: [
    { label: "Private", has: (row) => !row.shared },
    { label: "Shared", has: (row) => row.shared },
  ],
  columns: [
    { field: "backend", label: "source" },
    { field: "streams", label: "streams" },
    { field: "owner", label: "owner" },
    { field: "access", label: "access" },
    { field: "errors", label: "errors" },
    { field: "next_sync", label: "next sync" },
  ],
  empty:
    "No sources are registered. Register one in chat — the agent connects the account or " +
    "credential it needs as part of the request.",
  actions: (row, { act, busy }) =>
    row.apply === null || row.name === null ? null : (
      <div className="flex flex-wrap gap-xs">
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
        <Button
          variant="row"
          disabled={busy}
          onClick={() => act({ verb: "delete", kind: "source", name: row.name })}
        >
          Remove
        </Button>
      </div>
    ),
};
