import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import {
  IconCirclePlus,
  IconBroadcast,
  IconX,
  IconSettings,
  IconVolume,
  IconVolumeOff,
} from "@tabler/icons-react";

import * as DialogPrimitive from "@radix-ui/react-dialog";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { usePanelRead } from "@/kernel/panel";
import { ConnectSurfaces, SURFACES_READ, type SurfacesPayload } from "@/views/Surfaces";
import { statusDot, useAppStatus } from "@/lib/appStatusStore";
import { AgentIcon } from "@/lib/agentIcon";
import { cn } from "@/lib/cn";
import { agentName } from "@/lib/agentName";
import { openHomeLanes } from "@/lib/homeLanes";
import { placeWorkspace } from "@/lib/router";
import { setMuted, useMuted } from "@/lib/sound";
import { useOfferedTabs, useSurfaces } from "@/lib/surfaces";
import { Spotlight } from "@/views/Spotlight";
import type { Agent, Member } from "@/lib/types";
import { GLYPH_STROKE } from "@/lib/glyph";

const HOME = "Home";
const SEARCH = "Search";
const NEW_APP = "New app";
const CHANNELS = "Channels";
const WORKSPACE = "Workspace";
const MUTE = "Mute sounds";
const UNMUTE = "Unmute sounds";

const TILE =
  "flex size-(--size-glyph) shrink-0 items-center justify-center rounded-control border-0 bg-transparent p-0 text-ink-soft hover:text-ink";

const MARK =
  "pointer-events-none absolute top-0 left-1/2 size-(--size-lede) rounded-control bg-fill-strong " +
  "transition-[transform,opacity] duration-200 ease-control";

export const MARK_HELD = 5000;

const SLID = (mark: number | null) => ({
  transform: "translate(-50%, calc(" + String(mark ?? 0) + "px - 50%))",
});

function RailTip({ label, children }: { label: string; children: ReactElement }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}

function ChannelsTile({ agent, member }: { agent: Agent; member: Member }) {
  const [open, setOpen] = useState(false);
  const [toast, setToast] = useState<ToastState>(SILENT);
  const [reloads, setReloads] = useState(0);
  const state = usePanelRead<SurfacesPayload>(SURFACES_READ, reloads);
  const refuse = useCallback((title: string) => setToast({ title }), []);
  const missing =
    state.phase === "ready" &&
    state.payload.surfaces.some((row) => row.offered && !row.connected);
  return (
    <>
      <RailTip label={CHANNELS}>
        <button
          type="button"
          aria-label={CHANNELS}
          onClick={() => setOpen(true)}
          className={cn(TILE, "relative")}
        >
          <IconBroadcast className="size-(--size-glyph)" stroke={GLYPH_STROKE} aria-hidden />
          {missing ? (
            <span
              aria-hidden
              className="absolute -right-2xs -bottom-2xs size-sm rounded-full bg-attention-ink"
            />
          ) : null}
        </button>
      </RailTip>
      {open ? (
          <Dialog
            open
            onOpenChange={(next) => {
              if (next) return;
              setOpen(false);
              setReloads((count) => count + 1);
            }}
          >
          <DialogContent>
            <DialogHeader className="flex-row items-center justify-between">
              <DialogTitle>{CHANNELS}</DialogTitle>
              <DialogPrimitive.Close asChild>
                <Button variant="mark" size="glyph" aria-label="Close" className="text-ink-quiet">
                  <IconX stroke={1.25} aria-hidden />
                </Button>
              </DialogPrimitive.Close>
            </DialogHeader>
            <ConnectSurfaces
              agent={agent}
              member={member}
              onRefused={refuse}
            />
          </DialogContent>
        </Dialog>
      ) : null}
      <Toast state={toast} onDone={() => setToast(SILENT)} />
    </>
  );
}

export function MinimalSidebar({
  lanes,
  active,
  agents,
  member,
  main,
  account,
  onLane,
  onBuild,
}: {
  lanes: { lane: string; agent: Agent }[];
  active: string | undefined;
  agents: Agent[];
  member: Member;
  main: Agent | null;
  account: ReactNode;
  onLane: (lane: string) => void;
  onBuild: () => void;
}) {
  const { statuses } = useAppStatus();
  const tabs = useOfferedTabs();
  const surfaces = useSurfaces();
  const muted = useMuted();
  const list = useRef<HTMLUListElement>(null);
  const [mark, setMark] = useState<number | null>(null);
  const [held, setHeld] = useState(false);
  useLayoutEffect(() => {
    const tile = list.current?.querySelector<HTMLElement>("[data-active]") ?? null;
    setMark(tile === null ? null : tile.offsetTop + tile.offsetHeight / 2);
  }, [active, lanes]);
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
        <button type="button" aria-label={HOME} onClick={openHomeLanes} className={cn(TILE, "text-ink")}>
          <svg width={12.5} height={11} viewBox="0 0 12.5 11" fill="currentColor" aria-hidden>
            <circle cx={2} cy={2} r={2} />
            <circle cx={10.5} cy={2} r={2} />
            <circle cx={6.25} cy={9} r={2} />
          </svg>
        </button>
      </RailTip>
      <RailTip label={SEARCH}>
        <Spotlight
          agents={agents}
          className={cn(TILE, "size-(--size-lede) bg-fill")}
        />
      </RailTip>
      {/* A scrolling box clips both axes, so the track carries a gutter the width of the dot's overhang.
          Without it the rail is 16px wide inside its padding and the dot loses its outer half at every width. */}
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
      {surfaces.apps ? (
        <RailTip label={NEW_APP}>
          <button type="button" aria-label={NEW_APP} onClick={onBuild} className={TILE}>
            <IconCirclePlus className="size-(--size-glyph)" stroke={GLYPH_STROKE} aria-hidden />
          </button>
        </RailTip>
      ) : null}
      {main ? <ChannelsTile agent={main} member={member} /> : null}
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
