/** An app's homepage: the frame link of the page it stands on, or the answer that it has none. It
 *  rides the agent object so opening an app paints from the boot read without another request.
 *  The granular poll begins after its first interval and keeps it live thereafter.
 *  `deploy_generation` changes with the page's deployment, so a fresh answer remounts it. */
export type Homepage =
  | { state: "set"; url: string; deploy_generation?: number }
  | { state: "none" };

/** An agent as the boot read names it — the set a member may open and message. */
export type Agent = {
  id: string;
  name: string;
  model: string;
  main: boolean;
  /** The homepage the agent stands on, resolved once at boot and carried here. The boot read always
   *  sends it; a payload without it (or an agent built without one) reads as having none. */
  homepage?: Homepage;
  /** The mark this app is drawn with. `AGENT_ICONS` is the ordered set the picker offers and the
   *  bundle carries; any other name a tabler outline mark answers still draws. */
  icon: string;
  /** One sentence saying what this app is for. Every shipped app states one; an app a member built
   *  before it could states none, and every screen that reads it draws nothing rather than a
   *  placeholder. */
  purpose?: string | null;
  /** The slug the `app_*` extension shipped this agent under (`app_radar` → `"radar"`), or
   *  absent for every other agent. The slug is the app's identity — section addresses, the chat
   *  surface, the default pins — where the name is a member-visible string provisioning may
   *  suffix on collision. */
  app?: string | null;
  /** Whether the signed-in member created this app — the fact the apps listing narrows on. */
  mine?: boolean;
  /** Whether the deploy withholds this app from every list the portal draws. The workspace holds
   *  it either way, and a member arriving on its address still opens it — hidden is what the
   *  portal shows, never what the workspace has. */
  hidden?: boolean;
  /** Whether this app is installed and cannot work until the member acts — an account ungranted, a
   *  credential unfilled, a standing order unarmed. Read once at boot: what it answers changes only
   *  when the member settles one of those, and the setup screen re-reads for itself as they do. An
   *  agent nobody provisioned declares nothing and so owes nothing. */
  setup_due?: boolean;
  /** Whether this app stands on its setup screen rather than on a page: it declares something that
   *  screen lists, and this workspace has never built it a page. It rides the boot read because the
   *  shell draws from that read — the setup screen draws no navigation, and a fact learned a
   *  request later had the sidebar drawn and then taken away again. */
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

/** The agent a conversation ran under, carried by a read that spans every agent the member
 *  reaches and null by one taken inside a single agent's namespace. */
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

/** A conversation a read spanning every agent answers with: the row names the agent that ran it,
 *  since the pane it lands in names none. */
export type OwnedConversation = Conversation & { agent: ConversationAgent };

export type ArchivedApp = {
  id: string;
  name: string;
  /** The durable name the archived agent object answers to — what a restore targets. */
  object: string;
  icon: string;
  archived_at: string;
};

/** Which of the portal's own screens this member is offered. Most are a feature flag the boot read
 *  answers open, so a portal that reached no flag backend keeps drawing what a member already had;
 *  `team` is answered by their admin standing instead, because the roster is an admin's screen. A
 *  screen withheld here keeps its address: the tab is undrawn, and a member holding the link still
 *  lands on it. */
export type Surfaces = {
  team: boolean;
  memory: boolean;
  "community-skills": boolean;
  "installed-skills": boolean;
};

export type AgentsPayload = {
  agents: Agent[];
  archived: ArchivedApp[];
  member: Member;
  surfaces: Surfaces;
};

/** A file a message carries — one a turn shared, or one the member attached to their own words.
 *  `preview_url` links a picture of it: the file itself when it is an image, or the first page a
 *  document was rendered to. It carries its origin, like `url` — an app page draws this chat framed
 *  on its own origin, where a picture named without one resolves against the site rather than the
 *  route serving it. `media_type` says which cards the artifacts sidebar can draw as a document, so
 *  pressing one opens it there instead of downloading, and which picture wears a badge naming the
 *  document it came from. A file the member attached lives in the conversation's
 *  workspace rather than the artifact store, so it carries neither a download `url` nor a size. */
export type ChatFile = {
  filename: string;
  url: string | null;
  size_bytes?: number;
  preview_url: string | null;
  media_type: string;
};

/** One application a reply's turn created, as the card that opens it draws it: the app's own mark
 *  and name, the model it runs on, and the id the portal opens it at. */
export type ChatApp = {
  id: string;
  name: string;
  model: string;
  icon: string;
};

export type QuestionOption = { label: string; description?: string };

/** One thing a turn asks. `chosen` is the answer already settled — the option the form opens
 *  selected, or, where no option carries it, the words the row the member types into opens with. */
export type QuestionEntry = {
  question: string;
  header?: string;
  options?: QuestionOption[];
  multi_select?: boolean;
  free_text_only?: boolean;
  allow_attachments?: boolean;
  chosen?: string;
};

/** What a turn asks of the member, named by the turn that asked and marked with `icon`, any tabler
 *  icon name. `answered` holds what the surface confirmed it admitted for each entry the member
 *  has submitted — the words the transcript will read back, so the entry states its answer rather
 *  than vanishing. `closed` says the run is over: a later turn superseded the ask, so the card is
 *  the record of what the member answered and offers nothing to answer. */
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

/** One thing an agent did, as a conversation states it: a tool-run summary or a line it wrote. */
export type ActivityEvent =
  | { kind: "activity"; text: string }
  | { kind: "note"; text: string };

/** One subagent run beneath the reply that spawned it: the work it did, what it answered, and the
 *  runs it spawned in turn. `conversation_id` opens the whole record; `name` is the display name
 *  its spawn gave it, and the row states the profile when there is none. `turn_id`,
 *  `parent_turn_id`, `running`, and `current` exist only on a row the live stream is building —
 *  the durable transcript states finished runs and carries none of them. */
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
  /** How many of the spawning turn's events had landed when this row appeared — where the row
   *  interleaves into the activity list. Live-only; a durable run renders after the events. */
  at?: number;
};

/** One message a conversation states. `arrival_id` names the inbound-queue row a message admitted
 *  while a turn ran landed on, and it is carried only while that turn has not taken the message up
 *  — the turn's `absorbed` event names the same id when it does. `speaker` is the display line the
 *  admitting surface reported for a member other than the viewer, so a bubble in a conversation
 *  more members are in names who said it. */
export type Message = {
  role: string;
  text: string;
  speaker?: string;
  /** The question these words answered, drawn over them so the answer reads with what it
   *  answered. */
  asked?: string;
  arrival_id?: string;
  events?: ActivityEvent[];
  subagents?: SubagentRun[];
  /** What this reply asked the member, carried by the reply that asked it. */
  question?: ChatQuestion;
  /** What this reply's turn shared, or what the member attached to their own words: carried by the
   *  message so the files stay where the words that carried them are. */
  files?: ChatFile[];
  /** The applications this reply's turn created, carried by the reply that made them. */
  apps?: ChatApp[];
};

/** The private connect act a reply left the member: `turn` while the request stands — the press
 *  opens that turn's handoff, which is where the consent URL is minted — and `account` once the
 *  connect landed and the control is the record of it rather than an act to press. */
export type ChatConnect = {
  turn?: string;
  provider?: string;
  label?: string;
  account?: string;
};

export type Transcript = {
  messages: Message[];
  /** The compacted-away page standing directly above `messages`; each page's response names the
   *  bounded page above it in turn. Absent when the transcript reflects no compaction. */
  earlier_cursor?: string;
  turn?: string;
  credentials?: CredentialRequest | null;
};

export type SchemaProperty = {
  type?: string;
  title?: string;
  format?: string;
  maxLength?: number;
  examples?: string[];
  enum?: string[];
  anyOf?: { type?: string; format?: string; maxLength?: number; enum?: string[] }[];
};

export type SpecSchema = {
  properties?: Record<string, SchemaProperty>;
  required?: string[];
};

/** The invocation template an action view carries, pre-bound to the object it was read off: the
 *  kind and action, and the instance name and generation the read established. A control never
 *  reads it — the body it submits is the action's own input — and echoes it whole to the lane. */
export type ActionCall = {
  kind: string;
  action: string;
  name?: string;
  agent?: string;
  generation?: string;
  input: Record<string, unknown>;
};

/** One action as a portal read projects it beside its rows: the JSON Schema of the body it takes,
 *  the call it addresses, and the words the member reads. */
export type ActionView = {
  name: string;
  description: string;
  input_schema: SpecSchema;
  call: ActionCall;
  label: string;
  confirm?: string;
};

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

/** An action's input as it submits: each field in its wire type, an optional field left empty
 *  omitted so the input model applies its own default. */
export type ActionInput = Record<string, JsonValue>;
