import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import { Button, ConfirmButton } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Checkbox, Field, Input, Label } from "@/components/ui/field";
import { Facts } from "@/components/ui/facts";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
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
import { ownerLabel, useViewer } from "@/lib/audience";
import { useAgents } from "@/lib/mainAgent";
import type { Agent } from "@/lib/types";

type Connection = {
  provider: string;
  account_id: string | null;
  account_label: string | null;
  owner_email: string | null;
  own: boolean;
  shared: boolean;
  connected_at: string;
  grant: string;
};

type ConnectionsPayload = { connections: Connection[] };
type PoolConnection = Connection & { agents: { id: string; name: string }[] };
type PoolPayload = { connections: PoolConnection[] };
type GithubCoverage = { api: boolean; git_push: boolean; sources: boolean };

function matches(entry: Connection | PoolConnection, query: string): boolean {
  const said = [
    entry.provider,
    entry.account_label ?? "",
    entry.account_id ?? "",
    entry.owner_email ?? "",
  ].join(" ");
  return said.toLowerCase().includes(query.toLowerCase());
}

/** Every connector the member holds on one agent, the agent chosen in the bar. The agent's own tab
 *  states the same records narrowed to what that agent can actually reach; both read the one
 *  grant list, so nothing here needs a second endpoint. */
export function WorkspaceConnectors() {
  const agents = useAgents();
  const [targetAgent, setTargetAgent] = useState("");
  const [query, setQuery] = useState("");
  const [reloads, setReloads] = useState(0);
  const [handoff, setHandoff] = useState<NoticeState>(QUIET);
  const viewer = useViewer();
  const state = usePanelRead<PoolPayload>("/connections", reloads);
  const coverage = usePanelRead<GithubCoverage>("/github/coverage", reloads);
  return (
    <Section
      title="Connectors"
      bar={
        <>
          <Input aria-label="Search" placeholder="Search" className="max-w-control-row" value={query} onChange={(event) => setQuery(event.target.value)} />
          <Select value={targetAgent} onValueChange={setTargetAgent}>
            <SelectTrigger aria-label="Agent"><SelectValue placeholder="Attach to agent" /></SelectTrigger>
            <SelectContent>{agents.map((agent) => <SelectItem key={agent.id} value={agent.id}>{agent.name}</SelectItem>)}</SelectContent>
          </Select>
        </>
      }
    >
      {handoff.text ? <Notice tone="quiet">{handoff.text}</Notice> : null}
      <Panel state={state}>
        {(payload) => (
          <DataTable
            columns={["Provider", "Account", "Owner", "Access", "Connected", "Agents", ""]}
            rows={payload.connections.filter((entry) => matches(entry, query))}
            rowKey={(entry) => entry.provider + "-" + entry.account_id}
            empty="No connector is connected yet."
            note={query ? "No connected account matches this search." : undefined}
          >
            {(entry) => (
              <>
                <Td>{entry.provider}</Td>
                <Td>{entry.account_label ?? entry.account_id ?? "—"}</Td>
                <Td>{entry.owner_email ? ownerLabel(entry.owner_email, viewer) : "—"}</Td>
                <Td>{entry.shared ? "Workspace" : "Only you"}</Td>
                <Td>{day(entry.connected_at)}</Td>
                <Td>{(entry.agents ?? []).map((agent) => agent.name).join(", ") || "—"}</Td>
                <Td>
                  {entry.owner_email ? (
                    <div className="flex flex-wrap gap-xs">
                      <Button
                        variant="row"
                        disabled={!targetAgent}
                        onClick={async () => {
                          const outcome = await postIntent(targetAgent, { verb: "attach", kind: "connector_grant", name: entry.grant, spec: { provider: entry.provider, account_id: entry.account_id, shared: entry.shared } });
                          setHandoff(outcomeNotice(outcome));
                          setReloads((count) => count + 1);
                        }}
                      >Attach to agent</Button>
                      {(entry.agents ?? [])[0] ? (
                        <Button
                          variant="row"
                          onClick={async () => {
                            const outcome = await postIntent(entry.agents[0].id, {
                              verb: "apply",
                              kind: "connector_grant",
                              name: entry.grant,
                              spec: { provider: entry.provider, account_id: entry.account_id, shared: !entry.shared },
                            });
                            setHandoff(outcomeNotice(outcome));
                            setReloads((count) => count + 1);
                          }}
                        >
                          {entry.shared ? "Unshare" : "Share"}
                        </Button>
                      ) : null}
                      {(entry.agents ?? [])[0] ? (
                        <ConfirmButton
                          verb="Revoke"
                          variant="row"
                          onClick={async () => {
                            let last = QUIET;
                            for (const attached of entry.agents) {
                              const outcome = await postIntent(attached.id, { verb: "detach", kind: "connector_grant", name: entry.grant });
                              last = outcomeNotice(outcome);
                              if (last.refused) break;
                            }
                            setHandoff(last);
                            setReloads((count) => count + 1);
                          }}
                        />
                      ) : null}
                    </div>
                  ) : null}
                </Td>
              </>
            )}
          </DataTable>
        )}
      </Panel>
      {coverage.phase === "ready" ? (
        <Facts
          rows={[
            { label: "API", value: coverage.payload.api ? "Connected" : "Not connected" },
            { label: "Git push", value: coverage.payload.git_push ? "Connected" : "Not connected" },
            { label: "Sources", value: coverage.payload.sources ? "Connected" : "Not connected" },
          ]}
        />
      ) : null}
    </Section>
  );
}

/** What this agent can reach: a grant the member kept private is theirs, not the agent's, so the
 *  agent's own tab does not list it. */
export function AgentConnectors({ agent }: { agent: Agent }) {
  return <ConnectorList agent={agent} picker={null} sharedOnly={false} />;
}

function ConnectorList({
  agent,
  picker,
  sharedOnly,
}: {
  agent: Agent;
  picker: ReactNode;
  sharedOnly: boolean;
}) {
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
  const viewer = useViewer();
  const state = usePanelRead<ConnectionsPayload>("/agents/" + agent.id + "/connections", reloads);
  const pool = usePanelRead<PoolPayload>("/connections", reloads);
  const [attachName, setAttachName] = useState("");
  const attachEntry =
    pool.phase === "ready"
      ? pool.payload.connections.find((entry) => entry.grant === attachName)
      : undefined;
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
        title="Connectors"
        bar={
          <>
            {picker}
            {!picker ? (
              <>
                <Select value={attachName} onValueChange={setAttachName}>
                  <SelectTrigger aria-label="Connection"><SelectValue placeholder="Attach connection" /></SelectTrigger>
                  <SelectContent>
                    {pool.phase === "ready"
                      ? pool.payload.connections
                          .filter((entry) => !(entry.agents ?? []).some((attached) => attached.id === agent.id))
                          .map((entry) => <SelectItem key={entry.grant} value={entry.grant}>{entry.account_label ?? entry.account_id}</SelectItem>)
                      : null}
                  </SelectContent>
                </Select>
                <Button
                  variant="send"
                  disabled={!attachEntry}
                  onClick={() =>
                    attachEntry &&
                    act({
                      verb: "attach",
                      kind: "connector_grant",
                      name: attachEntry.grant,
                      spec: { provider: attachEntry.provider, account_id: attachEntry.account_id, shared: attachEntry.shared },
                    })
                  }
                >
                  Attach
                </Button>
              </>
            ) : null}
            <Input
              type="search"
              aria-label="Search"
              placeholder="Search"
              className="max-w-control-row"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <Button variant="send" onClick={() => setAdding(true)}>
              Add connector
            </Button>
          </>
        }
      >
        <Panel state={state}>
          {(payload) => (
            <DataTable
              columns={["Provider", "Account", "Owner", "Access", "Connected", ""]}
              rows={payload.connections.filter(
                (entry) => (!sharedOnly || entry.shared) && matches(entry, query),
              )}
              rowKey={(entry) => entry.grant}
              empty={
                sharedOnly
                  ? "No connector is shared with " + agent.name + " yet."
                  : "No account is connected to " + agent.name + " yet."
              }
              note={query ? "No connected account matches this search." : undefined}
            >
              {(entry) => (
                <>
                  <Td>{entry.provider}</Td>
                  <Td>{entry.account_label ?? entry.account_id ?? "—"}</Td>
                  <Td>{ownerLabel(entry.owner_email, viewer)}</Td>
                  <Td>{entry.shared ? "Workspace" : "Only you"}</Td>
                  <Td>{day(entry.connected_at)}</Td>
                  <Td>
                    {entry.own ? (
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
                              verb: "detach",
                              kind: "connector_grant",
                              name: entry.grant,
                            })
                          }
                        />
                      </div>
                    ) : null}
                  </Td>
                </>
              )}
            </DataTable>
          )}
        </Panel>
      </Section>
      {adding ? (
        <Dialog open onOpenChange={(next) => (next ? undefined : close())}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Add connector</DialogTitle>
            </DialogHeader>
            <OutcomeNotice state={refusal} />
            <form id="add-connector" onSubmit={connect} className="flex flex-col gap-xl">
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
              <Button type="submit" form="add-connector" variant="send" busy={busy}>
                Connect
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      ) : null}
    </>
  );
}
