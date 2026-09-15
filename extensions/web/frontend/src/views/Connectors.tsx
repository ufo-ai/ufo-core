import { IconBuilding, IconDots, IconKey, IconLock } from "@tabler/icons-react";
import { Fragment, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import { Button, ConfirmButton, buttonVariants } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Checkbox, Field, Input, Label, Search } from "@/components/ui/field";
import { Segmented, type Segment } from "@/components/ui/filter";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemHead,
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
import { usePageSearch } from "@/kernel/pane";
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
import { RowLines } from "@/kernel/rows";
import { rowControl } from "@/kernel/row";
import { closed, opened } from "@/kernel/slots";
import { workspaceHash } from "@/lib/route";
import { DataTable, OPEN } from "@/kernel/table";
import { Badge } from "@/components/ui/badge";
import { BrandMark } from "@/lib/brandMark";
import { cn } from "@/lib/cn";
import { ConsentLink, openConsentWindow } from "@/lib/consent";
import { Moment } from "@/lib/moments";
import { agentName } from "@/lib/agentName";
import { BASE, getJson, postAction, postIntent, postObjectAction, type Fetched } from "@/lib/api";
import { ownerLabel, useViewer } from "@/lib/audience";
import { ProviderGlyph } from "@/lib/providerGlyph";
import { connectArrival } from "@/lib/router";
import { useAgents, useMainAgent } from "@/lib/mainAgent";
import type { ActionView, Agent } from "@/lib/types";
import {
  FIRST_RUN_READ,
  WATCH_MS,
  type FirstRunPayload,
  type McpServerTile,
} from "@/lib/firstRun";
import { MCP_SERVERS_SLOT } from "@/views/CredentialPrompt";
import { ConnectAccount } from "@/views/ConnectAccount";
import { CONNECT_INSTALLS, WorkspaceChannels } from "@/views/Surfaces";

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

/** `searched` is what the broker's catalogue already returned for this term, passed whatever it
 *  reads as: that search matches a category too, which no spelling of a label here would keep. */
function rowMatches(row: ConnectionRow, query: string, searched: Set<string>): boolean {
  if (!query || searched.has(row.name)) return true;
  const said = [
    row.label,
    row.name,
    row.summary,
    row.entry ? accountHeld(row.entry) : "",
  ].join(" ");
  return said.toLowerCase().includes(query.toLowerCase());
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

type ConnectionRow = {
  key: string;
  name: string;
  label: string;
  summary: string;
  group: string;
  entry: PoolConnection | null;
  installed: boolean;
  /** Set on a named MCP server, which holds a token rather than a consent leg — so its row
   *  connects through the credential prompt and never through the `connect` verb. */
  mcp: McpServerTile | null;
};

type CredentialSlotView = { slot: string; entries: string[] };
type CredentialsPayload = { slots: CredentialSlotView[]; actions: ActionView[] };

/** The MCP servers this workspace has configured, by name — the same names `connectionRows` files
 *  a connected server's row under. */
function mcpHeld(state: PanelState<CredentialsPayload>): Set<string> {
  if (state.phase !== "ready") return new Set();
  const slot = state.payload.slots.find((row) => row.slot === MCP_SERVERS_SLOT);
  return new Set(slot?.entries ?? []);
}


/** Whose a row is, is the owner address against the viewer, never `own` — that flag is whether the
 *  viewer may manage the account, which a workspace admin may on every member's. */
function ownAccount(row: ConnectionRow, viewer: string | null): boolean {
  return row.entry !== null && row.entry.owner_email !== null && row.entry.owner_email === viewer;
}

/** The catalog's own order, which the rows already stand in, so a category chip stands where its
 *  first connector does. A row the catalog heads under nothing stands under All alone. */
function categories(rows: ConnectionRow[]): Segment[] {
  const counted = new Map<string, number>();
  for (const row of rows) {
    if (row.group) counted.set(row.group, (counted.get(row.group) ?? 0) + 1);
  }
  return [...counted].map(([group, count]) => ({ label: group, value: group, count }));
}

const SHELVES = ["available", "personal", "workspace"] as const;

type Shelf = (typeof SHELVES)[number];

const SHELF_LABELS: Record<Shelf, string> = {
  available: "Available",
  personal: "Personal",
  workspace: "Workspace",
};

/** The available shelf always draws its rows — the credential offer stands even where every connector
 *  is connected — so only the two held shelves can be empty. */
const SHELF_BLANKS: Record<Exclude<Shelf, "available">, string> = {
  personal: "You have connected no account.",
  workspace: "The workspace holds no account.",
};

/** Where a member lands: the shelf holding what they already have, and the catalogue only when they
 *  hold nothing. An address naming a shelf wins over both. */
function readShelf(chip: string | undefined, held: Record<Shelf, ConnectionRow[]>): Shelf {
  const named = SHELVES.find((name) => name === chip);
  if (named) return named;
  if (held.personal.length) return "personal";
  if (held.workspace.length) return "workspace";
  return "available";
}

function connectionRows(
  catalog: FirstRunPayload,
  pool: PoolPayload,
  viewer: string | null,
  mcp: Set<string>,
): ConnectionRow[] {
  const tiles = new Map(catalog.providers.map((tile) => [tile.name, tile]));
  const held = pool.connections.map((entry) => ({
    key: entry.grant,
    name: entry.provider,
    label: tiles.get(entry.provider)?.label ?? entry.provider,
    summary: tiles.get(entry.provider)?.summary ?? "",
    group: tiles.get(entry.provider)?.group ?? "",
    entry,
    installed: false,
    mcp: null,
  }));
  const servers = catalog.mcp_servers.map((tile) => ({
    key: "mcp:" + tile.name,
    name: tile.name,
    label: tile.label,
    summary: tile.summary,
    group: tile.group,
    entry: null,
    // A server's token is a workspace credential, so a configured one belongs to the workspace and
    // wears that badge — no member owns it and none of them can be the owner a personal row needs.
    installed: mcp.has(tile.name),
    mcp: tile,
  }));
  const installs = catalog.connectors
    .filter((row) => row.installed)
    .map((row) => ({
      key: "install:" + row.name,
      name: row.name,
      label: row.label,
      summary: tiles.get(row.name)?.summary ?? "",
      group: tiles.get(row.name)?.group ?? "",
      entry: null,
      installed: true,
      mcp: null,
    }));
  const connected = new Set(
    pool.connections
      .filter((entry) => entry.owner_email !== null && entry.owner_email === viewer)
      .map((entry) => entry.provider),
  );
  const offered = catalog.providers
    .filter((tile) => {
      const installed = catalog.connectors.find((row) => row.name === tile.name)?.installed;
      return installed === undefined ? !connected.has(tile.name) : !installed;
    })
    .map((tile) => ({
      key: "offer:" + tile.name,
      name: tile.name,
      label: tile.label,
      summary: tile.summary,
      group: tile.group,
      entry: null,
      installed: false,
      mcp: null,
    }));
  return [
    ...held.filter((row) => ownAccount(row, viewer)),
    ...held.filter((row) => !ownAccount(row, viewer)),
    ...installs,
    ...servers.filter((row) => row.installed),
    ...offered,
    ...servers.filter((row) => !row.installed),
  ];
}

/** A member hunting the shelf for a service ufo brokers no connector for finds the key path here,
 *  rather than learning that credentials live under settings by not finding them. */
function CredentialOffer() {
  return (
    <Item>
      <MarkTile>
        <IconKey className="size-(--size-brand-mark)" aria-hidden />
      </MarkTile>
      <ItemContent>
        <ItemTitle>Credential</ItemTitle>
        <ItemDescription>A key or token for a service with no connector.</ItemDescription>
      </ItemContent>
      <ItemActions>
        <a
          href={workspaceHash("credentials")}
          className={cn(buttonVariants({ variant: "outline", size: "bar" }))}
        >
          Add credential
        </a>
      </ItemActions>
    </Item>
  );
}

/** The other half of what a search that matched nothing can still offer: a server this deploy names
 *  no row for is reached by its own address, which the credentials screen takes. */
function McpOffer() {
  return (
    <Item>
      <MarkTile>
        <BrandMark provider="mcp" className="text-ink" />
      </MarkTile>
      <ItemContent>
        <ItemTitle>MCP server</ItemTitle>
        <ItemDescription>An address and token for a server with no row here.</ItemDescription>
      </ItemContent>
      <ItemActions>
        <a
          href={workspaceHash("credentials")}
          className={cn(buttonVariants({ variant: "outline", size: "bar" }))}
        >
          Add MCP server
        </a>
      </ItemActions>
    </Item>
  );
}

/** The endpoint is the row's, so only the token is asked for; the value written is the same
 *  `{name, url, auth}` the slot's merge takes from the credentials screen's own form. */
function ConnectMcpServer({
  tile,
  agentId,
  actions,
  onDone,
  onClose,
}: {
  tile: McpServerTile;
  agentId: string;
  actions: ActionView[];
  onDone: (notice: NoticeState) => void;
  onClose: () => void;
}) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const request = actions.find((view) => view.name === "request_credentials");

  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy || !token.trim() || !request) return;
    setBusy(true);
    const outcome = await postAction(agentId, request.call, {
      reason: "Sent to " + tile.url + " and nowhere else. Stored encrypted and never shown again.",
      prompts: [{ slot: MCP_SERVERS_SLOT, prompt: tile.token }],
    });
    const sealed = outcome.credentials?.sealed;
    if (!sealed) {
      setBusy(false);
      setNotice(outcomeNotice(outcome));
      return;
    }
    let res: Response;
    try {
      res = await fetch(BASE + "/credentials", {
        method: "POST",
        body: new URLSearchParams({
          sealed,
          slot: MCP_SERVERS_SLOT,
          value: JSON.stringify({ name: tile.name, url: tile.url, auth: token.trim() }),
        }),
        credentials: "same-origin",
      });
    } catch {
      setBusy(false);
      setNotice({ text: "Network error — try again.", refused: true });
      return;
    }
    setBusy(false);
    if (!res.ok) {
      setNotice({ text: (await res.text().catch(() => "")) || "The token was not stored.", refused: true });
      return;
    }
    onDone({ text: tile.label + " connected.", refused: false });
  }

  return (
    <Sheet open title={"Connect " + tile.label} onClose={onClose}>
      <OutcomeNotice state={notice} />
      <form onSubmit={save} className="flex flex-col gap-2xl">
        <Facts rows={[{ label: "Server", value: tile.url }]} />
        <Field
          label={tile.token}
          htmlFor="mcp-token"
          description="Stored encrypted and never shown again."
        >
          <Input
            id="mcp-token"
            type="password"
            autoComplete="off"
            autoFocus
            required
            value={token}
            onChange={(event) => setToken(event.target.value)}
          />
        </Field>
        <div className="flex justify-end">
          <Button type="submit" variant="send" size="bar" busy={busy} disabled={!request}>
            Connect
          </Button>
        </div>
      </form>
    </Sheet>
  );
}

function ConnectionRowItem({
  row,
  open,
  current,
  act,
}: {
  row: ConnectionRow;
  open: (() => void) | null;
  current: boolean;
  act: ReactNode;
}) {
  const control = open ? rowControl(open) : null;
  return (
    <Item
      {...control}
      aria-current={current || undefined}
      variant={current ? "muted" : undefined}
      className={cn(control?.className, open && "hover:bg-fill")}
    >
      <MarkTile>
        <BrandMark provider={row.name} className="text-ink" />
      </MarkTile>
      <ItemContent>
        <ItemTitle>
          <span className="flex items-center gap-sm">
            {row.label}
            {row.entry && !row.entry.shared ? (
              <Badge className="gap-2xs">
                <IconLock className="size-(--size-glyph)" aria-hidden />
                Private
              </Badge>
            ) : null}
            {row.installed || row.entry?.shared ? (
              <Badge className="gap-2xs">
                <IconBuilding className="size-(--size-glyph)" aria-hidden />
                Workspace
              </Badge>
            ) : null}
          </span>
        </ItemTitle>
        <ItemDescription>{said(row)}</ItemDescription>
      </ItemContent>
      <ItemActions>{act}</ItemActions>
    </Item>
  );
}

/** What a row says under its name: which account it stands on and who reaches it, or — where the row
 *  is still an offer — what connecting it would give the app. */
function said(row: ConnectionRow): string {
  return row.entry ? accountName(row.entry) : row.summary;
}





const COVERAGE = "github-coverage";

const CONNECTION = "connection/";

const GITHUB = "github";

type Handoff = { url: string; text: string };
const CONNECTOR_BATCH_MIN = 25;
const CONNECTOR_BATCH_CURSOR_LIMIT = 25;
const SEARCH_REST_MS = 200;

/** What the member has stopped typing: the catalogue search reaches the broker, so the keystrokes
 *  of one word are one search. The rows already on the shelf narrow on every keystroke. */
function useRested(said: string): string {
  const [rested, setRested] = useState(said);
  useEffect(() => {
    if (said === rested) return;
    const timer = window.setTimeout(() => setRested(said), SEARCH_REST_MS);
    return () => window.clearTimeout(timer);
  }, [said, rested]);
  return rested;
}

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
    setState((held) => (held.phase === "ready" ? held : { phase: "loading" }));
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
  const opens = place.opens ?? [];
  const [reloads, setReloads] = useState(0);
  const [waiting, setWaiting] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [watching, setWatching] = useState<string | null>(null);
  const [handoff, setHandoff] = useState<Handoff | null>(null);
  const [removing, setRemoving] = useState<ConnectionRow | null>(null);
  const [connecting, setConnecting] = useState<McpServerTile | null>(null);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [toast, setToast] = useState<ToastState>(arrivedToast);
  const consent = useRef<Window | null>(null);
  const viewer = useViewer();
  const agent = useMainAgent();
  const agents = useAgents();
  const box = usePageSearch();
  const catalog = usePanelRead<FirstRunPayload>(
    FIRST_RUN_READ,
    reloads,
    waiting ? WATCH_MS : undefined,
  );
  const pool = usePanelRead<PoolPayload>("/connections", reloads, waiting ? WATCH_MS : undefined);
  const held = place.q ?? "";
  const category = place.kind ?? "";
  /** The field holds what is typed and the address what was rested on, so the rows narrow on every
   *  keystroke while the address — and the link a member shares — takes one entry per word. */
  const [query, setQuery] = useState(held);
  useEffect(() => setQuery(held), [held]);
  const rested = useRested(query);
  useEffect(() => {
    if (rested !== (place.q ?? "")) onPlace({ q: rested || undefined });
  }, [rested]);
  const expanded = useConnectorCatalog(rested, reloads);
  // The credential slots carry both halves a named MCP row needs: which servers this workspace
  // already holds, and the action that seals the prompt its token is typed into.
  const credentials = usePanelRead<CredentialsPayload>("/workspace/credentials", reloads);
  const coverage = usePanelRead<GithubCoverage>("/github/coverage", reloads);
  const state = joined(catalog, pool, expanded.state);
  const searched = new Set(
    expanded.state.phase === "ready" ? expanded.state.payload.providers.map((row) => row.name) : [],
  );
  const landed =
    state.phase === "ready" &&
    (state.payload.pool.connections.some((entry) => entry.provider === waiting) ||
      state.payload.catalog.connectors.some((row) => row.name === waiting && row.installed));
  if (waiting && landed) setWaiting(null);
  const pooled = pool.phase === "ready" ? pool.payload.connections : [];

  function show(id: string) {
    onPlace({ opens: opened(opens, id) });
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


  return (
    <>
      {handoff ? (
        <Notice>
          <ConsentLink url={handoff.url}>{handoff.text}</ConsentLink>
        </Notice>
      ) : null}
      <OutcomeNotice state={notice} />
      <OutcomeNotice state={expanded.error} />
      <Panel state={state}>
        {(reads) => {
          const rows = connectionRows(reads.catalog, reads.pool, viewer, mcpHeld(credentials));
          const found = rows.filter((row) => rowMatches(row, query, searched));
          const shelves: Record<Shelf, ConnectionRow[]> = {
            available: found.filter((row) => !row.entry && !row.installed),
            personal: found.filter((row) => row.entry !== null && ownAccount(row, viewer)),
            workspace: found.filter(
              (row) => row.installed || (row.entry !== null && !ownAccount(row, viewer)),
            ),
          };
          const shelf = readShelf(place.chip, shelves);
          const shelved = shelves[shelf];
          const groups = categories(shelved);
          const picked = groups.some((one) => one.value === category) ? category : "";
          const standing = picked ? shelved.filter((row) => row.group === picked) : shelved;
          const narrowed = Boolean(query) || Boolean(picked);
          const more = shelf === "available" && expanded.state.phase === "ready" && expanded.state.payload.after;
          return (
            <div className="@container flex flex-col gap-6xl">
              <Section title="Reach ufo from wherever you already work">
                <WorkspaceChannels opens={opens} onOpen={show} onClose={shut} />
              </Section>
              <Section title="Coding providers">
                <ConnectAccount />
              </Section>
              <Section
                title="Integrations"
                action={
                  /* Beside the shelf it narrows rather than over the page: the two sections above
                     hold no rows a search could filter. */
                  <div className="flex items-center gap-sm max-narrow:flex-wrap">
                    {box}
                    <Segmented
                      label="Integrations"
                      segments={SHELVES.map((name) => ({
                        label: SHELF_LABELS[name],
                        value: name,
                      }))}
                      value={shelf}
                      onPick={(next: string) => onPlace({ chip: next })}
                    />
                  </div>
                }
              >
                <div className="flex flex-col gap-2xl">
                  {standing.length || narrowed || shelf === "available" ? (
                    <ItemGroup>
                      <ItemHead>
                        <Search
                          label="Search connectors"
                          placeholder="Search"
                          className="w-full"
                          value={query}
                          onChange={(event) => setQuery(event.target.value)}
                        />
                        {groups.length > 1 ? (
                          <Segmented
                            variant="tag"
                            label="Category"
                            segments={[{ label: "All", value: "", count: shelved.length }, ...groups]}
                            value={picked}
                            onPick={(next: string) => onPlace({ kind: next || undefined })}
                          />
                        ) : null}
                      </ItemHead>
                      {standing.length ? null : (
                        <>
                          <ItemSeparator />
                          <Item>
                            <ItemContent>
                              <ItemDescription whole>No connector matches this search.</ItemDescription>
                            </ItemContent>
                          </Item>
                        </>
                      )}
                      {standing.map((row) => (
                        <Fragment key={row.key}>
                          <ItemSeparator />
                          <ConnectionRowItem
                            row={row}
                            current={
                              row.entry
                                ? opens.includes(CONNECTION + row.entry.grant)
                                : row.name === GITHUB && opens.includes(COVERAGE)
                            }
                            open={
                              row.entry
                                ? () => show(CONNECTION + row.entry!.grant)
                                : row.name === GITHUB
                                  ? () => show(COVERAGE)
                                  : null
                            }
                            act={
                              row.entry || row.installed ? (
                                row.entry?.own ? (
                                  <DropdownMenu modal={false}>
                                    <DropdownMenuTrigger asChild>
                                      <Button
                                        variant="quiet"
                                        size="icon"
                                        aria-label={"Menu for " + row.label}
                                      >
                                        <IconDots aria-hidden />
                                      </Button>
                                    </DropdownMenuTrigger>
                                    <DropdownMenuContent
                                      align="end"
                                      className="w-(--container-menu)"
                                    >
                                      <DropdownMenuItem
                                        onSelect={() => setRemoving(row)}
                                      >
                                        {"Remove " + row.label}
                                      </DropdownMenuItem>
                                    </DropdownMenuContent>
                                  </DropdownMenu>
                                ) : null
                              ) : (
                                <Button
                                  variant="outline"
                                  size="bar"
                                  busy={waiting === row.name}
                                  disabled={busy !== null && waiting !== row.name}
                                  onClick={() =>
                                    row.mcp
                                      ? setConnecting(row.mcp)
                                      : connect(row.name, row.label)
                                  }
                                >
                                  {waiting === row.name ? "Connecting" : "Connect"}
                                </Button>
                              )
                            }
                          />
                        </Fragment>
                      ))}
                      {shelf === "available" ? (
                        <>
                          <ItemSeparator />
                          <CredentialOffer />
                          {/* A search that matched nothing is the one moment the open-ended path is
                              what the member wants, so it stands under the shelf's own offer. */}
                          {standing.length ? null : (
                            <>
                              <ItemSeparator />
                              <McpOffer />
                            </>
                          )}
                        </>
                      ) : null}
                    </ItemGroup>
                  ) : (
                    <PanelBlank body={SHELF_BLANKS[shelf as Exclude<Shelf, "available">]} />
                  )}
                  {more ? (
                    <div>
                      <Button variant="row" busy={expanded.loadingMore} onClick={expanded.loadMore}>
                        Load more
                      </Button>
                    </div>
                  ) : null}
                </div>
              </Section>
            </div>
          );
        }}
      </Panel>
      {sheet}
      {connecting && agent ? (
        <ConnectMcpServer
          tile={connecting}
          agentId={agent.id}
          actions={credentials.phase === "ready" ? credentials.payload.actions : []}
          onDone={(outcome) => {
            setConnecting(null);
            setNotice(outcome);
            setReloads((count) => count + 1);
          }}
          onClose={() => setConnecting(null)}
        />
      ) : null}
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
