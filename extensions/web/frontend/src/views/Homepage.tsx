import { PanelEmpty, usePanelRead } from "@/kernel/panel";
import type { Agent } from "@/lib/types";

type HomepageRead = { state: "set"; url: string } | { state: "none" };

/** The agent's homepage: the hosted site its binding names, framed whole. The frame is the app's
 *  own trusted page and carries the sandbox around the model-authored bytes itself, so this iframe
 *  takes no sandbox attribute — sandbox flags inherit, and the inner site is promised scripts. */
export function Homepage({ agent }: { agent: Agent }) {
  const read = usePanelRead<HomepageRead>("/agents/" + agent.id + "/homepage");
  if (read.phase === "loading") return null;
  if (read.phase === "failed") return <PanelEmpty>{read.message}</PanelEmpty>;
  if (read.payload.state === "none") {
    return <PanelEmpty>{agent.name} has not built its homepage.</PanelEmpty>;
  }
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <iframe
        src={read.payload.url}
        title={agent.name + " homepage"}
        className="size-full border-0"
      />
    </div>
  );
}
