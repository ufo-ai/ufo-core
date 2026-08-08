import { useEffect, useRef, useState, type FormEvent } from "react";

import { Button, ConfirmButton } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Checkbox, Field, Input, Label } from "@/components/ui/field";
import { Td } from "@/components/ui/table";
import {
  Notice,
  type NoticeState,
  OutcomeNotice,
  Panel,
  QUIET,
  Section,
  outcomeNotice,
  usePanelRead,
} from "@/kernel/panel";
import { DataTable } from "@/kernel/table";
import { day } from "@/lib/moments";
import { BASE, postIntent } from "@/lib/api";
import type { Agent } from "@/lib/types";

type Connection = {
  provider: string;
  account_id: string | null;
  owner_email: string | null;
  shared: boolean;
  connected_at: string;
  grant: string;
};

type ConnectionsPayload = { connections: Connection[] };

function matches(entry: Connection, query: string): boolean {
  const said = [entry.provider, entry.account_id ?? "", entry.owner_email ?? ""].join(" ");
  return said.toLowerCase().includes(query.toLowerCase());
}

export function Connections({ agent }: { agent: Agent }) {
  const [reloads, setReloads] = useState(0);
  const [handoff, setHandoff] = useState<NoticeState>(QUIET);
  const [consentUrl, setConsentUrl] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [refusal, setRefusal] = useState<NoticeState>(QUIET);
  const [provider, setProvider] = useState("");
  const [shared, setShared] = useState(false);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [watching, setWatching] = useState<string | null>(null);
  const state = usePanelRead<ConnectionsPayload>("/agents/" + agent.id + "/connections", reloads);
  const source = useRef<EventSource | null>(null);

  useEffect(() => {
    if (watching === null) return;
    const stream = new EventSource(BASE + "/turns/" + watching + "/stream");
    source.current = stream;
    const done = () => {
      stream.close();
      source.current = null;
    };
    stream.addEventListener("connect", (event) => {
      setConsentUrl(JSON.parse((event as MessageEvent).data).url);
      setHandoff(QUIET);
      done();
    });
    stream.addEventListener("connect_error", (event) => {
      setHandoff({
        text: JSON.parse((event as MessageEvent).data).message,
        refused: true,
      });
      done();
    });
    stream.addEventListener("terminal", done);
    stream.onerror = done;
    return () => {
      stream.close();
      source.current = null;
    };
  }, [watching]);

  async function connect(event: FormEvent) {
    event.preventDefault();
    const named = provider.trim();
    if (busy || !named) return;
    setBusy(true);
    setConsentUrl(null);
    const outcome = await postIntent(agent.id, {
      verb: "connect",
      kind: "connection",
      name: named,
      spec: { shared },
    });
    setBusy(false);
    if (!outcome.applied) {
      setRefusal(outcomeNotice(outcome));
      return;
    }
    close();
    setHandoff({ text: "Consent opens privately for you.", refused: false });
    setWatching(outcome.turn_id ?? null);
  }

  function close() {
    setAdding(false);
    setRefusal(QUIET);
    setProvider("");
    setShared(false);
  }

  async function act(envelope: unknown) {
    const outcome = await postIntent(agent.id, envelope);
    if (!outcome.applied) setConsentUrl(null);
    setHandoff(outcomeNotice(outcome));
    setReloads((count) => count + 1);
  }

  return (
    <Panel state={state}>
      {(payload) => (
        <>
          {consentUrl || handoff.text ? (
            <Notice tone={handoff.refused ? "attention" : "quiet"}>
              {consentUrl ? (
                <a href={consentUrl} target="_blank" rel="noopener">
                  Open the provider consent page
                </a>
              ) : (
                handoff.text
              )}
            </Notice>
          ) : null}
          <Section
            title="Connections"
            bar={
              <>
                <Input
                  type="search"
                  aria-label="Search"
                  placeholder="Search"
                  className="max-w-control-row"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
                <Button variant="send" onClick={() => setAdding(true)}>
                  Add connection
                </Button>
                <Button onClick={() => setReloads((count) => count + 1)}>Refresh</Button>
              </>
            }
          >
            <DataTable
              columns={["Provider", "Account", "Owner", "Access", "Connected", ""]}
              rows={payload.connections.filter((entry) => matches(entry, query))}
              rowKey={(entry) => entry.grant}
              empty="No accounts are connected to this agent."
              note={query ? "No connected account matches this search." : undefined}
            >
              {(entry) => (
                <>
                  <Td>{entry.provider}</Td>
                  <Td>{entry.account_id ?? "—"}</Td>
                  <Td>{entry.owner_email ?? "—"}</Td>
                  <Td>{entry.shared ? "Shared" : "Private"}</Td>
                  <Td>{day(entry.connected_at)}</Td>
                  <Td>
                    {entry.owner_email === null ? null : (
                      <div className="flex flex-wrap gap-xs">
                        <Button
                          variant="row"
                          onClick={() =>
                            act({
                              verb: "apply",
                              kind: "connector_grant",
                              name: entry.grant,
                              spec: {
                                provider: entry.provider,
                                account_id: entry.account_id,
                                shared: !entry.shared,
                              },
                            })
                          }
                        >
                          {entry.shared ? "Make private" : "Share with agent"}
                        </Button>
                        <ConfirmButton
                          verb="Revoke"
                          variant="row"
                          onClick={() =>
                            act({
                              verb: "delete",
                              kind: "connector_grant",
                              name: entry.grant,
                            })
                          }
                        />
                      </div>
                    )}
                  </Td>
                </>
              )}
            </DataTable>
          </Section>
          {adding ? (
            <Dialog open onOpenChange={(next) => (next ? undefined : close())}>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Add connection</DialogTitle>
                </DialogHeader>
                <OutcomeNotice state={refusal} />
                <form id="add-connection" onSubmit={connect} className="flex flex-col gap-xl">
                  <Field
                    label="Provider"
                    htmlFor="connect-provider"
                    description="Consent opens privately for you once the provider is named."
                  >
                    <Input
                      id="connect-provider"
                      required
                      aria-describedby="connect-provider-description"
                      placeholder="github"
                      value={provider}
                      onChange={(event) => setProvider(event.target.value)}
                    />
                  </Field>
                  <div className="flex items-center gap-sm">
                    <Checkbox
                      id="connect-shared"
                      checked={shared}
                      onChange={(event) => setShared(event.target.checked)}
                    />
                    <Label htmlFor="connect-shared">Share with agent</Label>
                  </div>
                </form>
                <DialogFooter>
                  <Button type="submit" form="add-connection" variant="send" busy={busy}>
                    Connect
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          ) : null}
        </>
      )}
    </Panel>
  );
}
