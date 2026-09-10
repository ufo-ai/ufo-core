// The radar app's page: a static site built with the portal's app kit. Edit this file and redeploy
// to change the page.

import {
  ARTIFACT_TEXT_BYTES,
  AgentIcon,
  COLUMN,
  FileSheet,
  Header,
  Markdown,
  MediaIcon,
  Moment,
  ObjectDetail,
  Panel,
  PanelBlank,
  PanelEmpty,
  Section,
  SectionApp,
  SurfaceGlyph,
  Sheet,
  Loading,
  agentHash,
  agentName,
  appended,
  beside,
  buttonVariants,
  chatHash,
  closed,
  cn,
  formatSize,
  mountApp,
  objectAt,
  opened,
  sectionHash,
  slackLink,
  slotOf,
  surfaceWord,
  useAgents,
  useCallback,
  useLayoutEffect,
  usePageHead,
  usePanelRead,
  useState,
  useTextArtifact,
} from "ufo/kit";
import type { ObjectAddress, Placement, ReactMouseEvent, ReactNode } from "ufo/kit";
// The tour's words, committed beside this file and taken into the bundle at build time. `?raw` is
// vite's own: the same import resolves in the deploy's app build and in the build a member's
// redeploy runs, so the document ships with the page rather than being fetched at runtime.
import TOUR_DOCUMENT from "./tour.md?raw";

const TASK_KIND = "scheduled_task";
const RUN_PREFIX = "run/";

/** Whether a slot in the place names a run of this page's own, which the drawer reads as a story
 *  rather than as a record held at an object address. */
function isRun(id: string): boolean {
  return id.startsWith(RUN_PREFIX) && id !== RUN_PREFIX;
}

/** What the drawer is titled until the report standing in it has named itself. The read may answer
 *  no run at all, and a drawer the member can shut cannot wait on a name that is never coming. */
const REPORT = "Report";

type RadarArtifact = {
  filename: string;
  subject: string | null;
  media_type: string;
  size_bytes: number;
  url: string | null;
  preview_url: string | null;
};

type RadarRun = {
  turn_id: string;
  conversation_id: string;
  agent_id: string;
  fired_at: string;
  status: string;
  task: string | null;
  surface: string;
  source: string | null;
  text: string;
  entry: DigestWritten | null;
  artifacts: RadarArtifact[];
};

/** One finding as the digest writer states it, and — where the report says who did the thing —
 *  the person who did it. */
type DigestPoint = { text: string; actor: string };

/** What the report-digest skill wrote about one report. A report published since the job last ran
 *  carries none yet, and stands on its task's name until it does. */
type DigestWritten = { title: string; summary: string; points: DigestPoint[] };

type ReportRow = {
  name: string;
  agent_id: string;
  conversation: string;
  fired_at: string;
  status: string;
  task: string | null;
  surface: string;
  source: string | null;
  text: string;
  entry: DigestWritten | null;
  artifacts: RadarArtifact[];
};

type ReportsPayload = { objects: ReportRow[]; next_cursor: string | null };

/** One report as the detail route answers it: the listing row's fields ride in `status`, beside
 *  the name the permalink carries. The detail resolves the turn directly — a report older than
 *  the feed's own window still answers its permalink — and its row names the firing agent. */
type ReportDetail = { name: string; status: Omit<ReportRow, "name"> };

/** One listing row as the feed reads it: the report kind's own fields, folded back into the run
 *  the stories are written over — the digest rides whole in the row's `entry` field, because a
 *  flat `summary` would collide with the listing row's own. */
function toRun(row: ReportRow): RadarRun {
  return {
    turn_id: row.name,
    conversation_id: row.conversation,
    agent_id: row.agent_id,
    fired_at: row.fired_at,
    status: row.status,
    task: row.task,
    surface: row.surface,
    source: row.source,
    text: row.text,
    entry: row.entry,
    artifacts: row.artifacts,
  };
}

/** A run that ended well needs no mark beside its own reply; the other endings are stated. */
const STATUS_NOTES: Record<string, string> = {
  failed: "Failed",
  cancelled: "Stopped",
};

/** The tour: the oldest entry on the rail, in every workspace. The feed runs newest first, so the
 *  foot of the list is where the thing that came before all the work belongs — a new team reads it
 *  because it is the only entry there, and a team already running reports keeps every report above
 *  it untouched.
 *
 *  Its words are `tour.md` beside this file and nothing else — the summary and the points its
 *  frontmatter states, the body under it. The document is committed, so the words are edited and
 *  reviewed as prose and read from one place by anything else that wants them, and the build takes
 *  it into the bundle the deploy serves: no per-workspace copy to migrate, no skill to load, and no
 *  row to rewrite.
 *
 *  It is drawn and never stored: a report exists by a scheduled task running, so a canned row in
 *  that table would be a run that never ran, and every reader of the report kind would have to know
 *  to skip it. */
const TOUR_SLOT = "tour";

/** The tour as its document states it: a digest like every other entry on the rail, plus the line
 *  above the title and the body the drawer reads. */
type TourWritten = DigestWritten & { lead: string; body: string };

const TOUR_FRONTMATTER = /^---\n([\s\S]*?)\n---\n([\s\S]*)$/;
const TOUR_POINT = /^ *- text: "(.*)"\n *actor: "(.*)"$/gm;

/** A value as the frontmatter writes it: quoted, because every scalar there is quoted, and a
 *  sentence in this document carries a colon. */
function tourValue(front: string, key: string): string {
  const found = new RegExp(`^${key}: "(.*)"$`, "m").exec(front);
  if (found === null) throw new Error(`tour.md states no ${key}`);
  return found[1];
}

/** The document read: its frontmatter as the fields the entry draws, its body as the drawer's
 *  prose. The frontmatter this reads is the one shape `tour.md` is written in — quoted scalars and
 *  a `points` list of `text`/`actor` pairs — because a page resolves no import but `ufo/kit`, so a
 *  YAML library is not one of its choices. The app's own tests read the same document with a YAML
 *  parser, which is what holds the file to that shape. */
function tourWritten(raw: string): TourWritten {
  const split = TOUR_FRONTMATTER.exec(raw);
  if (split === null) throw new Error("tour.md must open with a YAML frontmatter block");
  const [, front, body] = split;
  return {
    lead: tourValue(front, "lead"),
    title: tourValue(front, "title"),
    summary: tourValue(front, "summary"),
    points: [...front.matchAll(TOUR_POINT)].map((point) => ({
      text: point[1],
      actor: point[2],
    })),
    body,
  };
}

const TOUR = tourWritten(TOUR_DOCUMENT);

/** Which picture stands for a run. Only a file with a rendered preview can stand for one at all —
 *  the row shows a picture or nothing, never a frame around a name. Among those, a picture the run
 *  drew says at a glance what the report is about, while the first page of a document says only
 *  that a document exists, because every page of prose crops to the same grey band. */
function cover(artifacts: RadarArtifact[]): RadarArtifact | null {
  const pictures = artifacts.filter((artifact) => artifact.preview_url !== null);
  return (
    pictures.find((artifact) => artifact.media_type.startsWith("image/")) ?? pictures[0] ?? null
  );
}

const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

/** The calendar day a stamp names, read off the ISO string as it was sent rather than through
 *  `Date`, so no reader's zone shifts a report across midnight. */
function dateKey(iso: string): string {
  return iso.slice(0, 10);
}

/** The day heading: the month written out, because the heading is read as words rather than
 *  scanned as a column of figures. */
function dayName(key: string): string {
  const [year, month, date] = key.split("-");
  return MONTHS[Number(month) - 1] + " " + Number(date) + ", " + year;
}

/** The clock time a report fired, on the twelve-hour clock the heading's date is written for. */
function clock(iso: string): string {
  const hour = Number(iso.slice(11, 13));
  return ((hour % 12) || 12) + ":" + iso.slice(14, 16) + (hour < 12 ? " AM" : " PM");
}

/** One fact of the meta line, read as its mark and its words. The mark names the kind of fact —
 *  whose agent, which service, what file — so the line reads without labels. */
function Fact({ mark, children }: { mark: ReactNode; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-2xs">
      {mark}
      {children}
    </span>
  );
}

function Dot() {
  return (
    <span aria-hidden className="text-ink-faint">
      ·
    </span>
  );
}

/** The reports the reader may read, gathered under the day they fired. A day heading names the
 *  date once, so the rows under it state only their time; today is named as such and its rows
 *  count back from now, because a report filed this morning is read as recency rather than as a
 *  clock face. Today runs newest first — the reader came for what just happened — and every day
 *  behind it runs in the order it was lived.
 *
 *  The tour closes the rail rather than opening it, and closes it once: it stands on the page that
 *  has no older reports behind it, which is the first page of a workspace that has never run and
 *  the last page of one that walks its whole history. */
function Feed({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const opens = place.opens ?? [];
  const after = place.after ?? "";
  const params = new URLSearchParams({ order_by: "fired_at", order: "desc" });
  if (after) params.set("cursor", after);
  const state = usePanelRead<ReportsPayload>("/objects/report?" + params.toString());
  const now = dateKey(new Date().toISOString());
  return (
    <Panel state={state} shape="cards">
      {(payload) => {
        const runs = payload.objects.map(toRun);
        const days = [...new Set(runs.map((run) => dateKey(run.fired_at)))];
        return (
          <>
            <div className="flex flex-col">
              {days.map((key) => {
                const since = key === now;
                const inDay = runs
                  .filter((run) => dateKey(run.fired_at) === key)
                  .sort((one, other) =>
                    since
                      ? other.fired_at.localeCompare(one.fired_at)
                      : one.fired_at.localeCompare(other.fired_at),
                  );
                return (
                  <section key={key} className="flex flex-col first:[&>h2]:pt-0">
                    <h2 className="m-0 py-2xl text-ui font-medium text-ink">
                      {since ? "Today" : dayName(key)}
                    </h2>
                    <ol className="m-0 flex list-none flex-col border-t border-edge p-0">
                      {inDay.map((run) => (
                        <RunRow
                          key={run.turn_id}
                          run={run}
                          since={since}
                          standing={opens.includes(RUN_PREFIX + run.turn_id)}
                        />
                      ))}
                    </ol>
                  </section>
                );
              })}
              {payload.next_cursor ? null : (
                <ol className="m-0 flex list-none flex-col border-t border-edge p-0">
                  <TourRow standing={opens.includes(TOUR_SLOT)} />
                </ol>
              )}
            </div>
            {payload.next_cursor || after ? (
              <div className="flex gap-sm">
                {payload.next_cursor ? (
                  <button
                    className={cn(buttonVariants({ variant: "row" }))}
                    onClick={() => onPlace({ after: payload.next_cursor ?? undefined })}
                  >
                    Older reports
                  </button>
                ) : null}
                {after ? (
                  <button
                    className={cn(buttonVariants({ variant: "row" }))}
                    onClick={() => onPlace({ after: undefined })}
                  >
                    Newest reports
                  </button>
                ) : null}
              </div>
            ) : null}
          </>
        );
      }}
    </Panel>
  );
}

/** One row of the rail: when it fired, what it found, and the facts that place it — which agent
 *  filed it, where it reported, which task fired it, and what it shared. Each fact is read by its
 *  mark rather than by a label. The whole row opens the report beside the rail. */
function RunRow({
  run,
  since,
  standing,
}: {
  run: RadarRun;
  since: boolean;
  standing: boolean;
}) {
  const agent = useAgents().find((entry) => entry.id === run.agent_id);
  const picture = cover(run.artifacts);
  const files = run.artifacts.length;
  return (
    <Row
      href={sectionHash("radar", { opens: [RUN_PREFIX + run.turn_id] })}
      standing={standing}
      when={since ? <Moment at={run.fired_at} /> : clock(run.fired_at)}
      title={run.entry?.title ?? run.task ?? "Scheduled run"}
      body={run.entry?.summary ?? run.text}
      picture={picture?.preview_url ?? null}
    >
      {agent ? (
        <Fact mark={<AgentIcon name={agent.icon} className="size-icon" />}>
          {agentName(agent.name)}
        </Fact>
      ) : null}
      {agent ? <Dot /> : null}
      <Fact mark={<SurfaceGlyph surface={run.surface} />}>{surfaceWord(run.surface)}</Fact>
      {run.task ? <Dot /> : null}
      {run.task ? <span>{run.task}</span> : null}
      {files ? <Dot /> : null}
      {files ? (
        <Fact mark={<MediaIcon mediaType={run.artifacts[0].media_type} />}>
          {files + (files === 1 ? " file" : " files")}
        </Fact>
      ) : null}
    </Row>
  );
}

/** The tour on the rail, shaped as a row so a member reads it the way they read every report above
 *  it. It opens the tour in the drawer beside the feed. */
function TourRow({ standing }: { standing: boolean }) {
  return (
    <Row
      href={sectionHash("radar", { opens: [TOUR_SLOT] })}
      standing={standing}
      when={null}
      title={TOUR.title}
      body={TOUR.summary}
      picture={null}
    >
      <Fact mark={<AgentIcon name="radar" className="size-icon" />}>{TOUR.lead}</Fact>
    </Row>
  );
}

/** The shape every row on the rail takes: the time it fired in its own column, then what it says,
 *  then its facts, and the picture it drew at the far edge. It is a link rather than a button, so
 *  the heading it carries is a heading and a member opens one beside the rail the same way they
 *  open anything else in the portal. */
function Row({
  href,
  standing,
  when,
  title,
  body,
  picture,
  children,
}: {
  href: string;
  standing: boolean;
  when: ReactNode;
  title: string;
  body: string | null;
  picture: string | null;
  children: ReactNode;
}) {
  return (
    <li className="border-b border-edge">
      <a
        href={href}
        aria-current={standing || undefined}
        className={cn(
          "flex items-start gap-2xl px-sm py-2xl text-inherit no-underline",
          "hover:bg-fill",
          standing && "bg-fill",
        )}
      >
        <span className="w-8xl shrink-0 pt-hair text-small tabular-nums text-ink-soft">{when}</span>
        <span className="flex min-w-0 flex-1 flex-col gap-2xs">
          <h3 className="m-0 text-ui font-medium text-ink">{title}</h3>
          {body ? <span className="line-clamp-3 max-w-hint text-ui text-ink-soft">{body}</span> : null}
          <span className="flex flex-wrap items-center gap-x-sm gap-y-2xs pt-xs text-small text-ink-soft">
            {children}
          </span>
        </span>
        {picture ? (
          <img
            loading="lazy"
            alt=""
            src={picture}
            className="h-(--size-digest-picture) w-(--size-thumbnail) shrink-0 rounded-panel border border-edge object-cover object-top-left"
          />
        ) : null}
      </a>
    </li>
  );
}

function Radar({
  title,
  place,
  onPlace,
}: {
  title: string;
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const [named, setNamed] = useState<{ run: string; name: string } | null>(null);
  const name = useCallback((run: string, name: string) => setNamed({ run, name }), []);
  const opens = place.opens ?? [];
  const band = usePageHead(<Header pinned heading={1} title={title} />);
  return (
    <>
      {band}
      <Section>
        <div className={COLUMN}>
          <Feed place={place} onPlace={onPlace} />
        </div>
      </Section>
      {opens.slice(-1).map((id) =>
        id === TOUR_SLOT ? (
          <TourSheet key={id} opens={opens} onPlace={onPlace} />
        ) : isRun(id) ? (
          <StorySheet
            key={id}
            id={id}
            title={named?.run === id ? named.name : REPORT}
            opens={opens}
            onPlace={onPlace}
            onName={name}
          />
        ) : (
          <RecordSlot
            key={id}
            id={id}
            held={objectAt(id)}
            opens={opens}
            onPlace={onPlace}
          />
        ),
      )}
    </>
  );
}

function RecordSlot({
  id,
  held,
  opens,
  onPlace,
}: {
  id: string;
  held: ObjectAddress | null;
  opens: string[];
  onPlace: (place: Placement) => void;
}) {
  const shut = () => onPlace({ opens: closed(opens, id) });
  if (held === null)
    return (
      <Sheet open title={id} onClose={shut}>
        <PanelEmpty>That item is not on this page.</PanelEmpty>
      </Sheet>
    );
  return (
    <ObjectDetail
      agentId={held.agent}
      kind={held.kind}
      name={held.name}
      onOpen={(next) => onPlace({ opens: opened(opens, slotOf(next), id) })}
      onBack={shut}
    />
  );
}

/** The tour opened: the shipped prose, and under it the screens it names — the same dateline of
 *  ways out a report's story stands on, so a member leaves the tour for the work rather than for
 *  the page they came from. */
function TourSheet({
  opens,
  onPlace,
}: {
  opens: string[];
  onPlace: (place: Placement) => void;
}) {
  const out = "text-inherit no-underline hover:underline focus-visible:underline";
  return (
    <Sheet open title={TOUR.title} onClose={() => onPlace({ opens: closed(opens, TOUR_SLOT) })}>
      <div className="flex flex-col gap-2xl">
        <div className="text-body leading-reading">
          <Markdown text={TOUR.body} />
        </div>
        <p className="m-0 flex flex-wrap gap-x-sm font-mono text-mono text-ink-soft">
          <a href={sectionHash("artifacts")} className={out}>
            Artifacts
          </a>
          <a href={sectionHash("connectors")} className={out}>
            Connectors
          </a>
        </p>
      </div>
    </Sheet>
  );
}

/** An opened report, read in the drawer at the pane's edge — the one every record on this page is
 *  opened in, so a report and an object are read and put away in the same place. The feed stands
 *  behind it: the reader keeps the list they opened the story out of, and shutting the drawer is
 *  the whole of the way back.
 *
 *  A file the story shared is read in this same drawer, in the story's place, and shutting it hands
 *  the story back: the drawer slot holds one thing, so a second sheet raised here would stand over
 *  the story and take its close control with it.
 *
 *  A pinned address that answers no run says so in the drawer rather than over the feed: the run is
 *  gone or was never this reader's to read. */
function StorySheet({
  id,
  title,
  opens,
  onPlace,
  onName,
}: {
  id: string;
  title: string;
  opens: string[];
  onPlace: (place: Placement) => void;
  onName: (run: string, name: string) => void;
}) {
  const agents = useAgents();
  const owner = agents.find((agent) => agent.app === "radar") ?? agents[0];
  const pinned = id.slice(RUN_PREFIX.length);
  const [file, setFile] = useState<RadarArtifact | null>(null);
  const detail = usePanelRead<ReportDetail>(
    owner ? "/objects/report/" + pinned + "?agent=" + owner.id : null,
  );
  if (file !== null) return <FileSheet file={file} onClose={() => setFile(null)} />;
  return (
    <Sheet open title={title} onClose={() => onPlace({ opens: closed(opens, id) })}>
      {detail.phase === "failed" && detail.status === 404 ? (
        <PanelBlank body="This report does not exist or is not shared with you." />
      ) : (
        <Panel state={detail} shape="cards">
          {(payload) => (
            <ol className="m-0 flex list-none flex-col p-0">
              <Story
                run={toRun({
                  ...payload.status,
                  name: payload.name,
                  agent_id: payload.status.agent_id || (owner?.id ?? ""),
                })}
                opens={opens}
                from={id}
                onPlace={onPlace}
                onName={onName}
                onFile={setFile}
              />
            </ol>
          )}
        </Panel>
      )}
    </Sheet>
  );
}

/** A markdown file the run shared is the run's own document: it reads inline as the story rather
 *  than standing as a chip the member must download to open. One whose link is not minted cannot
 *  be read here and stays a chip. */
function isDocument(artifact: RadarArtifact): boolean {
  return (
    artifact.url !== null &&
    (artifact.media_type === "text/markdown" || artifact.filename.endsWith(".md"))
  );
}

/** One run as a story: the report it published is the headline, so the story is titled the way the
 *  document titles itself. The drawer's own band states that same name on one line and cuts what
 *  will not fit, and a report titles itself in prose that often will not — so the story states it
 *  whole under that band, and the document's own title line is dropped from the body so the report
 *  never says it three times.
 *
 *  Under the heading stands the byline — the agent the task belongs to, reached at its page, and the
 *  task itself, pressed to open the record where the prompt and schedule are read — and under that
 *  the dateline of ways out: when it ran, the conversation it reported into, the thread on the
 *  surface it came from, and any outcome. The body is what the run made, never what it said: files
 *  with their pictures where one exists, markdown documents read inline. Only a run that did not end
 *  well speaks in text, because a failure explains itself; the reply a successful run posted lives
 *  in its conversation, one link away.
 *
 *  A run that published no document, or one whose document opens on no title, is headed by the task
 *  that fired it — every story states what it is before it states what it did. */
function Story({
  run,
  opens,
  from,
  onPlace,
  onName,
  onFile,
}: {
  run: RadarRun;
  opens: string[];
  from?: string;
  onPlace: (place: Placement) => void;
  onName: (run: string, name: string) => void;
  onFile: (artifact: RadarArtifact) => void;
}) {
  const [title, setTitle] = useState<string | null>(null);
  const agent = useAgents().find((entry) => entry.id === run.agent_id);
  const note = STATUS_NOTES[run.status];
  const thread = slackLink(run.surface, run.source);
  const documents = run.artifacts.filter(isDocument);
  const files = run.artifacts.filter((artifact) => !isDocument(artifact));
  const heading = title ?? run.task ?? "Scheduled run";
  const out =
    "text-inherit no-underline hover:underline focus-visible:underline";
  useLayoutEffect(() => {
    onName(RUN_PREFIX + run.turn_id, heading);
  }, [onName, run.turn_id, heading]);
  return (
    <li className="flex flex-col gap-sm border-b border-edge pb-4xl last:border-b-0">
      <h3 className="m-0 text-title font-medium">{heading}</h3>
      {agent || run.task ? (
        <p className="m-0 text-ui text-ink-soft">
          by{" "}
          {agent ? (
            <>
              <a href={agentHash(agent.id)} className={out}>
                {agent.name}
              </a>{" "}
            </>
          ) : null}
          {run.task ? (
            <TaskName
              task={run.task}
              agentId={run.agent_id}
              opens={opens}
              from={from}
              onPlace={onPlace}
              className="m-0 border-0 p-0 text-left text-inherit hover:underline"
            />
          ) : null}
        </p>
      ) : null}
      <p className="m-0 flex flex-wrap gap-x-sm font-mono text-mono text-ink-soft">
        <a href={sectionHash("radar", { opens: [RUN_PREFIX + run.turn_id] })} className={out}>
          <Moment at={run.fired_at} />
        </a>
        <a href={chatHash(run.conversation_id)} className={out}>
          Conversation
        </a>
        {thread ? (
          <a href={thread} target="_blank" rel="noopener noreferrer" className={out}>
            Slack <span aria-hidden>↗</span>
          </a>
        ) : null}
        {note ? (
          <span
            className={
              run.status === "failed" ? "[color:var(--color-attention-ink)]" : undefined
            }
          >
            {note}
          </span>
        ) : null}
      </p>
      {files.length ? (
        <ul className="m-0 flex list-none flex-wrap gap-2xl p-0">
          {files.map((artifact) => (
            <li key={artifact.filename}>
              <Shared artifact={artifact} onOpen={() => onFile(artifact)} />
            </li>
          ))}
        </ul>
      ) : null}
      {run.status !== "done" && run.text ? (
        <div className="text-body leading-reading">
          <Markdown text={run.text} />
        </div>
      ) : null}
      {documents.map((artifact, at) => (
        <Report
          key={artifact.filename}
          artifact={artifact}
          heading={heading}
          onTitle={at === 0 ? setTitle : undefined}
        />
      ))}
    </li>
  );
}

function TaskName({
  task,
  agentId,
  opens,
  from,
  onPlace,
  className,
}: {
  task: string;
  agentId: string;
  opens: string[];
  from?: string;
  onPlace: (place: Placement) => void;
  className: string;
}) {
  const id = slotOf({ agent: agentId, kind: TASK_KIND, name: task });
  const standing = opens.includes(id);
  const press = (event: ReactMouseEvent<HTMLButtonElement>) =>
    onPlace({ opens: beside(event) ? appended(opens, id) : opened(opens, id, from) });
  return (
    <button
      type="button"
      aria-current={standing}
      onClick={press}
      onAuxClick={(event) => {
        if (beside(event)) press(event);
      }}
      className={cn(className, standing ? "-mx-xs rounded-control bg-fill px-xs" : "bg-transparent")}
    >
      {task}
    </button>
  );
}

/** A document's own title and the body under it: a report opens with the one `# Title` line that
 *  names it, which the story states as its heading instead. */
function titled(text: string): { title: string | null; body: string } {
  const opening = /^\s*#[ \t]+(\S.*?)[ \t]*(?:\n|$)/.exec(text);
  if (!opening) return { title: null, body: text };
  return { title: opening[1], body: text.slice(opening[0].length) };
}

/** The report the run published, read as the story's own body: a member standing on one report's own
 *  address came for that report, so the document flows whole down the page with nothing to press —
 *  neither a fold to open nor a box that scrolls inside a page that scrolls, which would trap the
 *  wheel over the very thing the member came to read. The title line is dropped where the story
 *  already stands under it, and a second document — which titles nothing above it — keeps its own.
 *  The story is told the title before the frame is painted, so no reader ever catches a report
 *  saying its own name twice. */
function Report({
  artifact,
  heading,
  onTitle,
}: {
  artifact: RadarArtifact;
  heading: string;
  onTitle?: (title: string) => void;
}) {
  const { body, bounded, message } = useTextArtifact(artifact.url);
  const read = titled(body ?? "");

  useLayoutEffect(() => {
    if (read.title !== null) onTitle?.(read.title);
  }, [onTitle, read.title]);

  if (message) return <div className="font-mono text-small text-ink-soft">{message}</div>;
  if (body === null)
    return (
      <div>
        <Loading />
      </div>
    );
  return (
    <>
      <div className="text-body leading-reading">
        <Markdown text={read.title === heading ? read.body : body} />
      </div>
      {bounded ? (
        <div className="font-mono text-small text-ink-soft">
          First {formatSize(ARTIFACT_TEXT_BYTES)} shown.
        </div>
      ) : null}
    </>
  );
}

/** A file the run shared: its picture where one exists, else its name and size — pressing either
 *  reads the file full in the drawer the story stands in, with its download, and a file whose link
 *  is not minted is named without one. */
function Shared({ artifact, onOpen }: { artifact: RadarArtifact; onOpen: () => void }) {
  const card = artifact.preview_url ? (
    <img
      loading="lazy"
      alt={artifact.subject || artifact.filename}
      src={artifact.preview_url}
      className="size-(--size-thumbnail) rounded-panel border border-edge object-cover object-top-left"
    />
  ) : (
    <span className="flex items-baseline gap-sm rounded-panel border border-edge px-lg py-sm">
      <span className="font-mono text-small text-ink">{artifact.filename}</span>
      <span className="font-mono text-mono text-ink-soft">{formatSize(artifact.size_bytes)}</span>
    </span>
  );
  if (!artifact.url) return card;
  return (
    <button
      type="button"
      onClick={onOpen}
      className="block cursor-pointer border-0 bg-transparent p-0 text-left hover:opacity-muted-soft"
    >
      {card}
    </button>
  );
}

mountApp(document.getElementById("root")!, (init) => (
  <SectionApp
    tab="radar"
    init={init}
    view={{
      label: "Radar",
      remountOnPlace: false,
      ownsHeader: true,
      render: (place, onPlace) => (
        <Radar title="Radar" place={place} onPlace={onPlace} />
      ),
    }}
  />
));
