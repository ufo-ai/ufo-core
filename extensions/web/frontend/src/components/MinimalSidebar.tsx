import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import {
  IconCirclePlus,
  IconPlus,
  IconSettings,
  IconVolume,
  IconVolumeOff,
} from "@tabler/icons-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { statusDot, useAppStatus } from "@/lib/appStatusStore";
import { AgentIcon } from "@/lib/agentIcon";
import { cn } from "@/lib/cn";
import { agentName } from "@/lib/agentName";
import { openHome, placeWorkspace } from "@/lib/router";
import { setMuted, useMuted } from "@/lib/sound";
import { useOfferedTabs } from "@/lib/surfaces";
import type { ChatRow } from "@/lib/rail";
import { Spotlight } from "@/views/Spotlight";
import type { Agent } from "@/lib/types";
import { GLYPH_STROKE } from "@/lib/glyph";

const HOME = "Home";
const LAUNCHER = "Launcher";
const NEW_APP = "New app";
const WORKSPACE = "Workspace";
const MUTE = "Mute sounds";
const UNMUTE = "Unmute sounds";

/** One mark's box on the rail. The column is the glyph's own width, so the mark is the whole of the
 *  control and the pointer answer is the ink it takes rather than a ground behind it. */
const TILE =
  "flex size-(--size-glyph) shrink-0 items-center justify-center rounded-control border-0 bg-transparent p-0 text-ink-soft hover:text-ink";

/** The mark saying which tab the member is standing in. It is a single box slid up and down the
 *  column rather than a fill switched on under one tile and off under another: the member's eye
 *  follows a box that travels, and two fills crossing over each other is a flicker they have to read
 *  twice. It takes the stronger fill because it is read against the rail's own ground rather than
 *  against the page, and the lighter one is barely a box there. It is measured off the tile it lands
 *  under rather than counted from that tile's place, because the list scrolls and a row's height is
 *  the tile's to state, not this one's to assume. */
const MARK =
  "pointer-events-none absolute top-0 left-1/2 size-(--size-lede) rounded-control bg-fill-strong " +
  "transition-[transform,opacity] duration-200 ease-control";

/** How long the mark stands before it fades. It answers a question the member asked by walking the
 *  row — which of these am I in — and a permanent answer to a question nobody is still asking is one
 *  more thing on a rail whose whole job is to stay out of the way. Walking again brings it back.
 *  The test that holds the fade reads the span from here, so what it waits cannot drift from what
 *  the rail holds. */
export const MARK_HELD = 5000;

/** Where the mark sits: its own centre carried to the tile's, which is why the box's own height is
 *  subtracted here rather than the tile's assumed. */
const SLID = (mark: number | null) => ({
  transform: "translate(-50%, calc(" + String(mark ?? 0) + "px - 50%))",
});

/** The rail states its destinations as marks alone, so every one of them is named here. The word
 *  stands to the right because the rail opens the shell on the left and has no room beside it. */
function RailTip({ label, children }: { label: string; children: ReactElement }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}

/** The column opening the shell on the left: the mark, the act that opens a tab, one tile per tab
 *  home stands, the act that builds an app, and the workspace at its foot.
 *
 *  The tiles are the member's own row of open tabs, not the workspace's roster of apps: they stand
 *  in the order home draws them, each named by the app filling it, and an app held by two tabs
 *  stands twice — the lane is what a tile means, so the lane is what keys it. A press lands home and
 *  brings that lane into view, leaving the row in the order the member arranged it; it never opens an
 *  app the member has not opened.
 *
 *  The tiles scroll and the two acts under them are reclaimed: a tab list that grew past the rail
 *  would push the way to a new tab, to an app, and to the workspace past the bottom edge.
 *
 *  The act that opens a tab always opens one. What the row does to make room for it is home's own
 *  rule, said where the track is written, so the mark here means one thing at every length of
 *  row. */
export function MinimalSidebar({
  lanes,
  active,
  agents,
  chats,
  pinned,
  account,
  onEnterLane,
  onLane,
  onBuild,
}: {
  lanes: { lane: string; agent: Agent }[];
  /** The lane home says the member is standing in, which this column marks. A lane the rail does
   *  not list — the picker's — leaves the mark off, since there is no tile for it to stand under. */
  active: string | undefined;
  agents: Agent[];
  /** The member's conversations and their pinned apps, which the palette lists. The rail draws
   *  neither: it carries the search, so it carries what the search opens on. */
  chats: ChatRow[];
  pinned: string[];
  /** Stand home on `opens` with `lane` brought into view as it lands — what the launcher's rows
   *  run. The seek and the address both belong to the shell, so the rail hands the panel the same
   *  act its own tiles are pressed with. */
  onEnterLane: (lane: string, opens: string[]) => void;
  /** The member's own menu — identity, theme, administration, sign out — drawn at the rail's foot,
   *  because at a desk width this rail is the whole shell and the way out has to stand on it. */
  account: ReactNode;
  onLane: (lane: string) => void;
  onBuild: () => void;
}) {
  const { statuses } = useAppStatus();
  const tabs = useOfferedTabs();
  const muted = useMuted();
  const list = useRef<HTMLUListElement>(null);
  const [mark, setMark] = useState<number | null>(null);
  const [held, setHeld] = useState(false);
  /* Where the mark stands, re-read whenever the tiles or the lane they mark change: a tab opened
     above the marked one moves it down the column without the member having gone anywhere. */
  useLayoutEffect(() => {
    const tile = list.current?.querySelector<HTMLElement>("[data-active]") ?? null;
    setMark(tile === null ? null : tile.offsetTop + tile.offsetHeight / 2);
  }, [active, lanes]);
  /* Whether it is lit, which only the member going somewhere changes. The tiles are rebuilt on
     every conversation spoken in, and a mark that relit itself on each of those would be a rail
     blinking at a member who has not moved. */
  useEffect(() => {
    if (active === undefined) {
      setHeld(false);
      return;
    }
    setHeld(true);
    list.current?.querySelector<HTMLElement>("[data-active]")?.scrollIntoView({ block: "nearest" });
    const fades = setTimeout(() => setHeld(false), MARK_HELD);
    return () => clearTimeout(fades);
  }, [active]);
  return (
    <nav
      aria-label="Tabs"
      className="flex min-h-0 flex-col items-center gap-2xl bg-sidebar px-lg py-2xl"
    >
      <RailTip label={HOME}>
        <button type="button" aria-label={HOME} onClick={openHome} className={cn(TILE, "text-ink")}>
          <svg width={12.5} height={11} viewBox="0 0 12.5 11" fill="currentColor" aria-hidden>
            <circle cx={2} cy={2} r={2} />
            <circle cx={10.5} cy={2} r={2} />
            <circle cx={6.25} cy={9} r={2} />
          </svg>
        </button>
      </RailTip>
      {/* The one act the rail leads with, over the tabs it adds to, in the filled box the picker
          lane it opens wears: the launcher, which holds every app and thread the tabs could open
          and the chord that opens it from anywhere. */}
      <RailTip label={LAUNCHER}>
        <Spotlight
          agents={agents}
          chats={chats}
          pinned={pinned}
          onEnterLane={onEnterLane}
          glyph={IconPlus}
          title={LAUNCHER}
          className={cn(TILE, "size-(--size-lede) bg-fill")}
        />
      </RailTip>
      {/* The tiles scroll, and a scrolling box clips both axes — so the track carries a gutter the
          width of the dot's overhang, and the mark's own column stays where it stood. Without it the
          rail is 16px wide inside its padding and the dot loses its outer half at every width. */}
      <ul
        ref={list}
        className="scrollbar-none relative -mx-2xs -my-2xs flex min-h-0 flex-1 list-none flex-col items-center gap-2xl self-stretch overflow-y-auto px-2xs py-2xs"
      >
        <li
          aria-hidden
          className={cn(MARK, (!held || mark === null) && "opacity-0")}
          style={SLID(mark)}
        />
        {lanes.map(({ lane, agent }) => {
          const dot = statusDot(statuses[agent.id], agent.setup_due === true);
          const here = lane === active;
          return (
            <li key={lane} data-active={here ? "" : undefined}>
              <RailTip label={agentName(agent.name)}>
                <button
                  type="button"
                  aria-label={agentName(agent.name)}
                  aria-current={here ? true : undefined}
                  onClick={() => onLane(lane)}
                  className={cn(TILE, here && "text-ink")}
                >
                  {/* The dot the wide column's rows wear, and nothing else of theirs: the rail has
                      no line for what an app is doing, so what it is doing stays off it — the dot
                      alone says working or waiting on the member, and it scales away rather than
                      vanishing so a change of state is a mark turning. */}
                  <span className="relative shrink-0">
                    <AgentIcon name={agent.icon} />
                    <span
                      aria-hidden
                      className={cn(
                        "absolute -right-2xs -bottom-2xs size-sm rounded-full transition duration-200 ease-control",
                        dot ?? "scale-0",
                      )}
                    />
                  </span>
                </button>
              </RailTip>
            </li>
          );
        })}
      </ul>
      <RailTip label={NEW_APP}>
        <button type="button" aria-label={NEW_APP} onClick={onBuild} className={TILE}>
          <IconCirclePlus className="size-(--size-glyph)" stroke={GLYPH_STROKE} aria-hidden />
        </button>
      </RailTip>
      {/* The track speaks when the bracket keys walk the row, and this is where a member takes that
          back. The word names the act, as every other tile's does, and the glyph carries the
          state. */}
      <RailTip label={muted ? UNMUTE : MUTE}>
        <button
          type="button"
          aria-label={muted ? UNMUTE : MUTE}
          aria-pressed={muted}
          onClick={() => setMuted(!muted)}
          className={TILE}
        >
          {muted ? (
            <IconVolumeOff className="size-(--size-glyph)" stroke={GLYPH_STROKE} aria-hidden />
          ) : (
            <IconVolume className="size-(--size-glyph)" stroke={GLYPH_STROKE} aria-hidden />
          )}
        </button>
      </RailTip>
      <RailTip label={WORKSPACE}>
        <button
          type="button"
          aria-label={WORKSPACE}
          onClick={() => placeWorkspace(tabs[0], {}, "push")}
          className={TILE}
        >
          <IconSettings className="size-(--size-glyph)" stroke={GLYPH_STROKE} aria-hidden />
        </button>
      </RailTip>
      {account}
    </nav>
  );
}
