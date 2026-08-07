import { useState } from "react";

import { Panel, PanelEmpty, usePanelRead } from "@/kernel/panel";
import { cn } from "@/lib/cn";
import type { Agent } from "@/lib/types";

export type ConversationSlotSummary = {
  id: string;
  label: string;
  icon: PortalIcon;
  kind: string;
  count: number;
};

export type ConversationSlotsPayload = { slots: ConversationSlotSummary[] };

type Change = { path: string; patch: string; truncated: boolean };
type ChangesPayload = { type: "changes"; changes: Change[]; truncated: boolean };
type SlotPayload = ChangesPayload;
export type PortalIcon = "artifact" | "diff" | "file" | "link";

function slotPath(
  agentId: string,
  conversationId: string,
  slot: string,
  rootConversationId?: string,
) {
  return (
    "/agents/" +
    agentId +
    "/conversations/" +
    conversationId +
    "/slots/" +
    slot +
    (rootConversationId ? "?root=" + rootConversationId : "")
  );
}

function slotsPath(agentId: string, conversationId: string, rootConversationId?: string) {
  return (
    "/agents/" +
    agentId +
    "/conversations/" +
    conversationId +
    "/slots" +
    (rootConversationId ? "?root=" + rootConversationId : "")
  );
}

export function ConversationSlotPane({
  agent,
  conversationId,
  slot,
  rootConversationId,
  summary,
  embedded = false,
  onClose,
  onOpenAgent,
}: {
  agent: Agent;
  conversationId: string;
  slot: string;
  rootConversationId?: string;
  summary?: ConversationSlotSummary;
  embedded?: boolean;
  onClose?: () => void;
  onOpenAgent?: (agentId: string) => void;
}) {
  const [reloads, setReloads] = useState(0);
  const inventory = usePanelRead<ConversationSlotsPayload>(
    summary ? null : slotsPath(agent.id, conversationId, rootConversationId),
  );
  const state = usePanelRead<SlotPayload>(
    slotPath(agent.id, conversationId, slot, rootConversationId),
    reloads,
  );
  const resolved =
    summary ??
    (inventory.phase === "ready"
      ? inventory.payload.slots.find((entry) => entry.id === slot)
      : undefined);
  const label = resolved?.label ?? slot;
  const content = (
    <>
      <div className="flex items-baseline gap-md border-b border-edge px-xl py-lg">
        {!embedded && onOpenAgent ? (
          <button
            type="button"
            onClick={() => onOpenAgent(agent.id)}
            className="m-0 border-0 bg-transparent p-0 text-title font-strong text-inherit"
          >
            {agent.name}
          </button>
        ) : null}
        <span className="inline-flex items-center gap-xs font-mono text-mono opacity-(--muted-strong)">
          {resolved ? <SlotIcon icon={resolved.icon} /> : null}
          {label}
        </span>
        <button type="button" className="ml-auto" onClick={() => setReloads((count) => count + 1)}>
          Refresh
        </button>
        {onClose ? (
          <button type="button" aria-label="Close slot" onClick={onClose}>
            Close
          </button>
        ) : null}
      </div>
      <div className="flex-1 overflow-y-auto p-xl">
        <Panel
          state={state}
          failed={(message) => (
            <PanelEmpty>
              {message.startsWith("Error 404")
                ? "This conversation is not shared with you."
                : message}
            </PanelEmpty>
          )}
        >
          {(payload) => <SlotContent payload={payload} />}
        </Panel>
      </div>
    </>
  );
  if (embedded) {
    return (
      <aside
        className="flex min-h-0 min-w-0 flex-col border-l border-edge max-narrow:absolute max-narrow:inset-0 max-narrow:z-10 max-narrow:border-l-0 max-narrow:bg-surface"
        aria-label={label}
      >
        {content}
      </aside>
    );
  }
  return <main className="flex min-h-0 min-w-0 flex-col">{content}</main>;
}

export function SlotIcon({ icon }: { icon: PortalIcon }) {
  const path = {
    artifact: "M4 7 12 3l8 4-8 4-8-4Zm0 5 8 4 8-4M4 17l8 4 8-4",
    diff: "M8 5v6M5 8h6M14 8h5M5 17h6M14 17h5",
    file: "M6 2h8l4 4v16H6V2Zm8 0v5h5",
    link: "M9 15l6-6M7.5 17.5l-1 1a3.5 3.5 0 0 1-5-5l4-4a3.5 3.5 0 0 1 5 0M16.5 6.5l1-1a3.5 3.5 0 0 1 5 5l-4 4a3.5 3.5 0 0 1-5 0",
  }[icon];
  return (
    <svg
      aria-hidden
      data-slot-icon={icon}
      viewBox="0 0 24 24"
      className="h-[1em] w-[1em] shrink-0 fill-none stroke-current"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={path} />
    </svg>
  );
}

function SlotContent({ payload }: { payload: SlotPayload }) {
  if (payload.type === "changes") {
    if (!payload.changes.length && !payload.truncated) return <PanelEmpty>No changes.</PanelEmpty>;
    return (
      <div className="flex flex-col gap-xl">
        {payload.changes.map((change, index) => (
          <ChangedFile key={[change.path, index].join(":")} change={change} />
        ))}
        {payload.truncated ? (
          <p className="m-0 opacity-(--muted-soft)">Some changes may not be shown.</p>
        ) : null}
      </div>
    );
  }
}

function ChangedFile({ change }: { change: Change }) {
  return (
    <section className="min-w-0 overflow-hidden rounded-panel border border-edge">
      <div className="border-b border-edge bg-fill-subtle px-lg py-sm">
        <h2 className="m-0 break-all font-mono text-label font-strong">{change.path}</h2>
      </div>
      <div className="overflow-x-auto">
        <pre className="m-0 min-w-max font-mono text-mono">
          {change.patch.split("\n").map((line, index) => (
            <span
              key={index}
              className={cn(
                "block px-md",
                index > 1 && line.startsWith("+") && "bg-link/10",
                index > 1 && line.startsWith("-") && "bg-attention/25",
                line.startsWith("@@") && "bg-fill-subtle",
              )}
            >
              {line || " "}
            </span>
          ))}
        </pre>
      </div>
      {change.truncated ? (
        <p className="m-0 border-t border-edge px-lg py-sm opacity-(--muted-soft)">
          This diff is truncated.
        </p>
      ) : null}
    </section>
  );
}
