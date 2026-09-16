import { ConnectLink } from "@/components/ui/connect-link";

export function HandoffConnect() {
  return <ConnectLink connect={{ turn: "turn-2f41", provider: "github", label: "GitHub" }} />;
}
