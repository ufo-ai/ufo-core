import { useState } from "react";
import {
  IconApps,
  IconCirclePlus,
  IconDots,
  IconPin,
  IconPinFilled,
} from "@tabler/icons-react";

import {
  SIDEBAR_FOLDED,
  SIDEBAR_PRESS,
  SIDEBAR_ROW,
  SidebarPress,
  SidebarRow,
} from "@/components/Sidebar";
import { Ticker } from "@/components/ui/ticker";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { useAppStatus, type AgentStatus } from "@/lib/appStatusStore";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";
import { appOrder, appRun } from "@/lib/rail";
import { useSurfaces } from "@/lib/surfaces";
import { APP_STORE_TITLE } from "@/lib/title";
import type { Agent } from "@/lib/types";
import { APP_CREATOR_TITLE } from "@/lib/wizard";

function statusDot(status: AgentStatus | undefined, setupDue: boolean): string | null {
  if (status?.turn === "running" || status?.turn === "queued") return "bg-live";
  if (setupDue || status?.turn === "parked" || status?.last_failed) return "bg-blocked";
  return null;
}

function AgentRow({
  agent,
  status,
  open,
  pinned,
  collapsed,
  onPin,
  onOpen,
}: {
  agent: Agent;
  status: AgentStatus | undefined;
  open: boolean;
  pinned: boolean;
  collapsed: boolean;
  onPin: () => void;
  onOpen: () => void;
}) {
  const [asks, setAsks] = useState(0);
  const dot = statusDot(status, agent.setup_due === true);
  const row = (
    <SidebarRow current={open}>
      <SidebarPress
        current={open}
        collapsed={collapsed}
        label={agentName(agent.name)}
        className="py-2xs"
        onClick={onOpen}
        onPointerEnter={() => setAsks((asked) => asked + 1)}
        onPointerLeave={() => setAsks(0)}
        onFocus={() => setAsks((asked) => asked + 1)}
        onBlur={() => setAsks(0)}
        glyph={
          <span className="relative shrink-0">
            <AgentIcon name={agent.icon} className="size-(--size-glyph)" />
            <span
              aria-hidden
              className={cn(
                "absolute -right-2xs -bottom-2xs size-sm rounded-full transition duration-200 ease-control",
                dot ?? "scale-0",
              )}
            />
          </span>
        }
      >
        <Ticker asks={asks} className="flex-1 text-label">
          {agentName(agent.name)}
        </Ticker>
      </SidebarPress>
      {collapsed ? null : (
        <button
          type="button"
          aria-label={(pinned ? "Unpin " : "Pin ") + agentName(agent.name)}
          aria-pressed={pinned}
          onClick={onPin}
          className={cn(
            "mr-xs shrink-0 rounded-control border-0 bg-transparent p-2xs text-ink-soft hover:bg-fill",
            "opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100",
          )}
        >
          {pinned ? (
            <IconPinFilled className="size-icon" aria-hidden />
          ) : (
            <IconPin className="size-icon" aria-hidden />
          )}
        </button>
      )}
    </SidebarRow>
  );
  if (!collapsed) return row;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{row}</TooltipTrigger>
      <TooltipContent side="right">{agentName(agent.name)}</TooltipContent>
    </Tooltip>
  );
}

export function AppsIndex({
  agents,
  openId,
  store,
  pinned,
  expanded,
  collapsed,
  onExpand,
  onPin,
  onOpen,
  onStore,
  onBuild,
}: {
  agents: Agent[];
  openId: string | null;
  store: boolean;
  pinned: string[];
  expanded: boolean;
  collapsed: boolean;
  onExpand: (expanded: boolean) => void;
  onPin: (agentId: string) => void;
  onOpen: (agentId: string) => void;
  onStore: () => void;
  onBuild: () => void;
}) {
  const mainAgent = useMainAgent();
  const offered = useSurfaces();
  const { statuses } = useAppStatus();
  /** Work in flight lifts a row to the top rather than moving it through this ladder: an app that sorted
   *  as `now` while it worked would drop back down the column the moment it stopped. */
  const lastActiveAt = (agentId: string): number | null => {
    const at = statuses[agentId]?.last_active_at;
    return at ? Date.parse(at) : null;
  };
  const working = (agentId: string): boolean => {
    const status = statuses[agentId];
    return status?.turn === "running" || status?.turn === "queued";
  };
  const ordered = appOrder(agents, pinned, lastActiveAt);
  const run = appRun(ordered, working);
  const shown = expanded ? run.shown.concat(run.more) : run.shown;
  return (
    <nav aria-label="Apps" className="flex min-h-0 flex-col">
      <div className="flex min-h-0 max-h-(--size-apps-open) flex-col overflow-y-auto">
        <ul className="m-0 flex list-none flex-col gap-px p-0">
          {shown.map((agent) => (
            <AgentRow
              key={agent.id}
              agent={agent}
              status={statuses[agent.id]}
              open={agent.id === openId}
              pinned={pinned.includes(agent.id)}
              collapsed={collapsed}
              onPin={() => onPin(agent.id)}
              onOpen={() => onOpen(agent.id)}
            />
          ))}
          {offered["app-store"] ? (
            <SidebarRow current={store}>
              <SidebarPress
                current={store}
                collapsed={collapsed}
                label={APP_STORE_TITLE}
                glyph={<IconApps className="size-(--size-glyph) shrink-0" aria-hidden />}
                onClick={onStore}
              />
            </SidebarRow>
          ) : mainAgent ? (
            <SidebarRow>
              <SidebarPress
                collapsed={collapsed}
                label={APP_CREATOR_TITLE}
                glyph={<IconCirclePlus className="size-(--size-glyph) shrink-0" aria-hidden />}
                onClick={onBuild}
              />
            </SidebarRow>
          ) : null}
        </ul>
      </div>
      {run.more.length || expanded ? (
        <button
          type="button"
          aria-expanded={expanded}
          aria-label={expanded ? "Less applications" : "More applications"}
          onClick={() => onExpand(!expanded)}
          className={cn(
            SIDEBAR_ROW,
            SIDEBAR_PRESS,
            "shrink-0 text-ink-soft",
            collapsed && SIDEBAR_FOLDED,
          )}
        >
          <IconDots className="size-(--size-glyph) shrink-0" aria-hidden />
          <span className={cn("min-w-0 flex-1 truncate", collapsed && "hidden")}>
            {expanded ? "Less" : "More"}
          </span>
        </button>
      ) : null}
    </nav>
  );
}
