import type { ActivityEvent, AskQuestion, SubagentRun } from "@/lib/contract";

export type {
  ActivityEvent,
  AskQuestion as QuestionEntry,
  QuestionOption,
  SourceRef,
  SubagentRun,
} from "@/lib/contract";

/** It rides the agent object, so opening an app paints from the boot read. `deploy_generation` changes
 *  with the page's deployment, so a fresh answer remounts it. */
export type Homepage =
  | { state: "set"; url: string; deploy_generation?: number }
  | { state: "none" };

export type Agent = {
  id: string;
  name: string;
  model: string;
  main: boolean;
  homepage?: Homepage;
  icon: string;
  purpose?: string | null;
  app?: string | null;
  mine?: boolean;
  hidden?: boolean;
  setup_due?: boolean;
  /** It rides the boot read because the shell draws from that read: a fact learned a request later had
   *  the sidebar drawn and then taken away again. */
  stands_on_setup?: boolean;
  web_audience?: string[];
};

export type Member = {
  id?: string;
  email: string;
  admin: boolean;
  seated?: boolean;
  workspace_id?: string;
};

export type ConversationAgent = { id: string; name: string };

/** The liveest turn a conversation holds, `idle` where it holds none. */
export type ConversationTurn = "running" | "queued" | "parked" | "idle";

/** One conversation as the `conversation` kind lists and resolves it: the rail's row, the Home
 *  table's, the open chat's and a permalink's are one record, `conversation_id` the kind's `name`.
 *  `readable` is whether its content reads now, `disclosable` whether an admin may open it by
 *  acknowledging, and `speakable` whether the member's messages land in it. */
export type Conversation = {
  conversation_id: string;
  agent_id: string;
  agent_name: string;
  title: string;
  opening: string | null;
  last_at: string;
  surface: string;
  surface_label: string | null;
  audience: string;
  member_email: string | null;
  owner_email: string | null;
  owner_name: string | null;
  mine: boolean;
  speaker: string | null;
  source: string | null;
  turn: ConversationTurn;
  automation_kind: string | null;
  automation_name: string | null;
  automation_title: string | null;
  unread: boolean;
  speakers: string[];
  turn_count: number;
  created_at: string;
  last_turn_at: string | null;
  archived: boolean;
  deleted: boolean;
  pinned: boolean;
  readable: boolean;
  disclosable: boolean;
  speakable: boolean;
};

export type ArchivedApp = {
  id: string;
  name: string;
  object: string;
  icon: string;
  purpose?: string | null;
  app?: string | null;
  hidden?: boolean;
  archived_at: string;
};

/** A screen withheld here keeps its address: the tab is undrawn, and a member holding the link still
 *  lands on it. */
export type Surfaces = {
  team: boolean;
  apps: boolean;
  memory: boolean;
  radar: boolean;
  "community-skills": boolean;
  "installed-skills": boolean;
  "app-store": boolean;
};

export type AgentsPayload = {
  agents: Agent[];
  archived: ArchivedApp[];
  member: Member;
  models: string[];
  surfaces: Surfaces;
};

/** It carries its origin, like `url`: an app page draws this chat framed on its own origin, where a
 *  picture named without one resolves against the site rather than the route serving it. */
export type ChatFile = {
  id?: string | null;
  filename: string;
  url: string | null;
  size_bytes?: number;
  preview_url: string | null;
  media_type: string;
  role?: "file" | "details";
  subject?: string | null;
};

export type ChatApp = {
  id: string;
  name: string;
  model: string;
  icon: string;
};

export type ChatQuestion = {
  turn_id: string;
  title?: string;
  icon?: string | null;
  questions: AskQuestion[];
  answered?: Record<number, string>;
  closed?: boolean;
};

export type CredentialPrompt = { slot: string; prompt: string; stored?: boolean };

export type CredentialRequest = {
  sealed: string;
  reason: string;
  prompts: CredentialPrompt[];
};

/** What a settled turn spent and how long it took, carried on the reply it produced. The transcript
 *  draws it from here, so the line stands after a reload and for a member who watched no stream. */
export type TurnSummary = {
  /** The model the turn ran on, held as the id the meta line draws its mark from. An agent on the
   *  auto sentinel carries none. */
  model?: string;
  tokens?: number;
  cost_micro_usd?: number;
  duration_ms?: number;
};

export type HiddenElement = "agent_detail";

/** `arrival_id` names the inbound-queue row a message admitted mid-turn landed on, carried only while
 *  that turn has not taken the message up. `at` is when the message landed: the member's words as
 *  their turn was admitted, the reply as the turn that wrote it settled. */
export type Message = {
  role: string;
  text: string;
  /** The member spoke these words over a surface that drew their emphasis as markup, so the text is
   *  markdown rather than the characters they typed. */
  markdown?: boolean;
  at?: string;
  speaker?: string;
  /** An object's fire admitted the turn these words founded — a source trigger's wake — so no
   *  member spoke them: the header says ufo sent them, and the provider's mark heads the words. */
  fired?: { provider: string | null };
  asked?: string;
  /** The elements the server folded off a member's words before they reached this page: an
   *  extension's alert states one line for the member and keeps the rest for the agent. */
  hidden?: HiddenElement[];
  arrival_id?: string;
  /** The turn this message belongs to — the run a member presses in a listing of runs: the turn
   *  that wrote a reply, and the turn the words that woke it founded. */
  turn?: string;
  events?: ActivityEvent[];
  subagents?: SubagentRun[];
  question?: ChatQuestion;
  files?: ChatFile[];
  apps?: ChatApp[];
  summary?: TurnSummary;
};

export type ChatConnect = {
  turn?: string;
  provider?: string;
  label?: string;
  account?: string;
};

export type Transcript = {
  messages: Message[];
  /** The compacted-away page standing directly above `messages`; each page's response names the bounded
   *  page above it in turn. */
  earlier_cursor?: string;
  turn?: string;
  /** When the running turn was admitted, so a page that loads into it counts the clock from the turn's
   *  own start rather than from the load. */
  credentials?: CredentialRequest | null;
};

export type SchemaProperty = {
  type?: string;
  title?: string;
  format?: string;
  maxLength?: number;
  examples?: string[];
  enum?: string[];
  default?: unknown;
  anyOf?: { type?: string; format?: string; maxLength?: number; enum?: string[] }[];
};

export type SpecSchema = {
  properties?: Record<string, SchemaProperty>;
  required?: string[];
};

/** A control never reads it — the body it submits is the action's own input — and echoes it whole to the lane. */
export type ActionCall = {
  kind: string;
  action: string;
  name?: string;
  agent?: string;
  generation?: string;
  input: Record<string, unknown>;
};

export type ActionView = {
  name: string;
  description: string;
  input_schema: SpecSchema;
  call: ActionCall;
  label: string;
  confirm?: string;
};

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export type ActionInput = Record<string, JsonValue>;
