import { useState } from "react";

import { Panel, PanelEmpty, usePanelRead } from "@/kernel/panel";
import { cn } from "@/lib/cn";
import type { Agent } from "@/lib/types";

type Change = { path: string; patch: string; truncated: boolean };
type ChangesPayload = { changes: Change[]; truncated: boolean };

export function ChangesPane({
  agent,
  conversationId,
  onOpenAgent,
}: {
  agent: Agent;
  conversationId: string;
  onOpenAgent: (agentId: string) => void;
}) {
  const [reloads, setReloads] = useState(0);
  const state = usePanelRead<ChangesPayload>(
    "/agents/" + agent.id + "/conversations/" + conversationId + "/changes",
    reloads,
  );
  return (
    <main className="flex min-h-0 min-w-0 flex-col">
      <div className="flex items-baseline gap-md border-b border-edge px-2xl py-lg">
        <button
          type="button"
          onClick={() => onOpenAgent(agent.id)}
          className="m-0 border-0 bg-transparent p-0 text-title font-strong text-inherit"
        >
          {agent.name}
        </button>
        <span className="font-mono text-mono opacity-(--muted-strong)">Changes</span>
        <button
          type="button"
          className="ml-auto"
          onClick={() => setReloads((count) => count + 1)}
        >
          Refresh
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-2xl">
        <Panel
          state={state}
          failed={(message) => (
            <PanelEmpty>
              {message.startsWith("Error 404")
                ? "This conversation is not shared with you."
                : message}
            </PanelEmpty>
          )}
          empty={(payload) =>
            payload.changes.length || payload.truncated ? null : "No changes."
          }
        >
          {(payload) => (
            <div className="flex flex-col gap-xl">
              {payload.changes.map((change, index) => (
                <ChangedFile key={[change.path, index].join(":")} change={change} />
              ))}
              {payload.truncated ? (
                <p className="m-0 opacity-(--muted-soft)">Some changes may not be shown.</p>
              ) : null}
            </div>
          )}
        </Panel>
      </div>
    </main>
  );
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
