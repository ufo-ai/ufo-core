import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import { Button, ConfirmButton } from "@/components/ui/button";
import { Checkbox, Field, Input, Label, Search } from "@/components/ui/field";
import { Facts, Group, type Fact } from "@/components/ui/facts";
import {
  BAR_CONTROL,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Td, TdFact } from "@/components/ui/table";
import { useBeside } from "@/kernel/beside";
import type { Placement } from "@/kernel/pager";
import { RecordPanel } from "@/kernel/pane";
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
import { DataTable, OPEN } from "@/kernel/table";
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

const PROVIDER = { label: "Provider", fact: true };
const ACCESS = { label: "Access", fact: true };
const POOL_COLUMNS = [PROVIDER, "Account", ACCESS, "Agents"];
const AGENT_COLUMNS = [PROVIDER, "Account", ACCESS];
const ATTACH_AGENT = "attach-agent";

/** What the record standing beside the table is headed by. A member holding two accounts on one
 *  provider tells them apart by the account, so that is the name; a connection the provider named
 *  no account for is the provider itself. */
function connectionName(entry: Connection): string {
  return entry.account_label ?? entry.account_id ?? entry.provider;
}

/** What the record states about the connection it heads: who holds it, and when it was made. Both
 *  are facts about that one connection, read by the member who opened it. */
function connectionFacts(entry: Connection, viewer: string | null): Fact[] {
  return [
    { label: "Owner", value: ownerLabel(entry.owner_email, viewer) },
    { label: "Connected", value: day(entry.connected_at) },
  ];
}

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
export function WorkspaceConnectors({ place }: { place: Placement }) {
  const query = place.q ?? "";
  const [reloads, setReloads] = useState(0);
  const [handoff, setHandoff] = useState<NoticeState>(QUIET);
  const [opened, setOpened] = useState("");
  const viewer = useViewer();
  const state = usePanelRead<PoolPayload>("/connections", reloads);
  const coverage = usePanelRead<GithubCoverage>("/github/coverage", reloads);
  const record =
    state.phase === "ready"
      ? state.payload.connections.find((entry) => entry.grant === opened)
      : undefined;
  if (state.phase === "ready" && opened && !record) setOpened("");

  const beside = useBeside(
    record ? (
      <PoolRecord
        key={record.grant}
        entry={record}
        viewer={viewer}
        onDone={(notice) => {
          setHandoff(notice);
          setReloads((count) => count + 1);
        }}
        onClose={() => setOpened("")}
      />
    ) : null,
    () => setOpened(""),
  );

  return (
    <>
      <Section>
        {handoff.text ? <Notice tone="quiet">{handoff.text}</Notice> : null}
        <Panel state={state}>
          {(payload) => (
            <DataTable
              columns={POOL_COLUMNS}
              rows={payload.connections.filter((entry) => matches(entry, query))}
              rowKey={(entry) => entry.provider + "-" + entry.account_id}
              empty="No connector is connected yet."
              note={query ? "No connected account matches this search." : undefined}
              open={(entry) => () => setOpened(entry.grant)}
              act={() => OPEN}
            >
              {(entry) => (
                <>
                  <TdFact>{entry.provider}</TdFact>
                  <Td>{entry.account_label ?? entry.account_id ?? "—"}</Td>
                  <TdFact>{entry.shared ? "Workspace" : "Only you"}</TdFact>
                  <Td>{(entry.agents ?? []).map((agent) => agent.name).join(", ") || "—"}</Td>
                </>
              )}
            </DataTable>
          )}
        </Panel>
        {coverage.phase === "ready" ? (
          <Group title="Coverage">
            <Facts
              rows={[
                { label: "API", value: coverage.payload.api ? "Connected" : "Not connected" },
                {
                  label: "Git push",
                  value: coverage.payload.git_push ? "Connected" : "Not connected",
                },
                { label: "Sources", value: coverage.payload.sources ? "Connected" : "Not connected" },
              ]}
            />
          </Group>
        ) : null}
      </Section>
      {beside}
    </>
  );
}

/** One connection of the pool, opened beside it: the connection's own facts and every act on it.
 *  The agent to attach to is picked here rather than in the bar, so the pick is this connection's
 *  and not whichever row the member presses next. */
function PoolRecord({
  entry,
  viewer,
  onDone,
  onClose,
}: {
  entry: PoolConnection;
  viewer: string | null;
  onDone: (notice: NoticeState) => void;
  onClose: () => void;
}) {
  const agents = useAgents();
  const [targetAgent, setTargetAgent] = useState("");
  const attached = (entry.agents ?? [])[0];

  async function act(lane: string, envelope: unknown) {
    onDone(outcomeNotice(await postIntent(lane, envelope)));
  }

  return (
    <RecordPanel title={connectionName(entry)} onClose={onClose}>
      <Facts rows={connectionFacts(entry, viewer)} />
      {entry.owner_email ? (
        <>
          <Field label="Agent" htmlFor={ATTACH_AGENT}>
            <Select value={targetAgent} onValueChange={setTargetAgent}>
              <SelectTrigger id={ATTACH_AGENT}>
                <SelectValue placeholder="Attach to agent" />
              </SelectTrigger>
              <SelectContent>
                {agents.map((agent) => (
                  <SelectItem key={agent.id} value={agent.id}>
                    {agent.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <div className="flex flex-wrap items-center gap-sm">
            <Button
              variant="send"
              size="bar"
              disabled={!targetAgent}
              onClick={() =>
                act(targetAgent, {
                  verb: "attach",
                  kind: "connector_grant",
                  name: entry.grant,
                  spec: {
                    provider: entry.provider,
                    account_id: entry.account_id,
                    shared: entry.shared,
                  },
                })
              }
            >
              Attach to agent
            </Button>
            {attached ? (
              <Button
                variant="row"
                onClick={() =>
                  act(attached.id, {
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
                {entry.shared ? "Unshare" : "Share"}
              </Button>
            ) : null}
            {attached ? (
              <ConfirmButton
                verb="Revoke"
                variant="row"
                onClick={async () => {
                  let last = QUIET;
                  for (const holder of entry.agents) {
                    last = outcomeNotice(
                      await postIntent(holder.id, {
                        verb: "detach",
                        kind: "connector_grant",
                        name: entry.grant,
                      }),
                    );
                    if (last.refused) break;
                  }
                  onDone(last);
                }}
              />
            ) : null}
          </div>
        </>
      ) : null}
    </RecordPanel>
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
  const [opened, setOpened] = useState("");
  const record =
    state.phase === "ready"
      ? state.payload.connections.find((entry) => entry.grant === opened)
      : undefined;
  if (state.phase === "ready" && opened && !record) setOpened("");
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

  const beside = useBeside(
    adding ? (
      <RecordPanel title="Add connector" onClose={close}>
        <OutcomeNotice state={refusal} />
        <form onSubmit={connect} className="flex flex-col gap-xl">
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
          <div className="flex justify-end">
            <Button type="submit" variant="send" size="bar" busy={busy}>
              Connect
            </Button>
          </div>
        </form>
      </RecordPanel>
    ) : null,
    close,
  );

  const shown = useBeside(
    record ? (
      <RecordPanel key={record.grant} title={connectionName(record)} onClose={() => setOpened("")}>
        <Facts rows={connectionFacts(record, viewer)} />
        {record.own ? (
          <div className="flex flex-wrap items-center gap-sm">
            <Button
              variant="send"
              size="bar"
              onClick={() =>
                act({
                  verb: "apply",
                  kind: "connector_grant",
                  name: record.grant,
                  spec: {
                    provider: record.provider,
                    account_id: record.account_id,
                    shared: !record.shared,
                  },
                })
              }
            >
              {record.shared ? "Make private" : "Share with agent"}
            </Button>
            <ConfirmButton
              verb="Revoke"
              variant="row"
              onClick={() =>
                act({ verb: "detach", kind: "connector_grant", name: record.grant })
              }
            />
          </div>
        ) : null}
      </RecordPanel>
    ) : null,
    () => setOpened(""),
  );

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
        bar={
          <>
            {picker}
            {!picker ? (
              <>
                <Select value={attachName} onValueChange={setAttachName}>
                  <SelectTrigger aria-label="Connection" className={BAR_CONTROL}><SelectValue placeholder="Attach connection" /></SelectTrigger>
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
                  size="bar"
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
            <Search
              label="Search"
              placeholder="Search"
              className="w-(--container-control-row)"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <Button variant="send" size="bar" onClick={() => setAdding(true)}>
              Add connector
            </Button>
          </>
        }
      >
        <Panel state={state}>
          {(payload) => (
            <DataTable
              columns={AGENT_COLUMNS}
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
              open={(entry) => () => setOpened(entry.grant)}
              act={() => OPEN}
            >
              {(entry) => (
                <>
                  <TdFact>{entry.provider}</TdFact>
                  <Td>{entry.account_label ?? entry.account_id ?? "—"}</Td>
                  <TdFact>{entry.shared ? "Workspace" : "Only you"}</TdFact>
                </>
              )}
            </DataTable>
          )}
        </Panel>
      </Section>
      {beside}
      {shown}
    </>
  );
}
