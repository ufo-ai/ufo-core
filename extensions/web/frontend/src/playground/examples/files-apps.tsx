import { TurnApps } from "@/components/ui/turn-apps";
import type { ChatApp } from "@/lib/types";

const APPS: ChatApp[] = [
  {
    id: "competitor-watch",
    name: "competitor watch",
    model: "claude-opus-5",
    icon: "propylon",
  },
  {
    id: "support-digest",
    name: "support digest",
    model: "claude-sonnet-5",
    icon: "akhet",
  },
];

export function FilesApps() {
  return <TurnApps apps={APPS} />;
}
