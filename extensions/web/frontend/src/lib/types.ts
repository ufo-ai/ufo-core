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

export type Conversation = {
  id: string;
  agent: ConversationAgent | null;
  surface: string;
  surface_label: string | null;
  audience: string;
  member_email: string | null;
  description: string;
  source: string | null;
  speakers: string[];
  turn_count: number;
  created_at: string;
  last_turn_at: string | null;
  readable: boolean;
  disclosable: boolean;
  commentable: boolean;
};

export type OwnedConversation = Conversation & { agent: ConversationAgent };

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
  memory: boolean;
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

export type QuestionOption = { label: string; description?: string };

export type QuestionEntry = {
  question: string;
  header?: string;
  options?: QuestionOption[];
  multi_select?: boolean;
  free_text_only?: boolean;
  allow_attachments?: boolean;
  chosen?: string;
};

export type ChatQuestion = {
  turn_id: string;
  title?: string;
  icon?: string;
  questions: QuestionEntry[];
  answered?: Record<number, string>;
  closed?: boolean;
};

export type CredentialPrompt = { slot: string; prompt: string; stored?: boolean };

export type CredentialRequest = {
  sealed: string;
  reason: string;
  prompts: CredentialPrompt[];
};

export type ActivityEvent =
  | { kind: "activity"; text: string }
  | { kind: "note"; text: string };

export type SubagentRun = {
  profile: string;
  name?: string;
  conversation_id: string;
  events: ActivityEvent[];
  output: string;
  subagents: SubagentRun[];
  turn_id?: string;
  parent_turn_id?: string;
  running?: boolean;
  current?: string;
  at?: number;
};

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
  asked?: string;
  arrival_id?: string;
  events?: ActivityEvent[];
  subagents?: SubagentRun[];
  question?: ChatQuestion;
  files?: ChatFile[];
  apps?: ChatApp[];
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
  turn_started_at?: string;
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
