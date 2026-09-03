import { IconCheck } from "@tabler/icons-react";
import { Fragment, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import { Button, ConfirmButton } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Checkbox, Field, Input, Label, Search } from "@/components/ui/field";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemSeparator,
  ItemTitle,
  MarkTile,
} from "@/components/ui/item";
import { Facts, type Fact } from "@/components/ui/facts";
import { Sheet } from "@/components/ui/sheet";
import {
  BAR_CONTROL,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Td, TdFact } from "@/components/ui/table";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import type { Placement } from "@/kernel/pager";
import { PageToolbar, usePageSearch } from "@/kernel/pane";
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
import { appended, beside, closed, opened } from "@/kernel/slots";
import { DataTable, OPEN } from "@/kernel/table";
import { BrandMark } from "@/lib/brandMark";
import { cn } from "@/lib/cn";
import { ConsentLink, openConsentWindow } from "@/lib/consent";
import { Moment } from "@/lib/moments";
import { agentName } from "@/lib/agentName";
import { BASE, getJson, postIntent, postObjectAction, type Fetched } from "@/lib/api";
import { ownerLabel, useViewer } from "@/lib/audience";
import { ProviderGlyph } from "@/lib/providerGlyph";
import { connectArrival } from "@/lib/router";
import { useAgents, useMainAgent } from "@/lib/mainAgent";
import type { Agent } from "@/lib/types";
import { FIRST_RUN_READ, WATCH_MS, type FirstRunPayload } from "@/views/FirstRun";
import { CONNECT_INSTALLS } from "@/views/Surfaces";

type SourcesPayload = {
  sources: {
    backend: string;
    account_id: string | null;
    stream: string;
    consecutive_errors: number;
    parked_reason: string | null;
  }[];
};

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
type ConnectorCatalogPayload = {
  providers: { name: string; label: string }[];
  after: string | null;
};
type GithubCoverage = { api: boolean; sources: boolean };

const PROVIDER = { label: "Provider", fact: true };
const ACCESS = { label: "Access", fact: true };
const AGENT_COLUMNS = [PROVIDER, "Account", ACCESS];
const ATTACH_AGENT = "attach-agent";
/** What a press that never became a request states. Both connector screens draw it, so it is said
 *  once: a member reads one sentence for one outcome wherever they pressed. */
export const CONNECT_REFUSED =
  "No connection request was opened. Ask in chat to connect the account.";

function accountName(entry: Connection): string {
  return entry.account_label ?? entry.owner_email ?? entry.account_id ?? "—";
}

function connectionName(entry: Connection): string {
  return entry.account_label ?? entry.account_id ?? entry.provider;
}

function connectionFacts(
  entry: Connection,
  viewer: string | null,
  sources: SourcesPayload | null,
): Fact[] {
  const facts = [
    { label: "Owner", value: ownerLabel(entry.owner_email, viewer) },
    { label: "Connected", value: <Moment at={entry.connected_at} /> },
  ];
  if (sources === null) return facts;
  const streams = sources.sources.filter(
    (source) => source.backend === entry.provider && source.account_id === entry.account_id,
  );
  const errors = streams.reduce((total, one) => total + one.consecutive_errors, 0);
  const parked = streams.filter((one) => one.parked_reason !== null);
  return [
    ...facts,
    { label: "Streams", value: streams.map((one) => one.stream).sort().join(", ") || "\u2014", block: true },
    {
      label: "Errors",
      value: parked.length ? (
        <span title={parked.map((one) => one.parked_reason).join(" ")}>
          {errors} · {parked.length} parked
        </span>
      ) : (
        String(errors)
      ),
    },
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
 *  carries no connection row, so it holds no connection and opens no record — what it states is
 *  already true, and the acts on a connection belong to the connection. */
type Standing = {
  key: string;
  name: string;
  label: string;
  detail: ReactNode;
  said: string;
  group: string;
  entry: PoolConnection | null;
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
      group: tiles.get(row.name)?.group ?? "",
      entry: null,
    }));
  return [
    ...installs,
    ...pool.connections.map((entry) => ({
      key: entry.grant,
      name: entry.provider,
      label: tiles.get(entry.provider)?.label ?? entry.provider,
      detail: (
        <>
          {entry.agents.length
            ? (entry.agents.length === 1 ? "App: " : "Apps: ") +
              entry.agents.map((agent) => agentName(agent.name)).join(", ")
            : "No apps"}
          {" \u00b7 "}
          {accountLine(entry)}
          {" \u00b7 "}
          <Moment at={entry.connected_at} />
        </>
      ),
      said: [
        entry.provider,
        entry.account_label ?? "",
        entry.account_id ?? "",
        entry.owner_email ?? "",
        ...entry.agents.map((agent) => agent.name),
      ].join(" "),
      group: tiles.get(entry.provider)?.group ?? "",
      entry,
    })),
  ];
}

/** What the catalog still offers: a tool the workspace has not installed, and one this member has
 *  not connected an account of their own on. A connection another member shares with the workspace
 *  does not fill that: a member's turns run on their own account first, so the tool stays offered
 *  beside the shared row until they connect theirs. Whose an account is, is the owner address
 *  against the viewer, never `own` — that flag is whether the viewer may manage the account, which
 *  a workspace admin may on every member's. An install reads its own flag and never the pool — the
 *  press installs the workspace leg, and a member's own account on the same provider does not fill
 *  what the workspace still lacks. */
function offers(catalog: FirstRunPayload, pool: PoolPayload, viewer: string | null): Offer[] {
  const installs = new Map(catalog.connectors.map((row) => [row.name, row.installed]));
  const connected = new Set(
    pool.connections
      .filter((entry) => entry.owner_email !== null && entry.owner_email === viewer)
      .map((entry) => entry.provider),
  );
  return catalog.providers.filter((tile) => {
    const installed = installs.get(tile.name);
    return installed === undefined ? !connected.has(tile.name) : !installed;
  });
}

/** Whose a standing row is: the owner address against the viewer, never `own` — that flag is
 *  whether the viewer may manage the account, which a workspace admin may on every member's. A
 *  workspace install carries no connection row and stands with what the workspace shares, not with
 *  what the member connected. */
function ownHeld(row: Standing, viewer: string | null): boolean {
  return row.entry !== null && row.entry.owner_email !== null && row.entry.owner_email === viewer;
}

/** The two headed zones a connected row falls into. A shared account another member holds reads on
 *  a row exactly like an own one, and a remove pressed on it disconnects that member — so the two
 *  stand apart, under headings that say whose the accounts under them are. */
const HELD_ZONES: [string, string, (row: Standing, viewer: string | null) => boolean][] = [
  ["Your connections", "Accounts you connected, and the apps that can use them.", ownHeld],
  [
    "Shared with the workspace",
    "Accounts another member connected, and the tools the workspace installed.",
    (row, viewer) => !ownHeld(row, viewer),
  ],
];

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

/** Every category the catalog carries, in the order it carries them, so the picker lists them the
 *  way the page stacks them. */
function categories(catalog: FirstRunPayload): string[] {
  return [...new Set(catalog.providers.map((tile) => tile.group))];
}

/** What the picker stands at when no category is picked. Radix names an option by a value, and an
 *  empty one is how it says nothing is selected — so the whole catalog is a named choice. */
const EVERY_CATEGORY = "all";

const COVERAGE = "github-coverage";

const CONNECTION = "connection/";

const GITHUB = "github";

/** The link left standing when the browser refused the consent window — the one case with nothing
 *  else to carry the member over. */
type Handoff = { url: string; text: string };
const CONNECTOR_BATCH_MIN = 25;
const CONNECTOR_BATCH_CURSOR_LIMIT = 25;

function connectorCatalogPath(query: string, after?: string): string {
  const params = new URLSearchParams();
  if (query) params.set("q", query);
  if (after) params.set("after", after);
  return "/connector-catalog?" + params.toString();
}

function useConnectorCatalog(query: string, reloads: number) {
  const [state, setState] = useState<PanelState<ConnectorCatalogPayload>>({ phase: "loading" });
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<NoticeState>(QUIET);
  const generation = useRef(0);

  useEffect(() => {
    const current = ++generation.current;
    const request = new AbortController();
    setState({ phase: "loading" });
    setLoadingMore(false);
    setError(QUIET);
    getJson<ConnectorCatalogPayload>(connectorCatalogPath(query), request.signal).then((result) => {
      if (current !== generation.current) return;
      setError(result.ok ? QUIET : { text: result.message, refused: true });
      setState(
        result.ok
          ? { phase: "ready", payload: result.payload }
          : { phase: "failed", message: result.message, status: result.status },
      );
    });
    return () => {
      if (generation.current === current) generation.current += 1;
      request.abort();
    };
  }, [query, reloads]);

  async function loadMore() {
    if (state.phase !== "ready" || !state.payload.after || loadingMore) return;
    setLoadingMore(true);
    const current = generation.current;
    const known = new Set(state.payload.providers.map((row) => row.name));
    const additions: ConnectorCatalogPayload["providers"] = [];
    let after: string | null = state.payload.after;
    let failure: NoticeState = QUIET;
    for (
      let cursorCount = 0;
      after && additions.length < CONNECTOR_BATCH_MIN && cursorCount < CONNECTOR_BATCH_CURSOR_LIMIT;
      cursorCount += 1
    ) {
      const requested: string = after;
      const result: Fetched<ConnectorCatalogPayload> = await getJson<ConnectorCatalogPayload>(
        connectorCatalogPath(query, requested),
      );
      if (current !== generation.current) return;
      if (!result.ok) {
        failure = { text: result.message, refused: true };
        break;
      }
      for (const row of result.payload.providers) {
        if (known.has(row.name)) continue;
        known.add(row.name);
        additions.push(row);
      }
      after = result.payload.after;
    }
    setError(failure);
    setState((held) => {
      if (held.phase !== "ready") return held;
      return {
        phase: "ready",
        payload: {
          providers: [...held.payload.providers, ...additions],
          after,
        },
      };
    });
    setLoadingMore(false);
  }

  return {
    state,
    loadingMore,
    error,
    loadMore,
  };
}

function joined(
  catalog: PanelState<FirstRunPayload>,
  pool: PanelState<PoolPayload>,
  expanded: PanelState<ConnectorCatalogPayload>,
): PanelState<{ catalog: FirstRunPayload; pool: PoolPayload }> {
  if (catalog.phase === "failed") return catalog;
  if (pool.phase === "failed") return pool;
  if (catalog.phase === "loading" || pool.phase === "loading") return { phase: "loading" };
  const known = new Set(catalog.payload.providers.map((row) => row.name));
  const expandedProviders = expanded.phase === "ready" ? expanded.payload.providers : [];
  return {
    phase: "ready",
    payload: {
      catalog: {
        ...catalog.payload,
        providers: [
          ...catalog.payload.providers,
          ...expandedProviders
            .filter((row) => !known.has(row.name))
            .map((row) => ({
              ...row,
              summary: "Connect this account to use its tools.",
              group: "More connectors",
            })),
        ],
      },
      pool: pool.payload,
    },
  };
}

/** The connect a forwarded tab arrived carrying, said the way an outcome whose own surface has gone
 *  is said. The callback page took itself away to bring the member here, so the sentence they read
 *  there is the sentence that stands here — one wording for one outcome, wherever it is read.
 *
 *  Read while the screen first draws, before the router has taken the arrival off the address, and
 *  held in state from there: a member who walks back onto this screen is not arriving from a
 *  provider. A member who pressed that page's own button reads nothing either — the button carries
 *  no account, because they were looking at the outcome when they chose to come. */
function arrivedToast(): ToastState {
  const named = connectArrival();
  return named ? { title: named + " connected." } : SILENT;
}

/** The workspace's available and connected accounts. */
export function WorkspaceConnectors({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const query = place.q ?? "";
  const picked = place.chip ?? EVERY_CATEGORY;
  const opens = place.opens ?? [];
  const [reloads, setReloads] = useState(0);
  const [waiting, setWaiting] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [watching, setWatching] = useState<string | null>(null);
  const [handoff, setHandoff] = useState<Handoff | null>(null);
  const [removing, setRemoving] = useState<Standing | null>(null);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [toast, setToast] = useState<ToastState>(arrivedToast);
  const consent = useRef<Window | null>(null);
  const viewer = useViewer();
  const agent = useMainAgent();
  const agents = useAgents();
  const catalog = usePanelRead<FirstRunPayload>(
    FIRST_RUN_READ,
    reloads,
    waiting ? WATCH_MS : undefined,
  );
  const pool = usePanelRead<PoolPayload>("/connections", reloads, waiting ? WATCH_MS : undefined);
  const expanded = useConnectorCatalog(query, reloads);
  const coverage = usePanelRead<GithubCoverage>("/github/coverage", reloads);
  const state = joined(catalog, pool, expanded.state);
  const held =
    state.phase === "ready" ? standing(state.payload.catalog, state.payload.pool) : null;
  if (waiting && held?.some((row) => row.name === waiting)) setWaiting(null);
  const pooled = pool.phase === "ready" ? pool.payload.connections : [];

  function show(id: string, aside: boolean) {
    onPlace({ opens: aside ? appended(opens, id) : opened(opens, id) });
  }

  function shut(id: string) {
    onPlace({ opens: closed(opens, id) });
  }

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
    const install = CONNECT_INSTALLS[name];
    const outcome = install
      ? await postObjectAction(agent.id, install, {})
      : await postIntent(agent.id, {
          verb: "connect",
          kind: "connection",
          name,
          spec: { shared: false },
        });
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
          { label: "Sources", value: coverage.payload.sources ? "Connected" : "Not connected" },
        ]
      : [];

  const sheet = opens.slice(-1).map((id) => {
    if (id === COVERAGE) return <CoverageRecord key={id} legs={legs} onClose={() => shut(id)} />;
    const entry = pooled.find((one) => CONNECTION + one.grant === id);
    if (!entry) return null;
    return (
      <ConnectionRecord
        key={id}
        entry={entry}
        viewer={viewer}
        attachTo={agents}
        holders={entry.agents ?? []}
        onDone={(outcome) => {
          setNotice(outcome);
          setReloads((count) => count + 1);
        }}
        onClose={() => shut(id)}
      />
    );
  });

  const search = usePageSearch();

  return (
    <>
      {search || (catalog.phase === "ready" && categories(catalog.payload).length > 1) ? (
        <PageToolbar>
          {catalog.phase === "ready" && categories(catalog.payload).length > 1 ? (
          <Select
            value={picked}
            onValueChange={(next) =>
              onPlace({ chip: next === EVERY_CATEGORY ? undefined : next })
            }
          >
            <SelectTrigger aria-label="Category" className={cn(BAR_CONTROL, "w-auto")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={EVERY_CATEGORY}>All categories</SelectItem>
              {categories(catalog.payload).map((group) => (
                <SelectItem key={group} value={group}>
                  {group}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          ) : null}
        </PageToolbar>
      ) : null}
      {handoff ? (
        <Notice>
          <ConsentLink url={handoff.url}>{handoff.text}</ConsentLink>
        </Notice>
      ) : null}
      <OutcomeNotice state={notice} />
      <OutcomeNotice state={expanded.error} />
      <Panel state={state}>
        {(reads) => {
          const term = query.toLowerCase();
          const inCategory = (group: string) =>
            picked === EVERY_CATEGORY || group === picked;
          const mine = standing(reads.catalog, reads.pool).filter(
            (row) =>
              inCategory(row.group) &&
              (row.label + " " + row.said).toLowerCase().includes(term),
          );
          const open = offers(reads.catalog, reads.pool, viewer).filter(
            (row) =>
              inCategory(row.group) &&
              (row.label + " " + row.name + " " + row.summary).toLowerCase().includes(term),
          );
          const more = expanded.state.phase === "ready" && expanded.state.payload.after;
          if (!mine.length && !open.length && !more) {
            return (
              <PanelBlank
                body={
                  query || picked !== EVERY_CATEGORY
                    ? "No connector matches this search."
                    : "No connector is offered yet."
                }
              />
            );
          }
          return (
            <div className="flex flex-col gap-8xl">
              {HELD_ZONES.map(([title, note, holds]) => {
                const rows = mine.filter((row) => holds(row, viewer));
                if (!rows.length) return null;
                return (
                  <Section key={title} title={title} note={note}>
                    <ItemGroup>
                      {rows.map((row, index) => {
                        const held = row.entry;
                        return (
                          <Fragment key={row.key}>
                            {index ? <ItemSeparator /> : null}
                            <Row
                              name={row.name}
                              label={row.label}
                              detail={row.detail}
                              open={
                                held
                                  ? (aside) => show(CONNECTION + held.grant, aside)
                                  : row.name === GITHUB
                                    ? (aside) => show(COVERAGE, aside)
                                    : null
                              }
                              current={opens.includes(held ? CONNECTION + held.grant : COVERAGE)}
                              act={
                                <>
                                  {/* The word gives way to the act on a phone: a row under a
                                      connected heading already says it is connected, and the
                                      remove needs the width the chip was taking. */}
                                  <span
                                    className={cn(
                                      "flex items-center gap-xs text-label text-ink-soft",
                                      held?.own && "max-narrow:hidden",
                                    )}
                                  >
                                    <IconCheck
                                      role="img"
                                      aria-label={row.label + " connected"}
                                      className="size-icon"
                                    />
                                    Connected
                                  </span>
                                  {held?.own ? (
                                    <Button
                                      variant="row"
                                      aria-label={"Remove " + row.label}
                                      onClick={() => setRemoving(row)}
                                    >
                                      Remove
                                    </Button>
                                  ) : null}
                                </>
                              }
                            />
                          </Fragment>
                        );
                      })}
                    </ItemGroup>
                  </Section>
                );
              })}
              {!open.length && !more ? null : (
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
                                open={
                                  row.name === GITHUB ? (aside) => show(COVERAGE, aside) : null
                                }
                                current={row.name === GITHUB && opens.includes(COVERAGE)}
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
                    {more ? (
                      <div>
                        <Button
                          variant="row"
                          busy={expanded.loadingMore}
                          onClick={expanded.loadMore}
                        >
                          Load more
                        </Button>
                      </div>
                    ) : null}
                  </div>
                </Section>
              )}
            </div>
          );
        }}
      </Panel>
      {sheet}
      <Dialog open={removing !== null} onOpenChange={(next) => !next && setRemoving(null)}>
        {removing?.entry && agent ? (
          <RemoveConnection
            agentId={agent.id}
            entry={removing.entry}
            label={removing.label}
            onDone={(outcome) => {
              setRemoving(null);
              setNotice(outcome);
              setReloads((count) => count + 1);
            }}
          />
        ) : null}
      </Dialog>
      <Toast state={toast} onDone={() => setToast(SILENT)} />
    </>
  );
}

/** What a per-account remove commits, and the step it stands behind. The provider and the account
 *  are named here rather than on the row, because a row states one line and a member about to
 *  disconnect an account is owed the whole sentence. The act ends that one connection — every other
 *  account the workspace holds keeps its own — and a refusal stands in the dialog that asked for it
 *  rather than behind a closed one, so the member reads the answer where they pressed. */
function RemoveConnection({
  agentId,
  entry,
  label,
  onDone,
}: {
  agentId: string;
  entry: PoolConnection;
  label: string;
  onDone: (outcome: NoticeState) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<NoticeState>(QUIET);

  async function remove() {
    if (busy) return;
    setBusy(true);
    const outcome = await postIntent(agentId, {
      verb: "delete",
      kind: "connection",
      name: entry.grant,
    });
    setBusy(false);
    if (outcome.applied) onDone(outcomeNotice(outcome));
    else setNotice(outcomeNotice(outcome));
  }

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Remove {label}</DialogTitle>
        <DialogDescription>
          The {label} account {accountName(entry)} is disconnected from every app that reaches it.
          Every other connected account stays as it is, and you can connect this one again later.
        </DialogDescription>
      </DialogHeader>
      <OutcomeNotice state={notice} />
      <DialogFooter>
        <Button variant="send" size="bar" busy={busy} onClick={remove}>
          Remove account
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

function Row({
  name,
  label,
  detail,
  open,
  current,
  act,
}: {
  name: string;
  label: string;
  detail: ReactNode;
  open: ((aside: boolean) => void) | null;
  current: boolean;
  act: ReactNode;
}) {
  const control = open ? rowControl(() => open(false)) : null;
  const alongside = open ? rowControl(() => open(true)) : null;
  return (
    <Item
      {...control}
      aria-current={current || undefined}
      onClick={(event) => (beside(event) ? alongside : control)?.onClick?.(event)}
      onAuxClick={(event) => {
        if (!beside(event)) return;
        alongside?.onClick?.(event);
      }}
      variant={current ? "muted" : undefined}
      className={cn(control?.className, open && "hover:bg-fill")}
    >
      <MarkTile>
        <BrandMark provider={name} className="text-ink" />
      </MarkTile>
      <ItemContent>
        <ItemTitle>{label}</ItemTitle>
        <ItemDescription>{detail}</ItemDescription>
      </ItemContent>
      <ItemActions>{act}</ItemActions>
    </Item>
  );
}

function CoverageRecord({ legs, onClose }: { legs: Fact[]; onClose: () => void }) {
  return (
    <Sheet open title="GitHub" onClose={onClose}>
      <Facts rows={legs} />
    </Sheet>
  );
}

function ConnectionRecord({
  entry,
  viewer,
  attachTo,
  holders,
  onDone,
  onClose,
}: {
  entry: Connection;
  viewer: string | null;
  attachTo: Agent[];
  holders: { id: string; name: string }[];
  onDone: (notice: NoticeState) => void;
  onClose: () => void;
}) {
  const [targetAgent, setTargetAgent] = useState("");
  const holder = holders[0];
  const sources = usePanelRead<SourcesPayload>("/workspace/sources");

  async function act(lane: string, envelope: unknown) {
    onDone(outcomeNotice(await postIntent(lane, envelope)));
  }

  return (
    <Sheet open title={connectionName(entry)} onClose={onClose}>
      <Facts
        rows={connectionFacts(
          entry,
          viewer,
          sources.phase === "ready" ? sources.payload : null,
        )}
      />
      {entry.own ? (
        <>
          {attachTo.length ? (
            <Field label="App" htmlFor={ATTACH_AGENT}>
              <Select value={targetAgent} onValueChange={setTargetAgent}>
                <SelectTrigger id={ATTACH_AGENT}>
                  <SelectValue placeholder="Attach to app" />
                </SelectTrigger>
                <SelectContent>
                  {attachTo.map((agent) => (
                    <SelectItem key={agent.id} value={agent.id}>
                      {agentName(agent.name)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          ) : null}
          <div className="flex flex-wrap items-center gap-sm">
            {attachTo.length ? (
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
            ) : null}
            {holder ? (
              <Button
                variant="row"
                onClick={() =>
                  act(holder.id, {
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
                {entry.shared ? "Make private" : "Share with app"}
              </Button>
            ) : null}
            {holder ? (
              <ConfirmButton
                verb="Revoke"
                variant="row"
                onClick={async () => {
                  let last = QUIET;
                  for (const held of holders) {
                    last = outcomeNotice(
                      await postIntent(held.id, {
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
    </Sheet>
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
  const [standing, setStanding] = useState("");
  const record =
    state.phase === "ready"
      ? state.payload.connections.find((entry) => entry.grant === standing)
      : undefined;
  if (state.phase === "ready" && standing && !record) setStanding("");
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

  const connecting = adding ? (
    <Sheet open title="Add connector" onClose={close}>
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
    </Sheet>
  ) : null;

  const shown = record ? (
    <ConnectionRecord
      key={record.grant}
      entry={record}
      viewer={viewer}
      /* The bar over the table is where this screen attaches a connection, so the record does not
         say the same act a second time under another name. */
      attachTo={[]}
      holders={[{ id: agent.id, name: agent.name }]}
      onDone={(notice) => {
        if (notice.refused) setConsentUrl(null);
        setHandoff(notice);
        setReloads((count) => count + 1);
      }}
      onClose={() => setStanding("")}
    />
  ) : null;

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
            <Button
              variant="send"
              size="bar"
              onClick={() => {
                setStanding("");
                setAdding(true);
              }}
            >
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
              open={(entry) => () => {
                setAdding(false);
                setStanding(entry.grant);
              }}
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
      {connecting}
      {shown}
    </>
  );
}
