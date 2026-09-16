import { Starters, type StarterRow, type UnlockRow } from "@/components/ui/starters";

const ROWS: StarterRow[] = [
  {
    kind: "app",
    line: "Track the competitors you name, with a source for every claim.",
    ask: "I want an application that tracks the competitors I name and writes up what changed, with a source for each claim.",
  },
];

const UNLOCK: UnlockRow = {
  line: "Answer questions about last quarter's revenue.",
  ask: "I want an application that answers questions about last quarter's revenue.",
  providers: [
    { name: "stripe", label: "Stripe" },
    { name: "hubspot", label: "HubSpot" },
  ],
};

export function StartUnlock() {
  return <Starters rows={ROWS} unlock={UNLOCK} waiting={false} onStart={() => {}} />;
}
