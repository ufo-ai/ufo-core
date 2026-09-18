import { TurnActivity } from "@/components/ui/turn-activity";
import type { SourceRef, SubagentRun } from "@/lib/types";

const WEB: SourceRef[] = [
  { kind: "web", title: "Pricing", url: "https://stripe.com/pricing", ref: "", provider: "" },
  { kind: "web", title: "Changelog", url: "https://stripe.com/changelog", ref: "", provider: "" },
];

const WORKSPACE: SourceRef[] = [
  { kind: "workspace", title: "Q3 memo", url: "", ref: "memo", provider: "notion" },
  { kind: "workspace", title: "Pricing sheet", url: "", ref: "sheet", provider: "slack" },
  { kind: "workspace", title: "Deal desk", url: "", ref: "desk", provider: "linear" },
  { kind: "workspace", title: "Renewals", url: "", ref: "renew", provider: "github" },
];

const READING = { label: "Searching the web", sources: WEB };
const MEMO = { label: "Reading the memo", sources: WORKSPACE };
const DRAFTING = { label: "Drafting the review", sources: [] };

const RUN: SubagentRun = {
  profile: "researcher",
  name: "Release branch",
  conversation_id: "c1",
  events: [
    { kind: "activity", text: "Reading the commits since Friday" },
    { kind: "activity", text: "Running the 41 tests" },
  ],
  output: "",
  subagents: [],
  running: false,
};

const Stage = ({ children }: { children: React.ReactNode }) => (
  <div className="w-full max-w-(--container-answer)">{children}</div>
);

export function ActivityFirstStep() {
  return (
    <Stage>
      <TurnActivity working="Searching the web" runs={[]} />
    </Stage>
  );
}

export function ActivityStepsLive() {
  return (
    <Stage>
      <TurnActivity working="Drafting the review" steps={[READING, MEMO]} runs={[]} />
    </Stage>
  );
}

export function ActivityWaitingOnRuns() {
  return (
    <Stage>
      <TurnActivity
        working="Reviewing the branch"
        steps={[READING]}
        runs={[{ ...RUN, running: true }]}
      />
    </Stage>
  );
}

export function ActivitySettledFolded() {
  return (
    <Stage>
      <TurnActivity working={null} steps={[READING, MEMO, DRAFTING]} runs={[]} took={4200} settled />
    </Stage>
  );
}

export function ActivitySettledRuns() {
  return (
    <Stage>
      <TurnActivity working={null} steps={[READING]} runs={[RUN]} took={31000} settled />
    </Stage>
  );
}

export function ActivitySettledOneStep() {
  return (
    <Stage>
      <TurnActivity working={null} steps={[DRAFTING]} runs={[]} took={900} settled />
    </Stage>
  );
}

export function ActivitySettledUntimed() {
  return (
    <Stage>
      <TurnActivity working={null} steps={[READING, MEMO]} runs={[]} settled />
    </Stage>
  );
}


export function ActivityReconnecting() {
  return (
    <Stage>
      <TurnActivity working="Reconnecting…" steps={[READING, MEMO]} runs={[]} />
    </Stage>
  );
}

export function ActivityCancelled() {
  return (
    <Stage>
      <TurnActivity working={null} steps={[READING, MEMO]} runs={[]} settled ended={{ kind: "cancelled" }} />
    </Stage>
  );
}

export function ActivityFailed() {
  return (
    <Stage>
      <TurnActivity working={null} steps={[]} runs={[]} settled ended={{ kind: "failed" }} />
    </Stage>
  );
}

export function ActivityIncomplete() {
  return (
    <Stage>
      <TurnActivity
        working={null}
        steps={[READING, MEMO, DRAFTING]}
        runs={[]}
        settled
        ended={{ kind: "incomplete" }}
      />
    </Stage>
  );
}

export function ActivityParked() {
  return (
    <Stage>
      <TurnActivity
        working={null}
        steps={[READING]}
        runs={[]}
        settled
        ended={{
          kind: "parked",
          message: "The model provider limited this task. It will retry after 09:41.",
        }}
      />
    </Stage>
  );
}

export function ActivityLost() {
  return (
    <Stage>
      <TurnActivity working={null} steps={[READING, MEMO]} runs={[]} settled ended={{ kind: "lost" }} />
    </Stage>
  );
}
