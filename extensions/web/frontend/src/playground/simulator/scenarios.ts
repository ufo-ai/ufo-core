import type { Meter, SourceRef, SubagentRun, TerminalFrame } from "@/lib/contract";
import type { Bubble, RunFrame } from "@/lib/turnRecord";
import type { ChatApp, ChatConnect, ChatFile, CredentialRequest, Speaker } from "@/lib/types";

export const SIMULATED_TURN = "9f1c7a20-0000-4000-8000-0000000000a1";
export const SIMULATED_CONVERSATION = "9f1c7a20-0000-4000-8000-0000000000e1";
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
  | { wait: number; event: "connect"; data: ChatConnect }
  | { wait: number; event: "credentials"; data: CredentialRequest }
  | { wait: number; drop: true };

/** What the simulator multiplies a beat's wait by, so a turn lands at the speed a member sees. */
export const PACE_FACTOR = 6;

export type Scenario = {
  id: string;
  name: string;
  /** The words the member sends to start it. */
  ask: string;
  /** Who else already spoke in the channel, seated before the member's words land. */
  cast?: Bubble[];
  beats: Beat[];
};

const ALEX: Speaker = { name: "Alex Rivera", email: "alex@example.com" };
const MARSHALL: Speaker = { name: "Marshall Doyle", email: "marshall@example.com" };

const CHANNEL: Bubble[] = [
  {
    role: "user",
    text: "I invited some of my team to this chat.",
    turn: "9f1c7a20-0000-4000-8000-0000000000f1",
    at: "2026-09-17T09:02:00Z",
  },
  {
    role: "user",
    text: "Wow, this multiplayer experience is really cool",
    turn: "9f1c7a20-0000-4000-8000-0000000000f2",
    speaker: ALEX,
    at: "2026-09-17T09:03:00Z",
  },
  {
    role: "user",
    text: "Digging this. Can't wait to get work done together.",
    turn: "9f1c7a20-0000-4000-8000-0000000000f3",
    speaker: MARSHALL,
    at: "2026-09-17T09:04:00Z",
  },
  {
    role: "user",
    text: "Let's invite Alex G.",
    turn: "9f1c7a20-0000-4000-8000-0000000000f4",
    speaker: MARSHALL,
    at: "2026-09-17T09:04:30Z",
  },
  {
    role: "user",
    text: "Can you handle that?",
    turn: "9f1c7a20-0000-4000-8000-0000000000f5",
    speaker: MARSHALL,
    at: "2026-09-17T09:05:00Z",
  },
];

/** The reply as the surface receives it: one `message` frame per word. */
function words(text: string): Beat[] {
  return text.split(" ").map((word, index) => ({
    wait: WORD_MS,
    event: "message",
    data: { text: index === 0 ? word : " " + word },
  }));
}

const SPENT = {
  model: SIMULATED_MODEL,
  tokens: 4812,
  cost_micro_usd: 20400,
  cache_percent: 62,
};

/** The turn settling on the words it streamed: the done terminal restates the whole answer, and
 *  every frame the terminal carries on the wire lands between the words and it. */
function settles(text: string, ...between: Beat[]): Beat[] {
  return [
    ...words(text),
    ...between,
    { wait: SETTLE_MS, event: "terminal", data: { status: "done", text, ...SPENT } },
  ];
}

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

type Child = Pick<RunFrame, "turn_id" | "conversation_id" | "profile" | "name">;

const READER: Child = {
  turn_id: READER_TURN,
  conversation_id: READER_CONVERSATION,
  profile: "researcher",
  name: "Release branch",
};

const WRITER: Child = {
  turn_id: WRITER_TURN,
  conversation_id: WRITER_CONVERSATION,
  profile: "writer",
  name: "Release note",
};

function run(wait: number, child: Child, moment: { activity?: string; status?: string }): Beat {
  return {
    wait,
    event: "subagent_activity",
    data: {
      ...child,
      parent_turn_id: SIMULATED_TURN,
      activity: moment.activity ?? "",
      status: moment.status ?? "",
    },
  };
}

const READER_COMMITS = "Reading the commits since Friday";
const READER_TESTS = "Running the 41 tests behind the topic preferences screen";
const WRITER_DRAFT = "Drafting the note";

const PUBLIC_BASE = "https://ufo.example.com";
const REVIEW_ID = "9f1c7a20-0000-4000-8000-0000000000a2";
const COMPARISON_ID = "9f1c7a20-0000-4000-8000-0000000000a3";
const SIGNED = "?exp=1789000000&ws=" + SIMULATED_CONVERSATION + "&sig=c2ltdWxhdGVk";

function shared(id: string, filename: string, media_type: string, size_bytes: number): ChatFile {
  return {
    id,
    filename,
    subject: null,
    media_type,
    size_bytes,
    role: "file",
    url: PUBLIC_BASE + "/artifacts/" + id + "/" + filename + SIGNED,
    preview_url: null,
  };
}

const REPORT: ChatFile[] = [
  shared(REVIEW_ID, "pricing-review.pdf", "application/pdf", 184320),
  shared(COMPARISON_ID, "plan-comparison.csv", "text/csv", 2048),
];

const BUILT: ChatApp[] = [
  {
    id: "9f1c7a20-0000-4000-8000-0000000000d1",
    name: "competitor-watch",
    model: SIMULATED_MODEL,
    icon: "aten",
  },
];

const VERCEL_REASON = "The deploy log is behind an API key, and the workspace holds none for Vercel.";

/** Every turn the simulator can play. Each one is the member's words and the frames the stream
 *  answers them with, so a press drives the real send, the real stream and the real fold. */
export const SCENARIOS: Scenario[] = [
  {
    id: "channel",
    name: "Group chat",
    ask: "Who else is in here?",
    cast: CHANNEL,
    beats: settles(
      "Alex Rivera and Marshall Doyle are both in this conversation. Either of them can ask me " +
        "for anything you can, and every reply lands here for all of you.",
    ),
  },
  {
    id: "reply",
    name: "Plain reply",
    ask: "What changed in the portal this week?",
    beats: settles(
      "Three changes landed. The composer keeps a draft per conversation, the transcript holds " +
        "its place across a reload, and the model picker lists every family this deploy serves." +
        "\n\nThe fourth change, topic preferences, is still behind review.",
    ),
  },
  {
    id: "research",
    name: "Research",
    ask: "How does our pricing compare to theirs?",
    beats: [
      { wait: STEP_MS, event: "sources", data: { items: WORKSPACE_SOURCES } },
      { wait: STEP_MS, event: "activity", data: { text: "Searching the web", call_id: "call-1" } },
      { wait: STEP_MS, event: "sources", data: { items: WEB_SOURCES } },
      { wait: STEP_MS, event: "activity", data: { text: "Reading the memo", call_id: "call-2" } },
      { wait: STEP_MS, event: "cost", data: { tokens: 2140, cost_micro_usd: 9800 } },
      ...settles(
        "Their entry plan is 20 dollars a seat against our 25, and they meter usage above ten " +
          "thousand calls a month. The Q3 memo already priced that gap at four points of margin.",
      ),
    ],
  },
  {
    id: "subagents",
    name: "Subagents",
    ask: "Review the release branch and write the note.",
    beats: [
      { wait: STEP_MS, event: "activity", data: { text: "Splitting the work", call_id: "call-3" } },
      run(60, READER, {}),
      run(40, WRITER, {}),
      run(70, READER, { activity: READER_COMMITS }),
      run(50, WRITER, { activity: WRITER_DRAFT }),
      run(380, READER, { activity: READER_TESTS }),
      run(260, READER, { status: "done" }),
      run(120, WRITER, { status: "done" }),
      ...settles(
        "Eleven commits, all tests green. The note is drafted against those eleven.",
        {
          wait: STEP_MS,
          event: "subagent",
          data: {
            profile: READER.profile,
            name: READER.name,
            conversation_id: READER_CONVERSATION,
            events: [
              { kind: "activity", text: READER_COMMITS },
              { kind: "note", text: "Eleven commits touch the portal; the rest are infra." },
              { kind: "activity", text: READER_TESTS },
            ],
            output: "Eleven commits, all tests green.",
            subagents: [],
            running: false,
          },
        },
        {
          wait: 0,
          event: "subagent",
          data: {
            profile: WRITER.profile,
            name: WRITER.name,
            conversation_id: WRITER_CONVERSATION,
            events: [{ kind: "activity", text: WRITER_DRAFT }],
            output: "The note is drafted against the eleven commits.",
            subagents: [],
            running: false,
          },
        },
      ),
    ],
  },
  {
    id: "files",
    name: "Files",
    ask: "Write the pricing review up as a document.",
    beats: [
      { wait: STEP_MS, event: "activity", data: { text: "Writing the review", call_id: "call-4" } },
      ...settles("The review is attached, with the plan comparison beside it.", {
        wait: STEP_MS,
        event: "files",
        data: { files: REPORT },
      }),
    ],
  },
  {
    id: "app",
    name: "New app",
    ask: "Build me something that watches our competitors.",
    beats: [
      { wait: STEP_MS, event: "activity", data: { text: "Creating the app", call_id: "call-5" } },
      ...settles("The app is created. Open it to name the competitors it watches.", {
        wait: STEP_MS,
        event: "apps",
        data: { apps: BUILT },
      }),
    ],
  },
  {
    id: "connect",
    name: "Connect account",
    ask: "Pull my open pull requests.",
    beats: [
      { wait: STEP_MS, event: "activity", data: { text: "Reaching GitHub", call_id: "call-6" } },
      ...settles(
        "GitHub is not connected to this workspace yet. Connect it and I will pull them.",
        {
          wait: STEP_MS,
          event: "connect",
          data: { turn: SIMULATED_TURN, provider: "github", label: "GitHub" },
        },
      ),
    ],
  },
  {
    id: "credential",
    name: "Credential",
    ask: "Check the deploy log for last night's release.",
    beats: [
      { wait: STEP_MS, event: "activity", data: { text: "Reaching Vercel", call_id: "call-7" } },
      ...settles(VERCEL_REASON, {
        wait: STEP_MS,
        event: "credentials",
        data: {
          sealed: "sealed-2f41",
          reason: VERCEL_REASON,
          prompts: [
            { slot: "vercel_token", prompt: "Paste a token with read access to the project." },
          ],
        },
      }),
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
          status: "done",
          text: "Two things decide the draft.",
          ...SPENT,
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
      {
        wait: SETTLE_MS,
        event: "terminal",
        data: {
          status: "cancelled",
          text: "",
          error_class: null,
          error_message: null,
          tokens: 0,
          cost_micro_usd: 0,
          cache_percent: 0,
          model: "",
        },
      },
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
        data: {
          status: "failed",
          text: "",
          error_class: "ProviderTimeout",
          error_message: "The model produced no token for 60 seconds.",
          ...SPENT,
          tokens: 980,
          cost_micro_usd: 4100,
        },
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
export const ANSWERED: Beat[] = settles(
  "The note covers the release branch and goes to Support first. It lists the three portal " +
    "changes and the two fixes behind them.",
);
