import { ObjectPane } from "@/kernel/objects";
import { PanelEmpty } from "@/kernel/panel";
import { useMainAgent } from "@/lib/mainAgent";

const SITE_KIND = "site";

export function Sites() {
  const mainAgent = useMainAgent();
  if (!mainAgent) return <PanelEmpty>No agent answers this workspace.</PanelEmpty>;
  return (
    <ObjectPane
      agentId={mainAgent.id}
      kind={SITE_KIND}
      absent="No sites extension is installed."
    />
  );
}
