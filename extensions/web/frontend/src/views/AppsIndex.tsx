import { useState, type CSSProperties } from "react";
import { IconPin, IconPinFilled } from "@tabler/icons-react";

import { Ticker } from "@/components/ui/ticker";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { statusDot, useAppStatus, type AgentStatus } from "@/lib/appStatusStore";
import { useChat } from "@/lib/chatStore";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";
import { appOrder } from "@/lib/rail";
import type { Agent } from "@/lib/types";
import { APP_CREATOR_TITLE, wizardKey } from "@/lib/wizard";

const RESPONDING = "Responding";

function activeLine(status: AgentStatus | undefined): { text: string; shimmer: boolean } | null {
  if (status === undefined) return null;
  if (status.turn === "running") return { text: status.activity ?? RESPONDING, shimmer: true };
  if (status.turn === "queued") return { text: "Queued", shimmer: false };
  return null;
}

/** A character the browser has just inserted is what runs the cut, which is why each carries the line
 *  it belongs to in its key. A screen reader is read the line once, whole. */
function ActivityLine({
  asks,
  text,
  shimmer,
}: {
  asks: number;
  text: string;
  shimmer: boolean;
}) {
  return (
    <Ticker asks={asks} className={cn("font-mono text-small text-ink-soft", shimmer && "shimmer")}>
      <span className="sr-only">{text}</span>
      <span aria-hidden>
        {Array.from(text, (character, at) => (
          <span key={text + at} data-cut style={{ "--cut": at } as CSSProperties}>
            {character}
          </span>
        ))}
      </span>
    </Ticker>
  );
}


function AgentRow({
  agent,
  status,
  open,
  pinned,
  onPin,
  onOpen,
}: {
  agent: Agent;
  status: AgentStatus | undefined;
  open: boolean;
  pinned: boolean;
  onPin: () => void;
  onOpen: () => void;
}) {
  const [asks, setAsks] = useState(0);
  const said = open ? null : activeLine(status);
  /** A track collapsing over nothing collapses instantly, so the words that were there stay drawn until
   *  the fold has shut. */
  const [held, setHeld] = useState(said);
  if (said !== null && (held === null || held.text !== said.text || held.shimmer !== said.shimmer)) {
    setHeld(said);
  }
  const dot = statusDot(status, agent.setup_due === true);
  return (
    <li className={cn("group/row flex items-center rounded-row hover:bg-fill", open && "bg-fill")}>
      <button
        type="button"
        aria-current={open}
        onClick={onOpen}
        onPointerEnter={() => setAsks((asked) => asked + 1)}
        onPointerLeave={() => setAsks(0)}
        onFocus={() => setAsks((asked) => asked + 1)}
        onBlur={() => setAsks(0)}
        className={cn(
          "flex min-h-(--size-row) min-w-0 flex-1 items-center gap-md border-0 bg-transparent",
          "px-sm py-2xs text-left text-inherit",
        )}
      >
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
        <span className="flex min-w-0 flex-1 flex-col">
          <Ticker asks={asks} className="text-label">
            {agentName(agent.name)}
          </Ticker>
          <span
            className={cn(
              "grid transition-[grid-template-rows] duration-200 ease-control",
              said === null ? "grid-rows-[0fr]" : "grid-rows-[1fr]",
            )}
            onTransitionEnd={(event) => {
              /* The line inside this track travels on its own transition, and that one bubbles here too. Only the
                 track's own end means the fold has shut. */
              if (event.target !== event.currentTarget) return;
              if (said === null) setHeld(null);
            }}
          >
            <span className="min-h-0 min-w-0 overflow-hidden">
              {held === null ? null : (
                <ActivityLine asks={asks} text={held.text} shimmer={held.shimmer} />
              )}
            </span>
          </span>
        </span>
      </button>
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
    </li>
  );
}

export function AppsIndex({
  agents,
  openId,
  building,
  pinned,
  onPin,
  onOpen,
  onBuild,
}: {
  agents: Agent[];
  openId: string | null;
  building: boolean;
  pinned: string[];
  onPin: (agentId: string) => void;
  onOpen: (agentId: string) => void;
  onBuild: () => void;
}) {
  const mainAgent = useMainAgent();
  const { statuses } = useAppStatus();
  const shown = appOrder(agents, pinned);
  const key = mainAgent ? wizardKey(mainAgent.id) : null;
  const held = useChat(key ?? "");
  const running =
    key !== null &&
    !held.closed &&
    (held.busy || (held.messages ?? []).length > 0 || held.founded !== null);
  const runTitle = held.founded?.title ?? null;
  return (
    <nav aria-label="Apps" className="flex min-h-0 flex-col">
      <div className="flex min-h-0 max-h-(--size-apps-open) flex-col overflow-y-auto">
        <ul className="m-0 flex list-none flex-col gap-px p-0">
          {building || running ? (
            <li className={cn("flex items-center gap-xs rounded-row", building && "bg-fill")}>
              {building ? (
                <div
                  aria-current
                  className="flex min-w-0 flex-1 flex-col gap-2xs px-sm py-xs"
                >
                  <span className="min-w-0 truncate text-label">
                    {runTitle ? APP_CREATOR_TITLE + ": " + runTitle : APP_CREATOR_TITLE}
                  </span>
                  <span className="w-full truncate font-mono text-small text-ink-soft">
                    Building
                  </span>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={onBuild}
                  className={cn(
                    "flex min-w-0 flex-1 flex-col gap-2xs border-0 bg-transparent px-sm py-xs",
                    "rounded-row text-left text-inherit hover:bg-fill",
                  )}
                >
                  <span className="min-w-0 max-w-full truncate text-label">
                    {runTitle ? APP_CREATOR_TITLE + ": " + runTitle : APP_CREATOR_TITLE}
                  </span>
                  <span className="w-full truncate font-mono text-small text-ink-soft">
                    Building
                  </span>
                </button>
              )}
            </li>
          ) : null}
          {shown.map((agent) => (
            <AgentRow
              key={agent.id}
              agent={agent}
              status={statuses[agent.id]}
              open={!building && agent.id === openId}
              pinned={pinned.includes(agent.id)}
              onPin={() => onPin(agent.id)}
              onOpen={() => onOpen(agent.id)}
            />
          ))}
        </ul>
      </div>
    </nav>
  );
}
