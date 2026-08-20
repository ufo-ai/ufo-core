import { IconCheck } from "@tabler/icons-react";
import { Fragment, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import { Button, ConfirmButton } from "@/components/ui/button";
import { Checkbox, Field, Input, Label, Search } from "@/components/ui/field";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemSeparator,
  ItemTitle,
} from "@/components/ui/item";
import { Facts, type Fact } from "@/components/ui/facts";
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
  PanelBlank,
  type PanelState,
  QUIET,
  Section,
  outcomeNotice,
  usePanelRead,
} from "@/kernel/panel";
import { rowControl } from "@/kernel/row";
import { DataTable, OPEN } from "@/kernel/table";
import { BrandMark } from "@/lib/brandMark";
import { cn } from "@/lib/cn";
import { ConsentLink, openConsentWindow } from "@/lib/consent";
import { Moment } from "@/lib/moments";
import { agentName } from "@/lib/agentName";
import { BASE, postIntent } from "@/lib/api";
import { ownerLabel, useViewer } from "@/lib/audience";
import { ProviderGlyph } from "@/lib/providerGlyph";
import { useAgents, useMainAgent } from "@/lib/mainAgent";
import type { Agent } from "@/lib/types";
import {
  CONNECT_VERB,
  FIRST_RUN_READ,
  WATCH_MS,
  type FirstRunPayload,
} from "@/views/FirstRun";

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
export type PoolConnection = Connection & { agents: { id: string; name: string }[] };
export type PoolPayload = { connections: PoolConnection[] };
type GithubCoverage = { api: boolean; git_push: boolean; sources: boolean };

const PROVIDER = { label: "Provider", fact: true };
const ACCESS = { label: "Access", fact: true };
const AGENT_COLUMNS = [PROVIDER, "Account", ACCESS];
const ATTACH_AGENT = "attach-agent";
/** What a press that never became a request states. Both connector screens draw it, so it is said
 *  once: a member reads one sentence for one outcome wherever they pressed. */
export const CONNECT_REFUSED =
  "No connection request was opened. Ask in chat to connect the account.";

/** What names the account to the member: the label the provider gave it, else the address it is
 *  held under. The broker's id names neither, so it stands only where the connection carries
 *  nothing else — the record that opens beside the table is where it is read. */
function accountName(entry: Connection): string {
  return entry.account_label ?? entry.owner_email ?? entry.account_id ?? "—";
}

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
    { label: "Connected", value: <Moment at={entry.connected_at} /> },
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

/** Every connector the member holds on one agent, the agent chosen in the bar. The agent's own
 *  settings state the same records narrowed to what that agent can actually reach; both read the
 *  one grant list, so nothing here needs a second endpoint. */
/** What a connected row says under the tool's name: the account it stands on, and who reaches it.
 *  A member holding the account alone reads a different sentence from one the whole workspace
 *  shares, and that difference is the fact worth stating on a row they scan rather than open. */
function accountLine(entry: Connection): string {
  return accountName(entry) + " \u00b7 " + (entry.shared ? "Workspace" : "Only you");
}

/** One tool already reachable: a connection this member can see, or a workspace install. An install
 *  carries no connection row, so it holds no grant and opens no record — what it states is already
 *  true, and the acts on a grant belong to the grant. */
type Standing = {
  key: string;
  name: string;
  label: string;
  detail: ReactNode;
  said: string;
  grant: string | null;
};

/** One tool the catalog still offers. */
type Offer = { name: string; label: string; summary: string; group: string };

/** Everything already standing: each workspace install the workspace holds, then every connection
 *  in the pool. The pool is listed whole rather than folded onto the catalog — the brokers reach
 *  further than the catalog names, and a member holding two accounts on one provider holds two
 *  rows. */
function standing(catalog: FirstRunPayload, pool: PoolPayload): Standing[] {
  const tiles = new Map(catalog.providers.map((tile) => [tile.name, tile]));
  const installs = catalog.connectors
    .filter((row) => row.installed)
    .map((row) => ({
      key: "install:" + row.name,
      name: row.name,
      label: row.label,
      detail: tiles.get(row.name)?.summary ?? "",
      said: row.label + " " + row.name,
      grant: null,
    }));
  return [
    ...installs,
    ...pool.connections.map((entry) => ({
      key: entry.grant,
      name: entry.provider,
      label: tiles.get(entry.provider)?.label ?? entry.provider,
      detail: (
        <>
          {accountLine(entry)}
          {" \u00b7 "}
          <Moment at={entry.connected_at} />
        </>
      ),
      said: [entry.provider, entry.account_label ?? "", entry.account_id ?? "", entry.owner_email ?? ""].join(" "),
      grant: entry.grant,
    })),
  ];
}

/** What the catalog still offers: a tool the workspace has not installed, and one no connection in
 *  the pool already stands for. An install reads its own flag and never the pool — the press
 *  installs the workspace leg, and a member's own account on the same provider does not fill what
 *  the workspace still lacks. */
function offers(catalog: FirstRunPayload, pool: PoolPayload): Offer[] {
  const installs = new Map(catalog.connectors.map((row) => [row.name, row.installed]));
  const connected = new Set(pool.connections.map((entry) => entry.provider));
  return catalog.providers.filter((tile) => {
    const installed = installs.get(tile.name);
    return installed === undefined ? !connected.has(tile.name) : !installed;
  });
}

/** The catalog's own order carried into headed runs, so a group's tools arrive together and no page
 *  sorts what the read already ordered. */
function grouped(rows: Offer[]): [string, Offer[]][] {
  const groups: [string, Offer[]][] = [];
  for (const row of rows) {
    const last = groups.at(-1);
    if (last && last[0] === row.group) last[1].push(row);
    else groups.push([row.group, [row]]);
  }
  return groups;
}

/** What the GitHub row opens beside the list. The three legs are GitHub's alone, so they are read
 *  where every other detail of a connector is read — its own record — rather than as a block on the
 *  page's ground under a heading that names no provider. */
const COVERAGE = "github-coverage";

const GITHUB = "github";

/** The link left standing when the browser refused the consent window — the one case with nothing
 *  else to carry the member over. */
type Handoff = { url: string; text: string };

function joined(
  catalog: PanelState<FirstRunPayload>,
  pool: PanelState<PoolPayload>,
): PanelState<{ catalog: FirstRunPayload; pool: PoolPayload }> {
  if (catalog.phase === "failed") return catalog;
  if (pool.phase === "failed") return pool;
  if (catalog.phase === "loading" || pool.phase === "loading") return { phase: "loading" };
  return { phase: "ready", payload: { catalog: catalog.payload, pool: pool.payload } };
}

/** The workspace's connector library: every account already reachable, then every tool the catalog
 *  still offers under the group headings it carries. A connected row opens its own record beside
 *  the list, which is where a grant is attached, shared or revoked. A workspace install dispatches
 *  its own admin-gated verb and the outcome carries the install link; every other row opens the
 *  broker's per-member consent through the main agent, with the URL riding the turn's stream. Both
 *  open the consent window on the press itself, so one press is the whole act. While a press waits
 *  on the provider's pages, both reads re-read at the watch cadence and the row moves up the moment
 *  the account lands. */
export function WorkspaceConnectors({ place }: { place: Placement }) {
  const query = place.q ?? "";
  const [reloads, setReloads] = useState(0);
  const [opened, setOpened] = useState("");
  const [waiting, setWaiting] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [watching, setWatching] = useState<string | null>(null);
  const [handoff, setHandoff] = useState<Handoff | null>(null);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const consent = useRef<Window | null>(null);
  const viewer = useViewer();
  const agent = useMainAgent();
  const catalog = usePanelRead<FirstRunPayload>(
    FIRST_RUN_READ,
    reloads,
    waiting ? WATCH_MS : undefined,
  );
  const pool = usePanelRead<PoolPayload>("/connections", reloads, waiting ? WATCH_MS : undefined);
  const coverage = usePanelRead<GithubCoverage>("/github/coverage", reloads);
  const state = joined(catalog, pool);
  const held =
    state.phase === "ready" ? standing(state.payload.catalog, state.payload.pool) : null;
  if (waiting && held?.some((row) => row.name === waiting)) setWaiting(null);
  const record =
    pool.phase === "ready"
      ? pool.payload.connections.find((entry) => entry.grant === opened)
      : undefined;
  if (pool.phase === "ready" && opened && opened !== COVERAGE && !record) setOpened("");

  useEffect(() => {
    if (watching === null) return;
    const stream = new EventSource(BASE + "/turns/" + watching + "/stream");
    const done = () => stream.close();
    let asked = false;
    stream.addEventListener("connect", () => {
      asked = true;
      const url = BASE + "/turns/" + watching + "/connect";
      if (consent.current) consent.current.location.href = url;
      setHandoff(consent.current ? null : { url, text: "Open the provider consent page" });
      consent.current = null;
      setNotice(QUIET);
      done();
    });
    stream.addEventListener("terminal", () => {
      if (!asked) {
        consent.current?.close();
        consent.current = null;
        setNotice({ text: CONNECT_REFUSED, refused: true });
        setWaiting(null);
      }
      done();
    });
    stream.onerror = done;
    return done;
  }, [watching]);

  async function connect(name: string, label: string) {
    if (busy || !agent) return;
    setBusy(name);
    setHandoff(null);
    setNotice(QUIET);
    // Opened on the press, before the round trip that mints the link: a window opened afterwards
    // has lost the gesture the browser opens one for.
    consent.current = openConsentWindow();
    const install = CONNECT_VERB[name];
    const outcome = await postIntent(
      agent.id,
      install
        ? { verb: install }
        : { verb: "connect", kind: "connection", name, spec: { shared: false } },
    );
    setBusy(null);
    if (!outcome.applied) {
      consent.current?.close();
      consent.current = null;
      setNotice(outcomeNotice(outcome));
      return;
    }
    if (!install) {
      setWaiting(name);
      setWatching(outcome.turn_id ?? null);
      return;
    }
    const url = outcome.url;
    if (!url) {
      consent.current?.close();
      consent.current = null;
      setNotice(outcomeNotice(outcome));
      return;
    }
    setWaiting(name);
    if (consent.current) consent.current.location.href = url;
    setHandoff(consent.current ? null : { url, text: "Open the " + label + " install page" });
    consent.current = null;
  }

  const legs =
    coverage.phase === "ready"
      ? [
          { label: "API", value: coverage.payload.api ? "Connected" : "Not connected" },
          { label: "Git push", value: coverage.payload.git_push ? "Connected" : "Not connected" },
          { label: "Sources", value: coverage.payload.sources ? "Connected" : "Not connected" },
        ]
      : [];

  const beside = useBeside(
    opened === COVERAGE ? (
      <RecordPanel title="GitHub" onClose={() => setOpened("")}>
        <Facts rows={legs} />
      </RecordPanel>
    ) : record ? (
      <PoolRecord
        key={record.grant}
        entry={record}
        viewer={viewer}
        onDone={(outcome) => {
          setNotice(outcome);
          setReloads((count) => count + 1);
        }}
        onClose={() => setOpened("")}
      />
    ) : null,
    () => setOpened(""),
  );

  return (
    <>
      {handoff ? (
        <Notice>
          <ConsentLink url={handoff.url}>{handoff.text}</ConsentLink>
        </Notice>
      ) : null}
      <OutcomeNotice state={notice} />
      <Panel state={state}>
        {(reads) => {
          const term = query.toLowerCase();
          const mine = standing(reads.catalog, reads.pool).filter((row) =>
            (row.label + " " + row.said).toLowerCase().includes(term),
          );
          const open = offers(reads.catalog, reads.pool).filter((row) =>
            (row.label + " " + row.name + " " + row.summary).toLowerCase().includes(term),
          );
          if (!mine.length && !open.length) {
            return (
              <PanelBlank
                body={query ? "No connector matches this search." : "No connector is offered yet."}
              />
            );
          }
          return (
            <div className="flex flex-col gap-6xl">
              {!mine.length ? null : (
                <Section title="Connected" note="Accounts the app can use now.">
                  <ItemGroup>
                    {mine.map((row, index) => (
                      <Fragment key={row.key}>
                        {index ? <ItemSeparator /> : null}
                        <Row
                          name={row.name}
                          label={row.label}
                          detail={row.detail}
                          open={
                            row.grant
                              ? () => setOpened(row.grant as string)
                              : row.name === GITHUB
                                ? () => setOpened(COVERAGE)
                                : null
                          }
                          act={
                            <span className="flex items-center gap-xs text-label text-ink-soft">
                              <IconCheck
                                role="img"
                                aria-label={row.label + " connected"}
                                className="size-icon"
                              />
                              Connected
                            </span>
                          }
                        />
                      </Fragment>
                    ))}
                  </ItemGroup>
                </Section>
              )}
              {!open.length ? null : (
                <Section title="Available" note="Connect an account to let the app reach it.">
                  <div className="flex flex-col gap-4xl">
                    {grouped(open).map(([group, members]) => (
                      <div key={group} className="flex flex-col gap-sm">
                        <h3 className="m-0 flex items-baseline gap-xs text-label font-medium text-ink-soft">
                          {group}
                          <span className="font-normal text-ink-faint">{members.length}</span>
                        </h3>
                        <ItemGroup>
                          {members.map((row, index) => (
                            <Fragment key={row.name}>
                              {index ? <ItemSeparator /> : null}
                              <Row
                                name={row.name}
                                label={row.label}
                                detail={row.summary}
                                open={row.name === GITHUB ? () => setOpened(COVERAGE) : null}
                                act={
                                  <Button
                                    variant="outline"
                                    size="bar"
                                    busy={waiting === row.name}
                                    disabled={busy !== null && waiting !== row.name}
                                    onClick={() => connect(row.name, row.label)}
                                  >
                                    {waiting === row.name ? "Connecting" : "Connect"}
                                  </Button>
                                }
                              />
                            </Fragment>
                          ))}
                        </ItemGroup>
                      </div>
                    ))}
                  </div>
                </Section>
              )}
            </div>
          );
        }}
      </Panel>
      {beside}
    </>
  );
}

const MARK_TILE = cn(
  "flex size-(--size-touch) shrink-0 items-center justify-center",
  "rounded-control bg-fill",
);

/** One tool of the library, drawn as a row: the product's own mark, its name and either what the
 *  agent does with it or the account it already stands on, and the one act left on it at the right
 *  edge. A row holding a grant is the control that opens that grant's record, and a press landing
 *  on the act itself never also opens it. */
function Row({
  name,
  label,
  detail,
  open,
  act,
}: {
  name: string;
  label: string;
  detail: ReactNode;
  open: (() => void) | null;
  act: ReactNode;
}) {
  const control = open ? rowControl(open) : null;
  return (
    <Item {...control} className={cn(control?.className, open && "hover:bg-fill")}>
      <span className={MARK_TILE}>
        <BrandMark provider={name} className="text-ink" />
      </span>
      <ItemContent>
        <ItemTitle>{label}</ItemTitle>
        <ItemDescription>{detail}</ItemDescription>
      </ItemContent>
      <ItemActions>{act}</ItemActions>
    </Item>
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
          <Field label="App" htmlFor={ATTACH_AGENT}>
            <Select value={targetAgent} onValueChange={setTargetAgent}>
              <SelectTrigger id={ATTACH_AGENT}>
                <SelectValue placeholder="Attach to app" />
              </SelectTrigger>
              <SelectContent>
                {agents.map((agent) => (
                  <SelectItem key={agent.id} value={agent.id}>
                    {agentName(agent.name)}
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
              Attach to app
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

/** What this agent can reach, read as a section of the agent's settings: a grant the member kept
 *  private is theirs, not the agent's, so the agent's own section does not list it. */
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
  // Opened on the press and pointed at the provider when the frame carrying the URL lands. The
  // press is the whole act: the wait between them is the typed verb and the broker's own call, no
  // model round, and nothing appears under the button for the member to find and press again.
  const consent = useRef<Window | null>(null);

  useEffect(() => {
    if (watching === null) return;
    const stream = new EventSource(BASE + "/turns/" + watching + "/stream");
    source.current = stream;
    const done = () => {
      stream.close();
      source.current = null;
    };
    // The request stands once the turn commits it; the address below is what mints the consent URL,
    // so the window this press opened is pointed at our own surface and redirected from there.
    let asked = false;
    stream.addEventListener("connect", () => {
      asked = true;
      const url = BASE + "/turns/" + watching + "/connect";
      if (consent.current) consent.current.location.href = url;
      // The link stands only for a member whose browser refused the window.
      setConsentUrl(consent.current ? null : url);
      consent.current = null;
      setHandoff(QUIET);
      done();
    });
    // A turn that ended without asking is a refusal the member is still waiting on, and the window
    // they pressed for has nowhere to go.
    stream.addEventListener("terminal", () => {
      if (!asked) {
        consent.current?.close();
        consent.current = null;
        setHandoff({ text: CONNECT_REFUSED, refused: true });
      }
      done();
    });
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
    consent.current = openConsentWindow();
    const outcome = await postIntent(agent.id, {
      verb: "connect",
      kind: "connection",
      name: named,
      spec: { shared },
    });
    setBusy(false);
    if (!outcome.applied) {
      consent.current?.close();
      consent.current = null;
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
    if (outcome.applied) setAttachName("");
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
            <Label htmlFor="connect-shared">Share with app</Label>
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
              {record.shared ? "Make private" : "Share with app"}
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
            <ConsentLink url={consentUrl}>Open the provider consent page</ConsentLink>
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
                          .map((entry) => (
                            <SelectItem
                              key={entry.grant}
                              value={entry.grant}
                              textValue={entry.provider + " " + accountName(entry)}
                            >
                              <span className="flex min-w-0 items-center gap-sm">
                                <ProviderGlyph provider={entry.provider} />
                                <span className="shrink-0">{entry.provider}</span>
                                <span className="min-w-0 truncate">{accountName(entry)}</span>
                                <span className="shrink-0 text-ink-soft">
                                  {ownerLabel(entry.owner_email, viewer)}
                                </span>
                              </span>
                            </SelectItem>
                          ))
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
                  ? "No connector is shared with " + agentName(agent.name) + " yet."
                  : "No account is connected to " + agentName(agent.name) + " yet."
              }
              note={query ? "No connected account matches this search." : undefined}
              open={(entry) => () => setOpened(entry.grant)}
              act={() => OPEN}
            >
              {(entry) => (
                <>
                  <TdFact>{entry.provider}</TdFact>
                  <Td>{accountName(entry)}</Td>
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
