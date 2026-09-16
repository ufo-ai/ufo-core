import type { Meter, SourceRef, SubagentRun, TerminalFrame } from "@/lib/contract";
import type { RunFrame } from "@/lib/turnRecord";
import type { ChatApp, ChatFile } from "@/lib/types";

export const SIMULATED_TURN = "9f1c7a20-0000-4000-8000-0000000000a1";
export const SIMULATED_MODEL = "claude-opus-5";

const WORD_MS = 14;
const STEP_MS = 90;
const SETTLE_MS = 140;

/** One frame the stream emits and how long it waits first. `wait` is stated at the fast pace, which
 *  the picked pace scales; `drop` is the stream failing where a frame would have been. */
export type Beat =
  | { wait: number; event: "message"; data: { text: string } }
  | { wait: number; event: "activity"; data: { text: string; call_id: string } }
  | { wait: number; event: "sources"; data: { items: SourceRef[] } }
  | { wait: number; event: "subagent_activity"; data: RunFrame }
  | { wait: number; event: "subagent"; data: SubagentRun }
  | { wait: number; event: "cost"; data: Meter }
  | { wait: number; event: "files"; data: { files: ChatFile[] } }
  | { wait: number; event: "apps"; data: { apps: ChatApp[] } }
  | { wait: number; event: "terminal"; data: TerminalFrame }
  | { wait: number; event: "parked"; data: { message: string } }
  | { wait: number; drop: true };

export type Pace = "instant" | "fast" | "lifelike";

/** What each pace multiplies a beat's wait by. */
export const PACES: { pace: Pace; label: string; factor: number }[] = [
  { pace: "instant", label: "Instant", factor: 0 },
  { pace: "fast", label: "Fast", factor: 1 },
  { pace: "lifelike", label: "Lifelike", factor: 6 },
];

export type Scenario = {
  id: string;
  name: string;
  /** The words the member sends to start it. */
  ask: string;
  beats: Beat[];
};

/** The reply as the surface receives it: one `message` frame per word. */
function words(text: string): Beat[] {
  return text.split(" ").map((word, index) => ({
    wait: WORD_MS,
    event: "message",
    data: { text: index === 0 ? word : " " + word },
  }));
}

const SETTLED: TerminalFrame = {
  status: "done",
  model: SIMULATED_MODEL,
  tokens: 4812,
  cost_micro_usd: 20400,
  cache_percent: 62,
};

const PARAGRAPH: Beat = { wait: WORD_MS, event: "message", data: { text: "\n\n" } };

const WEB_SOURCES: SourceRef[] = [
  { kind: "web", title: "Pricing", url: "https://stripe.com/pricing" },
  { kind: "web", title: "Changelog", url: "https://stripe.com/changelog" },
  { kind: "web", title: "Rate limits", url: "https://docs.github.com/en/rest/rate-limit" },
];

const WORKSPACE_SOURCES: SourceRef[] = [
  { kind: "workspace", title: "Q3 pricing memo", ref: "drive:1a2b", provider: "googledrive" },
  { kind: "workspace", title: "#pricing", ref: "slack:C0192", provider: "slack" },
];

const READER_TURN = "9f1c7a20-0000-4000-8000-0000000000b1";
const READER_CONVERSATION = "9f1c7a20-0000-4000-8000-0000000000b2";
const WRITER_TURN = "9f1c7a20-0000-4000-8000-0000000000c1";
const WRITER_CONVERSATION = "9f1c7a20-0000-4000-8000-0000000000c2";

function run(fields: Omit<RunFrame, "parent_turn_id">): Beat {
  return {
    wait: STEP_MS,
    event: "subagent_activity",
    data: { ...fields, parent_turn_id: SIMULATED_TURN },
  };
}

const REPORT: ChatFile[] = [
  {
    filename: "pricing-review.pdf",
    url: null,
    preview_url: null,
    media_type: "application/pdf",
    size_bytes: 184320,
  },
  {
    filename: "plan-comparison.csv",
    url: null,
    preview_url: null,
    media_type: "text/csv",
    size_bytes: 2048,
  },
];

const BUILT: ChatApp[] = [
  {
    id: "9f1c7a20-0000-4000-8000-0000000000d1",
    name: "competitor-watch",
    model: SIMULATED_MODEL,
    icon: "aten",
  },
];

/** Every turn the simulator can play. Each one is the member's words and the frames the stream
 *  answers them with, so a press drives the real send, the real stream and the real fold. */
export const SCENARIOS: Scenario[] = [
  {
    id: "reply",
    name: "Plain reply",
    ask: "What changed in the portal this week?",
    beats: [
      ...words(
        "Three changes landed. The composer keeps a draft per conversation, the transcript holds " +
          "its place across a reload, and the model picker lists every family this deploy serves.",
      ),
      PARAGRAPH,
      ...words("The fourth change, topic preferences, is still behind review."),
      { wait: SETTLE_MS, event: "terminal", data: SETTLED },
    ],
  },
  {
    id: "research",
    name: "Research",
    ask: "How does our pricing compare to theirs?",
    beats: [
      { wait: STEP_MS, event: "activity", data: { text: "Searching the web", call_id: "call-1" } },
      { wait: STEP_MS, event: "sources", data: { items: WEB_SOURCES } },
      { wait: STEP_MS, event: "activity", data: { text: "Reading the memo", call_id: "call-2" } },
      { wait: STEP_MS, event: "sources", data: { items: WORKSPACE_SOURCES } },
      { wait: STEP_MS, event: "cost", data: { tokens: 2140, cost_micro_usd: 9800 } },
      ...words(
        "Their entry plan is 20 dollars a seat against our 25, and they meter usage above ten " +
          "thousand calls a month. The Q3 memo already priced that gap at four points of margin.",
      ),
      { wait: SETTLE_MS, event: "terminal", data: SETTLED },
    ],
  },
  {
    id: "subagents",
    name: "Subagents",
    ask: "Review the release branch and write the note.",
    beats: [
      { wait: STEP_MS, event: "activity", data: { text: "Splitting the work", call_id: "call-3" } },
      run({
        turn_id: READER_TURN,
        conversation_id: READER_CONVERSATION,
        profile: "researcher",
        name: "Release branch",
        activity: "Reading the commits since Friday",
        status: "",
      }),
      run({
        turn_id: WRITER_TURN,
        conversation_id: WRITER_CONVERSATION,
        profile: "writer",
        name: "Release note",
        activity: "Drafting the note",
        status: "",
      }),
      run({
        turn_id: READER_TURN,
        conversation_id: READER_CONVERSATION,
        profile: "researcher",
        name: "Release branch",
        activity: "Running the 41 tests behind the topic preferences screen",
        status: "",
      }),
      {
        wait: STEP_MS,
        event: "subagent",
        data: {
          profile: "researcher",
          name: "Release branch",
          conversation_id: READER_CONVERSATION,
          events: [
            { kind: "activity", text: "Reading the commits since Friday" },
            { kind: "activity", text: "Running the 41 tests behind the topic preferences screen" },
          ],
          output: "Eleven commits, all tests green.",
          subagents: [],
          running: false,
          turn_id: READER_TURN,
          parent_turn_id: SIMULATED_TURN,
        },
      },
      run({
        turn_id: WRITER_TURN,
        conversation_id: WRITER_CONVERSATION,
        profile: "writer",
        name: "Release note",
        activity: "",
        status: "done",
      }),
      ...words("Eleven commits, all tests green. The note is drafted against those eleven."),
      { wait: SETTLE_MS, event: "terminal", data: SETTLED },
    ],
  },
  {
    id: "files",
    name: "Files",
    ask: "Write the pricing review up as a document.",
    beats: [
      { wait: STEP_MS, event: "activity", data: { text: "Writing the review", call_id: "call-4" } },
      ...words("The review is attached, with the plan comparison beside it."),
      { wait: STEP_MS, event: "files", data: { files: REPORT } },
      { wait: SETTLE_MS, event: "terminal", data: SETTLED },
    ],
  },
  {
    id: "app",
    name: "New app",
    ask: "Build me something that watches our competitors.",
    beats: [
      { wait: STEP_MS, event: "activity", data: { text: "Creating the app", call_id: "call-5" } },
      ...words("The app is created. Open it to name the competitors it watches."),
      { wait: STEP_MS, event: "apps", data: { apps: BUILT } },
      { wait: SETTLE_MS, event: "terminal", data: SETTLED },
    ],
  },
  {
    id: "question",
    name: "Question",
    ask: "Draft Thursday's release note.",
    beats: [
      ...words("Two things decide the draft."),
      {
        wait: SETTLE_MS,
        event: "terminal",
        data: {
          ...SETTLED,
          question: {
            title: "Before the draft",
            questions: [
              {
                question: "Which branch does the note cover?",
                options: [
                  { label: "release", description: "What ships on Thursday." },
                  { label: "main" },
                ],
              },
              {
                question: "Who reads it first?",
                options: [{ label: "Support" }, { label: "Everyone in the workspace" }],
              },
            ],
          },
        },
      },
    ],
  },
  {
    id: "stopped",
    name: "Stopped",
    ask: "Read every file the changelog has named since Friday.",
    beats: [
      {
        wait: STEP_MS,
        event: "activity",
        data: { text: "Reading CHANGELOG.md", call_id: "call-6" },
      },
      ...words("Forty-one files are named since Friday. Reading them in"),
      { wait: SETTLE_MS, event: "terminal", data: { status: "cancelled" } },
    ],
  },
  {
    id: "failed",
    name: "Failed",
    ask: "Summarise the support queue.",
    beats: [
      { wait: STEP_MS, event: "activity", data: { text: "Reading the queue", call_id: "call-7" } },
      ...words("The queue holds 312 open threads. Grouping them by"),
      {
        wait: SETTLE_MS,
        event: "terminal",
        data: { status: "failed", error_class: "ProviderTimeout" },
      },
    ],
  },
  {
    id: "parked",
    name: "Parked",
    ask: "Give the finance team access to the billing app.",
    beats: [
      ...words("Granting access to the billing app needs an admin."),
      {
        wait: SETTLE_MS,
        event: "parked",
        data: { message: "Waiting for an admin to approve the grant." },
      },
    ],
  },
  {
    id: "lost",
    name: "Connection lost",
    ask: "Walk the release branch file by file.",
    beats: [
      { wait: STEP_MS, event: "activity", data: { text: "Reading the branch", call_id: "call-8" } },
      ...words("Eleven commits, 41 files. Starting with the"),
      { wait: STEP_MS, drop: true },
    ],
  },
];

/** What the turn a member's answer opens streams back. */
export const ANSWERED: Beat[] = [
  ...words(
    "The note covers the release branch and goes to Support first. It lists the three portal " +
      "changes and the two fixes behind them.",
  ),
  { wait: SETTLE_MS, event: "terminal", data: SETTLED },
];
