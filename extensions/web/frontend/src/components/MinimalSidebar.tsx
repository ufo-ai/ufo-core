import { type ReactElement, type ReactNode } from "react";
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
import { Spotlight } from "@/views/Spotlight";
import type { Agent } from "@/lib/types";
import { GLYPH_STROKE } from "@/lib/glyph";

const HOME = "Home";
const NEW_TAB = "New tab";
const NEW_APP = "New app";
const WORKSPACE = "Workspace";
const MUTE = "Mute sounds";
const UNMUTE = "Unmute sounds";

/** One mark's box on the rail. The column is the glyph's own width, so the mark is the whole of the
 *  control and the pointer answer is the ink it takes rather than a ground behind it. */
const TILE =
  "flex size-(--size-glyph) shrink-0 items-center justify-center rounded-control border-0 bg-transparent p-0 text-ink-soft hover:text-ink";

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
  agents,
  account,
  onNewTab,
  onLane,
  onBuild,
}: {
  lanes: { lane: string; agent: Agent }[];
  agents: Agent[];
  /** The member's own menu — identity, theme, administration, sign out — drawn at the rail's foot,
   *  because at a desk width this rail is the whole shell and the way out has to stand on it. */
  account: ReactNode;
  onNewTab: () => void;
  onLane: (lane: string) => void;
  onBuild: () => void;
}) {
  const { statuses } = useAppStatus();
  const muted = useMuted();
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
          lane it opens wears — the press and the lane it stands read as one thing. */}
      <RailTip label={NEW_TAB}>
        <button
          type="button"
          aria-label={NEW_TAB}
          onClick={onNewTab}
          className={cn(TILE, "size-(--size-lede) bg-fill")}
        >
          <IconPlus className="size-(--size-glyph)" stroke={GLYPH_STROKE} aria-hidden />
        </button>
      </RailTip>
      {/* Search keeps its one component and its chord: the desk shell has no wide column to carry
          it, so the rail does. */}
      <Spotlight agents={agents} className={TILE} />
      <ul className="scrollbar-none m-0 flex min-h-0 w-full flex-1 list-none flex-col items-center gap-2xl overflow-y-auto p-0">
        {lanes.map(({ lane, agent }) => {
          const dot = statusDot(statuses[agent.id], agent.setup_due === true);
          return (
            <li key={lane}>
              <RailTip label={agentName(agent.name)}>
                <button
                  type="button"
                  aria-label={agentName(agent.name)}
                  onClick={() => onLane(lane)}
                  className={TILE}
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
      {/* The track speaks when a lane opens and when focus crosses to the one beside it, and this
          is where a member takes that back. The word names the act, as every other tile's does, and
          the glyph carries the state. */}
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
          onClick={() => placeWorkspace("team", {}, "push")}
          className={TILE}
        >
          <IconSettings className="size-(--size-glyph)" stroke={GLYPH_STROKE} aria-hidden />
        </button>
      </RailTip>
      {account}
    </nav>
  );
}
