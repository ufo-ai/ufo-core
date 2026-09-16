import { ConnectLink } from "@/components/ui/connect-link";

export function HandoffConnected() {
  return (
    <ConnectLink connect={{ provider: "github", label: "GitHub", account: "priya@work.com" }} />
  );
}
