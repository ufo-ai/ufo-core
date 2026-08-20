import { IconCheck } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";

import {
  Notice,
  type NoticeState,
  OutcomeNotice,
  Panel,
  PanelEmpty,
  type PanelState,
  QUIET,
  Section,
  outcomeNotice,
  usePanelRead,
} from "@/kernel/panel";
import { BASE, postIntent } from "@/lib/api";
import { BrandMark } from "@/lib/brandMark";
import { cn } from "@/lib/cn";
import { ConsentLink, openConsentWindow } from "@/lib/consent";
import { useMainAgent } from "@/lib/mainAgent";
import { workspaceHash } from "@/lib/route";
import type { PoolPayload } from "@/views/Connectors";
import { CONNECT_VERB, FIRST_RUN_READ, WATCH_MS, type FirstRunPayload } from "@/views/FirstRun";

type Reads = { offered: FirstRunPayload; pool: PoolPayload };

/** The link left standing when the browser refused the consent window — the one case with nothing
 *  else to carry the member over. */
type Handoff = { url: string; text: string };

function joined(
  catalog: PanelState<FirstRunPayload>,
  pool: PanelState<PoolPayload>,
): PanelState<Reads> {
  if (catalog.phase === "failed") return catalog;
  if (pool.phase === "failed") return pool;
  if (catalog.phase === "loading" || pool.phase === "loading") return { phase: "loading" };
  return { phase: "ready", payload: { offered: catalog.payload, pool: pool.payload } };
}

/** Which tiles stand connected: a workspace install by its own flag, every other provider by a
 *  connection this member can see in the pool. An install's tile never reads the pool — the tile's
 *  press installs the workspace leg, and a member's broker account on the same provider does not
 *  fill what the workspace still lacks. */
function heldNames(reads: Reads): ReadonlySet<string> {
  const installs = new Set(reads.offered.connectors.map((row) => row.name));
  return new Set([
    ...reads.offered.connectors.filter((row) => row.installed).map((row) => row.name),
    ...reads.pool.connections
      .map((row) => row.provider)
      .filter((name) => !installs.has(name)),
  ]);
}

/** The catalog the first run offers, standing as its own page: one tile per tool, filled where the
 *  workspace or this member already holds it, and connecting on the press everywhere else. A
 *  workspace install dispatches its own admin-gated verb and the outcome carries the install link;
 *  every other tile opens the broker's per-member consent through the main agent, with the URL
 *  riding the turn's stream. Both open the consent window on the press itself, so one press is the
 *  whole act. While a press waits on the provider's pages, both reads re-read at the watch cadence
 *  and the tile fills the moment the account lands. */
export function Connect() {
  const agent = useMainAgent();
  const [waiting, setWaiting] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [watching, setWatching] = useState<string | null>(null);
  const [handoff, setHandoff] = useState<Handoff | null>(null);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const consent = useRef<Window | null>(null);
  const catalog = usePanelRead<FirstRunPayload>(FIRST_RUN_READ, 0, waiting ? WATCH_MS : undefined);
  const pool = usePanelRead<PoolPayload>("/connections", 0, waiting ? WATCH_MS : undefined);
  const state = joined(catalog, pool);
  const held = state.phase === "ready" ? heldNames(state.payload) : null;
  if (waiting && held?.has(waiting)) setWaiting(null);

  useEffect(() => {
    if (watching === null) return;
    const stream = new EventSource(BASE + "/turns/" + watching + "/stream");
    const done = () => stream.close();
    stream.addEventListener("connect", (event) => {
      const url = JSON.parse((event as MessageEvent).data).url;
      if (consent.current) consent.current.location.href = url;
      setHandoff(consent.current ? null : { url, text: "Open the provider consent page" });
      consent.current = null;
      setNotice(QUIET);
      done();
    });
    stream.addEventListener("connect_error", (event) => {
      consent.current?.close();
      consent.current = null;
      setNotice({ text: JSON.parse((event as MessageEvent).data).message, refused: true });
      setWaiting(null);
      done();
    });
    stream.addEventListener("terminal", done);
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

  if (!agent) return <PanelEmpty>This workspace has no app to connect accounts to yet.</PanelEmpty>;
  return (
    <Section>
      {handoff ? (
        <Notice>
          <ConsentLink url={handoff.url}>{handoff.text}</ConsentLink>
        </Notice>
      ) : null}
      <OutcomeNotice state={notice} />
      <Panel state={state}>
        {(reads) => (
          <div className="flex flex-wrap justify-center gap-lg">
            {reads.offered.providers.map((tile) => (
              <Tile
                key={tile.name}
                name={tile.name}
                label={tile.label}
                held={held?.has(tile.name) ?? false}
                disabled={busy !== null}
                onConnect={() => connect(tile.name, tile.label)}
              />
            ))}
          </div>
        )}
      </Panel>
      <p className="m-0 text-center text-label text-ink-soft">
        <a
          href={workspaceHash("connectors")}
          className="text-inherit no-underline hover:underline focus-visible:underline"
        >
          Manage connected accounts
        </a>
      </p>
    </Section>
  );
}

const TILE_CARD = cn(
  "relative flex aspect-square w-full items-center justify-center",
  "rounded-panel border border-edge bg-surface",
);

const TILE_SLOT = cn(
  "absolute top-xs right-xs flex size-(--size-glyph) items-center justify-center",
  "rounded-full border border-edge",
);

/** One tool of the catalog, drawn as the first run draws it: the product's own mark on a card and
 *  the name under it, with the slot in the corner filling when the account is held. A held tile is
 *  not an act — what it states is already true — and an open one connects on the press. */
function Tile({
  name,
  label,
  held,
  disabled,
  onConnect,
}: {
  name: string;
  label: string;
  held: boolean;
  disabled: boolean;
  onConnect: () => void;
}) {
  if (held) {
    return (
      <div className="flex w-(--size-app-tile) flex-col items-center gap-sm">
        <span className={cn(TILE_CARD, "border-ink")}>
          <BrandMark provider={name} />
          <span className={cn(TILE_SLOT, "border-ink bg-ink")}>
            <IconCheck
              role="img"
              aria-label={label + " connected"}
              className="size-icon text-surface"
            />
          </span>
        </span>
        <span className="text-label text-ink">{label}</span>
      </div>
    );
  }
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onConnect}
      className="group flex w-(--size-app-tile) flex-col items-center gap-sm border-0 bg-transparent p-0 text-inherit"
    >
      <span className={cn(TILE_CARD, "group-hover:border-edge-strong")}>
        <BrandMark provider={name} />
        <span className={TILE_SLOT} />
      </span>
      <span className="text-label text-ink-soft">{label}</span>
    </button>
  );
}
