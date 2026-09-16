import { Starters, type StarterRow } from "@/components/ui/starters";

const ROWS: StarterRow[] = [
  {
    kind: "app",
    line: "Track the competitors you name, with a source for every claim.",
    ask: "I want an application that tracks the competitors I name and writes up what changed, with a source for each claim.",
  },
  {
    kind: "check_in",
    line: "Send me what is still open in the support queue every morning at 09:00.",
    ask: "I want an application that reads the support queue and sends me what is still open every morning at 09:00.",
  },
  {
    kind: "app",
    line: "Draft the release note from the branches that merged this week.",
    ask: "I want an application that drafts our release note from the branches that merged this week.",
  },
];

export function StartRows() {
  return <Starters rows={ROWS} unlock={null} waiting={false} onStart={() => {}} />;
}
