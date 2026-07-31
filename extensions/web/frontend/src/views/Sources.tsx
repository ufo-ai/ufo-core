import { useState } from "react";

import type { Placement } from "@/kernel/pager";
import { Button } from "@/components/ui/button";
import { Table, Td, Th } from "@/components/ui/table";
import { Notice, Panel, PanelEmpty, usePanelRead } from "@/kernel/panel";
import { postIntent } from "@/lib/api";
import { useMainAgent } from "@/lib/mainAgent";

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

export function Sources({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const mainAgent = useMainAgent();
  const [notice, setNotice] = useState(place.notice ?? "");
  const [busy, setBusy] = useState(false);
  const state = usePanelRead<{ sources: Source[] }>("/workspace/sources");

  async function act(envelope: unknown) {
    if (!mainAgent) return;
    setBusy(true);
    const outcome = await postIntent(mainAgent.id, envelope);
    setBusy(false);
    if (outcome.applied) {
      onPlace({ notice: outcome.message });
      return;
    }
    setNotice(outcome.message);
  }

  return (
    <Panel state={state}>
      {(payload) => {
        const bindings = new Map<string, Source[]>();
        const plain: Source[] = [];
        for (const entry of payload.sources) {
          if (entry.name === null) {
            plain.push(entry);
            continue;
          }
          const streams = bindings.get(entry.name) ?? [];
          streams.push(entry);
          bindings.set(entry.name, streams);
        }

        if (!bindings.size && !plain.length) {
          return (
            <>
              <PanelEmpty>
                No sources are registered. Register one in chat — the agent connects the account or
                credential it needs as part of the request.
              </PanelEmpty>
              <Notice>{notice}</Notice>
            </>
          );
        }

        return (
          <>
            <Table>
              <thead>
                <tr>
                  {["source", "streams", "owner", "access", "errors", "next sync", ""].map(
                    (column, index) => (
                      <Th key={index}>{column}</Th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {[...bindings].map(([name, streams]) => {
                  const first = streams[0];
                  const names = streams.map((entry) => entry.stream).sort();
                  const spec = {
                    provider: first.backend,
                    streams: names,
                    account_id: first.account_id,
                    base_url: first.base_url,
                    shared: first.shared,
                  };
                  return (
                    <tr key={name}>
                      <Td>{first.backend}</Td>
                      <Td>{names.join(", ")}</Td>
                      <Td>{first.owner_email || "—"}</Td>
                      <Td>{first.shared ? "shared" : "private"}</Td>
                      <Td>
                        {String(
                          streams.reduce((total, entry) => total + entry.consecutive_errors, 0),
                        )}
                      </Td>
                      <Td>
                        {streams
                          .map((entry) => entry.next_sync_at)
                          .sort()[0]
                          .replace("T", " ")
                          .slice(0, 16)}
                      </Td>
                      <Td>
                        <div className="flex flex-wrap gap-xs">
                          <Button
                            variant="row"
                            disabled={busy}
                            onClick={() =>
                              act({
                                verb: "apply",
                                kind: "source",
                                name,
                                spec: { ...spec, resync: true },
                              })
                            }
                          >
                            Resync
                          </Button>
                          {first.shared ? null : (
                            <Button
                              variant="row"
                              disabled={busy}
                              onClick={() =>
                                act({
                                  verb: "apply",
                                  kind: "source",
                                  name,
                                  spec: { ...spec, shared: true },
                                })
                              }
                            >
                              Share
                            </Button>
                          )}
                          <Button
                            variant="row"
                            disabled={busy}
                            onClick={() => act({ verb: "delete", kind: "source", name })}
                          >
                            Remove
                          </Button>
                        </div>
                      </Td>
                    </tr>
                  );
                })}
                {plain.map((entry, index) => (
                  <tr key={"plain-" + index}>
                    <Td>{entry.backend}</Td>
                    <Td>—</Td>
                    <Td>{entry.owner_email || "—"}</Td>
                    <Td>{entry.shared ? "shared" : "private"}</Td>
                    <Td>{String(entry.consecutive_errors)}</Td>
                    <Td>{entry.next_sync_at.replace("T", " ").slice(0, 16)}</Td>
                    <Td />
                  </tr>
                ))}
              </tbody>
            </Table>
            <Notice>{notice}</Notice>
          </>
        );
      }}
    </Panel>
  );
}
