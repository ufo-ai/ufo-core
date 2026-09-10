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
import { RowLines } from "@/kernel/rows";
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
import { FIRST_RUN_READ, WATCH_MS, type FirstRunPayload } from "@/lib/firstRun";
import { CONNECT_INSTALLS } from "@/views/Surfaces";

type Stream = {
  id: string;
  connection_id: string;
  backend: string;
  stream: string | null;
  consecutive_errors: number;
  next_sync_at: string;
  parked_reason: string | null;
};

type SourcesPayload = { sources: Stream[] };

type Connection = {
  id: string;
  provider: string;
  account_id: string;
  account_label: string | null;
  owner_email: string | null;
  own: boolean;
  shared: boolean;
  base_url: string | null;
  backfill_days: number | null;
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
const TENANT_URL = "connection-base-url";
const BACKFILL_DAYS = "connection-backfill-days";
export const CONNECT_REFUSED =
  "No connection request was opened. Ask in chat to connect the account.";

/** A workspace connection has no account and carries the empty string as its `account_id`, which is
 *  why every fall through here is `||` and not `??`. */
function accountHeld(entry: Connection): string {
  return entry.account_label || entry.owner_email || entry.account_id;
}

function accountName(entry: Connection): string {
  return accountHeld(entry) || entry.provider;
}

function dayCount(days: number): string {
  return days === 1 ? "1 day" : days + " days";
}

function syncFacts(entry: Connection): Fact[] {
  return [
    { label: "Tenant URL", value: entry.base_url || "The provider's own host" },
    {
      label: "Backfill",
      value:
        entry.backfill_days === null ? "Each stream's own window" : dayCount(entry.backfill_days),
    },
  ];
}

function streamMeta(one: Stream): ReactNode[] {
  return [
    one.consecutive_errors === 0
      ? null
      : one.consecutive_errors === 1
        ? "1 error"
        : one.consecutive_errors + " errors",
    one.parked_reason,
  ];
}

/** The kind forbids an unknown field and takes every known one at its declared default, so a spec
 *  naming less than the whole record is an edit that also unshares the account and forgets its tenant. */
function connectionSpec(
  entry: Connection,
  change: { shared?: boolean; base_url?: string; backfill_days?: number | null },
) {
  return {
    verb: "apply",
    kind: "connection",
    name: entry.grant,
    spec: {
      provider: entry.provider,
      account_id: entry.account_id,
      shared: entry.shared,
      base_url: entry.base_url ?? "",
      backfill_days: entry.backfill_days,
      ...change,
    },
  };
}

function connectionFacts(
  entry: Connection,
  viewer: string | null,
  holders: { id: string; name: string }[],
): Fact[] {
  return [
    { label: "Access", value: entry.shared ? "Workspace" : "Only you" },
    { label: "Owner", value: ownerLabel(entry.owner_email, viewer) },
    { label: "Connected", value: <Moment at={entry.connected_at} /> },
    {
      label: holders.length === 1 ? "App" : "Apps",
      value: holders.length ? holders.map((one) => agentName(one.name)).join(", ") : "No apps",
    },
  ];
}

function matches(entry: Connection | PoolConnection, query: string): boolean {
  const said = [
    entry.provider,
    entry.account_label ?? "",
    entry.account_id,
    entry.owner_email ?? "",
  ].join(" ");
  return said.toLowerCase().includes(query.toLowerCase());
}

function accountLine(entry: Connection): string {
  const access = entry.shared ? "Workspace" : "Only you";
  const named = accountHeld(entry);
  return named ? named + " \u00b7 " + access : access;
}

type Standing = {
  key: string;
  name: string;
  label: string;
  detail: ReactNode;
  said: string;
  group: string;
  entry: PoolConnection | null;
};

type Offer = { name: string; label: string; summary: string; group: string };

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
        entry.account_id,
        entry.owner_email ?? "",
        ...entry.agents.map((agent) => agent.name),
      ].join(" "),
      group: tiles.get(entry.provider)?.group ?? "",
      entry,
    })),
  ];
}

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

/** Whose a row is, is the owner address against the viewer, never `own` — that flag is whether the
 *  viewer may manage the account, which a workspace admin may on every member's. */
function ownHeld(row: Standing, viewer: string | null): boolean {
  return row.entry !== null && row.entry.owner_email !== null && row.entry.owner_email === viewer;
}

const HELD_ZONES: [string, string, (row: Standing, viewer: string | null) => boolean][] = [
  ["Your connections", "Accounts you connected, and the apps that can use them.", ownHeld],
  [
    "Shared with the workspace",
    "Accounts the workspace holds, and the tools it installed.",
    (row, viewer) => !ownHeld(row, viewer),
  ],
];

function grouped(rows: Offer[]): [string, Offer[]][] {
  const groups: [string, Offer[]][] = [];
  for (const row of rows) {
    const last = groups.at(-1);
    if (last && last[0] === row.group) last[1].push(row);
    else groups.push([row.group, [row]]);
  }
  return groups;
}

function categories(catalog: FirstRunPayload): string[] {
  return [...new Set(catalog.providers.map((tile) => tile.group))];
}

/** Radix names an option by a value, and an empty one is how it says nothing is selected. */
const EVERY_CATEGORY = "all";

const COVERAGE = "github-coverage";

const CONNECTION = "connection/";

const GITHUB = "github";

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

function arrivedToast(): ToastState {
  const named = connectArrival();
  return named ? { title: named + " connected." } : SILENT;
}

export function Connectors({
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
    // Opened on the press, before the round trip that mints the link: a window opened afterwards has lost
    // the gesture the browser opens one for.
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
        lane={agent?.id ?? null}
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
  const streams = usePanelRead<SourcesPayload>("/workspace/sources");
  const synced =
    streams.phase === "ready" &&
    streams.payload.sources.some((one) => one.connection_id === entry.id);

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
      {synced ? (
        <Notice tone="attention">
          Removing this account deletes its streams and everything they synced.
        </Notice>
      ) : null}
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

/** A workspace connection is owned by nobody and shared by construction, so it draws no make-private
 *  control: the act would reach a connection with no owner to hand it back to and be refused. */
function ConnectionRecord({
  entry,
  viewer,
  lane,
  attachTo,
  holders,
  onDone,
  onClose,
}: {
  entry: Connection;
  viewer: string | null;
  lane: string | null;
  attachTo: Agent[];
  holders: { id: string; name: string }[];
  onDone: (notice: NoticeState) => void;
  onClose: () => void;
}) {
  const [targetAgent, setTargetAgent] = useState("");
  const holder = holders[0];
  const streams = usePanelRead<SourcesPayload>("/workspace/sources");
  // Sharing an account is the owner's alone; making it private is theirs or an admin's, which is what
  // `own` answers. A row that offered an admin the share would be refused at the gate.
  const mine = entry.owner_email !== null && entry.owner_email === viewer;

  async function act(agentId: string, envelope: unknown) {
    onDone(outcomeNotice(await postIntent(agentId, envelope)));
  }

  return (
    <Sheet open title={accountName(entry)} onClose={onClose}>
      <Section title="Account">
        <Facts rows={connectionFacts(entry, viewer, holders)} />
      </Section>
      <Section title="Sync">
        {entry.own && lane ? (
          <SyncForm entry={entry} lane={lane} onDone={onDone} />
        ) : (
          <Facts rows={syncFacts(entry)} />
        )}
      </Section>
      <Section title="Streams">
        <Panel state={streams}>
          {(payload) => {
            const rows = payload.sources.filter((one) => one.connection_id === entry.id);
            if (!rows.length) return <PanelBlank body="No stream syncs this account yet." />;
            return (
              <RowLines
                rows={rows}
                rowKey={(one) => one.id}
                primary={(one) => one.stream ?? one.backend}
                meta={streamMeta}
                when={(one) => <Moment at={one.next_sync_at} />}
              />
            );
          }}
        </Panel>
      </Section>
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
                    spec: { provider: entry.provider, account_id: entry.account_id },
                  })
                }
              >
                Attach to app
              </Button>
            ) : null}
            {lane && (entry.shared ? entry.owner_email !== null : mine) ? (
              <Button
                variant="row"
                onClick={() => act(lane, connectionSpec(entry, { shared: !entry.shared }))}
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

function SyncForm({
  entry,
  lane,
  onDone,
}: {
  entry: Connection;
  lane: string;
  onDone: (notice: NoticeState) => void;
}) {
  const [baseUrl, setBaseUrl] = useState(entry.base_url ?? "");
  const [days, setDays] = useState(entry.backfill_days === null ? "" : String(entry.backfill_days));
  const [busy, setBusy] = useState(false);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    const outcome = await postIntent(
      lane,
      connectionSpec(entry, {
        base_url: baseUrl.trim(),
        backfill_days: days.trim() === "" ? null : Number(days),
      }),
    );
    setBusy(false);
    onDone(outcomeNotice(outcome));
  }

  return (
    <form onSubmit={save} className="flex flex-col gap-2xl">
      <Field
        label="Tenant URL"
        htmlFor={TENANT_URL}
        description="The provider's API host for this account. Empty dials the provider's own."
      >
        <Input
          id={TENANT_URL}
          type="url"
          aria-describedby={TENANT_URL + "-description"}
          placeholder="https://acme.example.com"
          value={baseUrl}
          onChange={(event) => setBaseUrl(event.target.value)}
        />
      </Field>
      <Field
        label="Backfill days"
        htmlFor={BACKFILL_DAYS}
        description="How far back a stream's first sync reads. Empty takes each stream's own window."
      >
        <Input
          id={BACKFILL_DAYS}
          type="number"
          min={1}
          aria-describedby={BACKFILL_DAYS + "-description"}
          value={days}
          onChange={(event) => setDays(event.target.value)}
        />
      </Field>
      <div className="flex justify-end">
        <Button type="submit" variant="send" size="bar" busy={busy}>
          Save
        </Button>
      </div>
    </form>
  );
}

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
  const consent = useRef<Window | null>(null);

  useEffect(() => {
    if (watching === null) return;
    const stream = new EventSource(BASE + "/turns/" + watching + "/stream");
    source.current = stream;
    const done = () => {
      stream.close();
      source.current = null;
    };
    let asked = false;
    stream.addEventListener("connect", () => {
      asked = true;
      const url = BASE + "/turns/" + watching + "/connect";
      if (consent.current) consent.current.location.href = url;
      setConsentUrl(consent.current ? null : url);
      consent.current = null;
      setHandoff(QUIET);
      done();
    });
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
      lane={agent.id}
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
                      spec: { provider: attachEntry.provider, account_id: attachEntry.account_id },
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
